# ad2web/user/views.py

from flask import Blueprint, render_template, request, redirect, url_for, abort, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..decorators import admin_required
from .models import User, UserHistory, FailedLogin, UserDetail
from .forms import ProfileForm  # (ProfileForm is moved from settings.forms to user.forms or imported appropriately)
from ..admin.forms import UserForm  # (Admin user form for create/edit users, moved from admin.forms to user or imported)
# Note: In refactoring, we might move Admin's UserForm into user/forms.py for coherence.

# 1. Blueprint for personal user operations (profile & history)
user_bp = Blueprint('user', __name__, url_prefix='/user')  # personal area (could also use '/settings/user')

@user_bp.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """View or edit the current user's profile."""
    user = User.query.get(current_user.id)
    if user is None:
        abort(404)
    form = ProfileForm(obj=user.user_detail or UserDetail(),  # populate form with existing details
                       email=user.email,
                       role_code=user.role_code,
                       status_code=user.status_code)
    if form.validate_on_submit():
        # Update user's basic fields
        user.email = form.email.data
        user.role_code = form.role_code.data
        user.status_code = form.status_code.data
        # Update or create UserDetail
        if not user.user_detail:
            user.user_detail = UserDetail()  # attach new detail record if not exist
        form.populate_obj(user.user_detail)  # fill in detail fields like age, bio, etc.
        # Handle avatar upload if provided
        if form.avatar_file.data:
            upload_file = request.files.get(form.avatar_file.name)
            if upload_file:
                # Save avatar with secure filename and update user.avatar
                filename = User.save_avatar(upload_file, user.id)  # assume we add a helper on User to handle saving
                user.avatar = filename
        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for('user.profile'))  # reload profile page
    return render_template('user/profile.html', user=user, form=form)

@user_bp.route('/history')
@login_required
def my_history():
    """View login history for current user."""
    user = User.query.get(current_user.id)
    if user is None:
        abort(404)
    history_records = UserHistory.query.filter_by(user_id=user.id).all()
    return render_template('user/history.html', user=user, history=history_records)

# 2. Blueprint for administrative user management
users_bp = Blueprint('users', __name__, url_prefix='/settings/users')  # admin section for users

@users_bp.route('/', methods=['GET'])
@login_required
@admin_required
def list_users():
    """Admin view: list all users."""
    users = User.query.all()
    return render_template('admin/users.html', users=users, active='users')

@users_bp.route('/failed_logins', methods=['GET'])
@login_required
@admin_required
def failed_logins():
    """Admin view: list all failed login attempts."""
    records = FailedLogin.query.all()
    return render_template('admin/failed_logins.html', failed_logins=records, active='users')

@users_bp.route('/create', methods=['GET', 'POST'], defaults={'user_id': None})
@users_bp.route('/<int:user_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_user(user_id):
    """Admin view: create a new user or edit an existing user."""
    if user_id is None:
        user_obj = User()  # new user
    else:
        user_obj = User.query.get_or_404(user_id)
    form = UserForm(obj=user_obj)
    if form.validate_on_submit():
        form.populate_obj(user_obj)
        try:
            db.session.add(user_obj)
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Duplicate user name or email. Please use unique values.", "error")
            return redirect(url_for('users.list_users'))
        flash("User {} successfully.".format("created" if user_id is None else "updated"), "success")
        return redirect(url_for('users.list_users'))
    return render_template('admin/user_edit.html', form=form, user_id=user_id)

@users_bp.route('/remove/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def remove_user(user_id):
    """Admin action: delete a user (if not the primary admin)."""
    user_obj = User.query.get_or_404(user_id)
    if user_obj.id == 1:
        flash("Primary admin user cannot be deleted.", "warning")
    else:
        db.session.delete(user_obj)
        db.session.commit()
        flash("User deleted.", "success")
    return redirect(url_for('users.list_users'))
