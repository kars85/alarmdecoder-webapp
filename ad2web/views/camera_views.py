from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from ad2web.services import camera_service
from ad2web.settings.models import Setting
from ad2web.keypad.models import KeypadButton
from ad2web.forms.camera_form import CameraForm

cameras_bp = Blueprint('cameras', __name__, url_prefix='/cameras')

@cameras_bp.route('/', methods=['GET'])
@login_required
def index():
    """Camera monitor page - view live camera feeds (unchanged except modernization of query)."""
    camera_list = camera_service.get_cameras_for_user(current_user.id)
    buttons = KeypadButton.query.filter_by(user_id=current_user.id).all()
    use_ssl = Setting.get_by_name('use_ssl', default=False).value
    return render_template('cameras/index.html', camera_list=camera_list, buttons=buttons, ssl=use_ssl)

@cameras_bp.route('/camera_list', methods=['GET'])
@login_required
def cam_list():
    """Camera management page - list all cameras with AJAX-capable interface."""
    camera_list = camera_service.get_cameras_for_user(current_user.id)
    # 'active' used in layout to highlight menu, 'ssl' included for consistency (if needed by templates/scripts)
    use_ssl = Setting.get_by_name('use_ssl', default=False).value
    return render_template('cameras/cam_list.html', camera_list=camera_list, active="cameras", ssl=use_ssl)

@cameras_bp.route('/create_camera', methods=['GET', 'POST'])
@login_required
def create_camera():
    form = CameraForm()
    if request.method == 'POST':
        if form.validate_on_submit():
            # Create new camera via service
            cam = camera_service.create_camera(current_user.id, form.name.data, form.get_jpg_url.data,
                                               form.username.data, form.password.data)
            # Prepare success message and response
            success_msg = "Camera Created"
            if request.is_json or request.accept_mimetypes.accept_json:  # AJAX request expects JSON
                return jsonify(success=True, message=success_msg,
                               camera={"id": cam.id, "name": cam.name, "url": cam.get_jpg_url})
            flash(success_msg, 'success')
            return redirect(url_for('cameras.cam_list'))
        # Validation failed
        if request.is_json or request.accept_mimetypes.accept_json:
            # Return form HTML with errors for AJAX to display
            html = render_template('cameras/form_modal.html', form=form, form_action=url_for('cameras.create_camera'))
            return html, 400
        # Non-AJAX: re-render create page with errors
    # GET request (display form)
    if request.is_json or request.accept_mimetypes.accept_json:
        # Return the form HTML snippet for modal
        return render_template('cameras/form_modal.html', form=form, form_action=url_for('cameras.create_camera'))
    return render_template('cameras/create_cam.html', form=form, active="cameras")

@cameras_bp.route('/edit_camera/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_camera(id):
    cam = camera_service.get_camera(id, current_user.id)
    if not cam:
        abort(404)
    form = CameraForm(obj=cam)
    if request.method == 'POST':
        if form.validate_on_submit():
            # Update camera via service
            updated = camera_service.update_camera(id, current_user.id, form.name.data, form.get_jpg_url.data,
                                                   form.username.data, form.password.data)
            success_msg = "Camera Updated"
            if request.is_json or request.accept_mimetypes.accept_json:
                return jsonify(success=True, message=success_msg,
                               camera={"id": cam.id, "name": cam.name, "url": cam.get_jpg_url})
            flash(success_msg, 'success')
            return redirect(url_for('cameras.cam_list'))
        # Validation failed
        if request.is_json or request.accept_mimetypes.accept_json:
            html = render_template('cameras/form_modal.html', form=form, form_action=url_for('cameras.edit_camera', id=id))
            return html, 400
        # Non-AJAX: fall through to re-render form with errors below
    # GET request - return form
    if request.is_json or request.accept_mimetypes.accept_json:
        return render_template('cameras/form_modal.html', form=form, form_action=url_for('cameras.edit_camera', id=id))
    return render_template('cameras/edit_cam.html', form=form, id=id, active="cameras")

@cameras_bp.route('/remove_camera/<int:id>', methods=['GET', 'POST'])
@login_required
def remove_camera(id):
    """Delete a camera and redirect or return JSON."""
    success = camera_service.delete_camera(id, current_user.id)
    if request.is_json or request.accept_mimetypes.accept_json:
        if success:
            return jsonify(success=True, message="Camera Removed", id=id)
        return jsonify(success=False, message="Camera not found or not authorized"), 404
    if success:
        flash('Camera Removed', 'success')
    else:
        flash('Camera not found.', 'danger')
    return redirect(url_for('cameras.cam_list'))
