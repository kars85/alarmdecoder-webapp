from flask import Blueprint, render_template, request, flash, redirect, url_for, jsonify, current_app
from flask_login import login_required
from ad2web.decorators import admin_required
from werkzeug.utils import secure_filename
import os, json, zipfile
from ad2web.forms.updater_form import UpdateFirmwareForm, UpdateFirmwareJSONForm
from ad2web.services.updater_service import UpdaterService

# Blueprint for updater endpoints
updater_bp = Blueprint('update', __name__, url_prefix='/update')

@updater_bp.route('/')
@login_required
@admin_required
def index():
    """Show the update status page with current components and their update info."""
    updates = getattr(current_app.decoder, 'updates', {}) or {}
    return render_template('updater/index.html', updates=updates)

@updater_bp.route('/update', methods=['POST'])
@login_required
@admin_required
def update():
    """Initiate an update for a specific component (AJAX endpoint)."""
    result = {'status': 'FAIL'}
    data = request.get_json(silent=True)
    if data:
        component = data.get('component')
        if component:
            result = UpdaterService.perform_update(component)
    return json.dumps(result)

@updater_bp.route('/restart', methods=['POST'])
@login_required
@admin_required
def restart():
    """Trigger a webapp service restart (AJAX endpoint)."""
    current_app.decoder.trigger_restart = True
    return json.dumps({'status': 'PASS'})

@updater_bp.route('/checkavailable', methods=['GET'])
def checkavailable():
    """
    Check if the application is available (used for polling during restart)
    and provide current update status info.
    """
    info = UpdaterService.firmware_version_info()
    info['software_update_available'] = bool(current_app.jinja_env.globals.get('update_available', False))
    info['status'] = 'PASS'
    return json.dumps(info)

@updater_bp.route('/check_for_updates', methods=['GET'])
@login_required
@admin_required
def check_for_updates():
    """Manually trigger an update check and redirect to the update status page."""
    UpdaterService.check_updates()
    # Also refresh firmware update availability flag
    current_app.jinja_env.globals['firmware_update_available'] = current_app.decoder.updater.check_firmware()
    return redirect(url_for('update.index'))

@updater_bp.route('/update_firmware', methods=['GET', 'POST'])
@login_required
@admin_required
def update_firmware():
    """
    Firmware update interface for selecting a firmware from the server list or uploading a file.
    Provides JSON responses for AJAX requests.
    """
    current_firmware = None
    if current_app.decoder.device:
        current_firmware = getattr(current_app.decoder.device, 'version_number', 'N/A')
    form = UpdateFirmwareJSONForm()
    form2 = UpdateFirmwareForm()
    form2.multipart = True
    firmware_list = None
    all_ok = True

    # On GET, retrieve the list of available firmware from the AlarmDecoder server
    if request.method == 'GET':
        try:
            from urllib import request as urlrequest
            with urlrequest.urlopen(UpdaterService.FIRMWARE_JSON_URL, timeout=5) as resp:
                firmware_list = json.load(resp)
        except Exception as e:
            flash('Cannot connect to AlarmDecoder server to retrieve firmware list.', 'error')
            all_ok = False
        if firmware_list and 'firmware' in firmware_list:
            form.firmware_file_json.choices = [(fw['file'], fw['version']) for fw in firmware_list['firmware']]

    # If a firmware selection was submitted (download firmware and start update)
    if form.validate_on_submit():
        file_url = form.firmware_file_json.data
        return_data = {}
        try:
            from urllib import request as urlrequest
            # Download the firmware ZIP file from the selected URL
            local_zip, headers = urlrequest.urlretrieve(file_url)
            with zipfile.ZipFile(local_zip) as zf:
                hex_files = [name for name in zf.namelist() if name.endswith('.hex')]
                if not hex_files:
                    return_data['error'] = "NOHEX"
                    return jsonify(return_data)
                # Use the first .hex file found in the archive
                hex_filename = hex_files[0]
                file_data = zf.read(hex_filename)
                tmp_path = os.path.join('/tmp', secure_filename(hex_filename))
                with open(tmp_path, 'wb') as tmpf:
                    tmpf.write(file_data)
                # Count lines starting with ':' (Intel HEX format) to determine length
                line_count = len([line for line in file_data.splitlines() if line and line.startswith(b':')])
                current_app.decoder.firmware_file = tmp_path
                current_app.decoder.firmware_length = line_count
                UpdaterService.start_firmware_update(tmp_path, line_count)
                return_data['uploading'] = hex_filename
        except Exception as e:
            current_app.logger.error(f"Firmware download/update failed: {e}")
            return_data['error'] = "DOWNLOAD_FAILED"
        return jsonify(return_data)

    # If a firmware file was uploaded via the form
    if form2.is_submitted():
        files = request.files.getlist('file')
        return_data = {}
        if not files or files[0].filename == '':
            return_data['error'] = "NOFILE"
            return jsonify(return_data)
        file_storage = files[0]
        file_data = file_storage.read()
        filename = secure_filename(file_storage.filename)
        tmp_path = os.path.join('/tmp', filename)
        with open(tmp_path, 'wb') as tmpf:
            tmpf.write(file_data)
        line_count = len([line for line in file_data.splitlines() if line and line.startswith(b':')])
        current_app.decoder.firmware_file = tmp_path
        current_app.decoder.firmware_length = line_count
        UpdaterService.start_firmware_update(tmp_path, line_count)
        return_data['uploading'] = filename
        return jsonify(return_data)

    # Render the firmware update page (initial GET or if form not submitted)
    return render_template('updater/firmware_json.html',
                           current_firmware=current_firmware,
                           form=form, form2=form2,
                           firmwarejson=firmware_list,
                           all_ok="true" if all_ok else "false")
