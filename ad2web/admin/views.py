# ad2web/admin/views.py

from flask import (
    Blueprint, render_template,
    request, jsonify, abort
)
from flask_login import login_required
from ..decorators import admin_required
from ..services.user_service import (
    UserService, DuplicateUserError
)

from ad2web.forms.user_form import UserForm

admin_bp = Blueprint('admin', __name__, url_prefix='/settings/admin')


@admin_bp.route('/')
@login_required
@admin_required
def index():
    # Redirect to users list
    return render_template('admin/index.html', active='admin')


@admin_bp.route('/users')
@login_required
@admin_required
def users_page():
    # Renders the users overview page (DataTable will fetch data via AJAX)
    return render_template('admin/users.html', active='users')


@admin_bp.route('/users/data')
@login_required
@admin_required
def users_data():
    # AJAX: return JSON list of users for DataTable
    users = UserService.list_users()
    data = [{
        'id': u.id,
        'name': u.name,
        'email': u.email,
        'role': u.role_code,
        'status': u.status_code
    } for u in users]
    return jsonify(data=data)


@admin_bp.route('/users/create', methods=['POST'])
@login_required
@admin_required
def user_create():
    form = UserForm()
    if not form.validate_on_submit():
        return jsonify(success=False, errors=form.errors), 400
    try:
        user = UserService.create_user(
            name=form.name.data,
            email=form.email.data,
            password=form.password.data,
            role_code=int(form.role_code.data),
            status_code=int(form.status_code.data)
        )
        return jsonify(success=True, user_id=user.id)
    except DuplicateUserError as e:
        return jsonify(success=False, error=str(e)), 409


@admin_bp.route('/users/<int:user_id>/edit', methods=['POST'])
@login_required
@admin_required
def user_edit(user_id):
    form = UserForm()
    if not form.validate_on_submit():
        return jsonify(success=False, errors=form.errors), 400
    try:
        user = UserService.update_user(
            user_id=user_id,
            name=form.name.data,
            email=form.email.data,
            password=form.password.data or None,
            role_code=int(form.role_code.data),
            status_code=int(form.status_code.data)
        )
        if not user:
            abort(404)
        return jsonify(success=True)
    except DuplicateUserError as e:
        return jsonify(success=False, error=str(e)), 409


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(user_id):
    if not UserService.delete_user(user_id):
        return jsonify(success=False), 400
    return jsonify(success=True)


@admin_bp.route('/failed_logins')
@login_required
@admin_required
def failed_logins_page():
    # Renders the failed-logins overview page
    return render_template('admin/failed_logins.html', active='failed_logins')


@admin_bp.route('/failed_logins/data')
@login_required
@admin_required
def failed_logins_data():
    # AJAX: return JSON list of failed login attempts
    attempts = UserService.list_failed_logins()
    data = [{
        'name': a.name,
        'ip_address': a.ip_address,
        'user_agent': a.user_agent_string,
        'timestamp': a.login_time.isoformat()
    } for a in attempts]
    return jsonify(data=data)
