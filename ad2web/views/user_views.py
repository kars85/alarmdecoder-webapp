from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, abort
from flask_login import login_required, current_user

from .forms import UserForm
from .service import UserService, DuplicateUserError
from ..user import User  # user model (for current_user and role checks)
from ..decorators import admin_required

users_bp = Blueprint('users', __name__, url_prefix='/settings/users')

@users_bp.route('/')
@login_required
@admin_required
def index():
    """List all users (admin view)."""
    users = UserService.list_users()
    return render_template('admin/users.html', users=users, active='users')

@users_bp.route('/failed_logins')
@login_required
@admin_required
def failed_logins():
    """Show all failed login attempts."""
    failed_logins = UserService.list_failed_logins()
    return render_template('admin/failed_logins.html', failed_logins=failed_logins, active='users')

@users_bp.route('/create', methods=['GET', 'POST'])
@login_required
@admin_required
def create():
    """Create a new user account."""
    form = UserForm()
    if form.validate_on_submit():
        try:
            UserService.create_user(
                name=form.name.data,
                email=form.email.data,
                password=form.password.data,
                role_code=form.role_code.data,
                status_code=form.status_code.data
            )
            flash('User created.', 'success')
            return redirect(url_for('users.index'))
        except DuplicateUserError:
            flash('Duplicate user data, please use unique username and email.', 'error')
            return redirect(url_for('users.index'))
    return render_template('admin/user_form.html', form=form, user_id=None, active='users')

@users_bp.route('/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def edit(user_id):
    """Edit an existing user account."""
    user = UserService.get_user(user_id)
    if not user:
        abort(404)
    form = UserForm(obj=user)
    # Ensure password fields are blank on edit (require admin to re-enter password)
    form.password.data = ''
    form.password_again.data = ''
    if form.validate_on_submit():
        try:
            UserService.update_user(
                user_id=user_id,
                name=form.name.data,
                email=form.email.data,
                password=form.password.data,
                role_code=form.role_code.data,
                status_code=form.status_code.data
            )
            flash('User updated.', 'success')
            return redirect(url_for('users.index'))
        except DuplicateUserError:
            flash('Duplicate user data, please use unique username and email.', 'error')
            return redirect(url_for('users.index'))
    return render_template('admin/user_form.html', form=form, user_id=user_id, active='users')

@users_bp.route('/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def delete(user_id):
    """Delete a user account."""
    if not UserService.delete_user(user_id):
        # Either user not found or deletion not allowed (e.g., user_id == 1)
        flash('User could not be deleted.', 'error')
        return jsonify(success=False)
    flash('User deleted.', 'success')
    return jsonify(success=True)

@users_bp.route('/<int:user_id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_status(user_id):
    """Toggle the active status of a user (AJAX endpoint)."""
    user = UserService.toggle_user_status(user_id)
    if not user:
        return jsonify(success=False), 404
    # Return the new status text and code
    return jsonify(success=True, new_status=user.status, new_status_code=user.status_code)
