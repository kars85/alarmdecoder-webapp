from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, abort
from flask_login import login_required

from ad2web.decorators import admin_required
from ad2web.services.admin_service import AdminService, DuplicateUserError
from ad2web.forms.admin_form import UserForm

admin = Blueprint('admin', __name__, url_prefix='/settings/admin')

@admin.route('/users')
@login_required
@admin_required
def users():
    """Admin page: list all users."""
    users = AdminService.list_users()
    return render_template('admin/users.html', users=users, active='users')

@admin.route('/failed_logins')
@login_required
@admin_required
def failed_logins():
    """Admin page: list failed login attempts."""
    records = AdminService.list_failed_logins()
    return render_template('admin/failed_logins.html', failed_logins=records, active='users')

@admin.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_user():
    """Admin page: create a new user."""
    form = UserForm()
    if form.validate_on_submit():
        try:
            AdminService.create_user(
                name=form.name.data,
                email=form.email.data,
                password=form.password.data,
                role_code=form.role_code.data,
                status_code=form.status_code.data
            )
            flash('User created.', 'success')
            return redirect(url_for('admin.users'))
        except DuplicateUserError as e:
            flash(str(e), 'error')
            # Redirect back to the users list (error message will be displayed)
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', form=form, user_id=None, active='users')

@admin.route('/users/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    """Admin page: edit an existing user."""
    user = AdminService.get_user(user_id)
    if not user:
        abort(404)
    # Pre-populate form with user data; mark edit mode so password is optional
    form = UserForm(obj=user, edit=True)
    # Preserve any next parameter (if provided, though not typically used in admin)
    if request.args.get('next'):
        form.next.data = request.args.get('next')
    if form.validate_on_submit():
        try:
            AdminService.update_user(
                user_id,
                name=form.name.data,
                email=form.email.data,
                password=form.password.data,
                role_code=form.role_code.data,
                status_code=form.status_code.data
            )
            flash('User updated.', 'success')
            return redirect(url_for('admin.users'))
        except DuplicateUserError as e:
            flash(str(e), 'error')
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', form=form, user_id=user_id, active='users')

@admin.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_user(user_id):
    """Toggle a user's active status (AJAX endpoint)."""
    user = AdminService.toggle_user_status(user_id)
    if not user:
        abort(404)
    # If called via AJAX, return JSON; otherwise redirect back with a message
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(success=True, status=user.status_code, status_label=user.status)
    flash(f"User status changed to '{user.status}'.", 'success')
    return redirect(url_for('admin.users'))

@admin.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    """Delete a user (AJAX or form submission)."""
    result = AdminService.delete_user(user_id)
    if not result:
        # Determine reason for failure (primary admin or not found)
        error_msg = "Cannot delete the primary admin user." if user_id == 1 else "User not found or deletion failed."
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify(success=False, message=error_msg)
        flash(error_msg, 'error')
        return redirect(url_for('admin.users'))
    # Deletion successful
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(success=True)
    flash('User deleted.', 'success')
    return redirect(url_for('admin.users'))
