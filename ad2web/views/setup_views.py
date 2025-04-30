from flask import Blueprint, render_template, flash, redirect, url_for, request
from flask_login import current_user
from ..services.setup_service import SetupService
from ..extensions import db
from ..forms.setup_form import (DeviceTypeForm, NetworkDeviceForm, LocalDeviceForm, LocalDeviceFormUSB,
                                DeviceForm, SSLForm, SSLHostForm, TestDeviceForm,
                                CreateAccountForm, EmailSetupForm)
from ..settings.models import Setting
from flask import current_app

setup = Blueprint('setup', __name__, url_prefix='/setup')

@setup.before_request
def protect_setup():
    # Redirect to login if setup has been completed
    if SetupService.is_setup_complete():
        flash('Initial setup is already completed.', 'info')
        return redirect(url_for('frontend.login'))

@setup.route('/')
def index():
    return render_template('setup/index.html')

@setup.route('/type', methods=['GET', 'POST'])
def device_type():
    form = DeviceTypeForm()
    if request.method == 'GET':
        # Pre-select existing values if any (in case of re-entry)
        device_type_val = Setting.get_by_name('device_type').value
        device_location_val = Setting.get_by_name('device_location').value
        if device_type_val:
            form.device_type.data = device_type_val
        if device_location_val:
            form.device_location.data = device_location_val if device_location_val in ['local', 'network'] else 'local'
    if form.validate_on_submit():
        next_endpoint = SetupService.set_device_type(form.device_type.data, form.device_location.data)
        return redirect(url_for(next_endpoint))
    return render_template('setup/type.html', form=form)

@setup.route('/local', methods=['GET', 'POST'])
def local():
    # Determine device type from settings (AD2USB, AD2PI, etc.)
    device_type = Setting.get_by_name('device_type').value
    form = LocalDeviceFormUSB() if device_type == 'AD2USB' else LocalDeviceForm()
    if request.method == 'GET':
        if device_type != 'AD2USB':
            # Prefill path and baudrate from stored settings or defaults
            saved_path = Setting.get_by_name('device_path').value
            saved_baud = Setting.get_by_name('device_baudrate').value
            if saved_path:
                form.device_path.data = saved_path
            else:
                # Default paths per device type
                from ..setup.constants import DEFAULT_PATHS
                form.device_path.data = DEFAULT_PATHS.get(device_type, '/dev/ttyUSB0')
            if saved_baud:
                form.baudrate.data = int(saved_baud)
            else:
                from ..setup.constants import DEFAULT_BAUDRATES
                form.baudrate.data = DEFAULT_BAUDRATES.get(device_type, 115200)
        else:
            # For AD2USB, populate available device choices
            usb_devices = SetupService.find_usb_devices()
            if not usb_devices:
                flash('No AD2USB devices found. Please connect the device and refresh.', 'error')
            form.device_path.choices = [(dev, dev) for dev in usb_devices] or form.device_path.choices
            # If a device was previously saved, pre-select it
            saved_path = Setting.get_by_name('device_path').value
            if saved_path and any(saved_path == dev for dev in usb_devices):
                form.device_path.data = saved_path
        # Handle ser2sock availability for management option
        if not current_app or not hasattr(current_app, 'decoder'):
            # If no decoder context, assume ser2sock may not be applicable
            pass
        if not hasattr(form, 'confirm_management') or not form.confirm_management:
            # Form already adjusted for ser2sock presence in AD2USB above
            pass
        else:
            # If ser2sock not installed, remove the management option field
            try:
                from ..ser2sock import ser2sock as s2s_check
                if not s2s_check.exists():
                    del form.confirm_management
            except Exception:
                pass
    if form.validate_on_submit():
        next_endpoint, warning = SetupService.save_local_device_settings(device_type, form.device_path.data,
                                                                         form.baudrate.data,
                                                                         getattr(form, 'confirm_management', form.confirm_management.data) if hasattr(form, 'confirm_management') else False)
        if warning:
            flash(warning, 'warning')
        return redirect(url_for(next_endpoint))
    return render_template('setup/local.html', form=form)

@setup.route('/network', methods=['GET', 'POST'])
def network():
    form = NetworkDeviceForm()
    if request.method == 'GET':
        saved_address = Setting.get_by_name('device_address').value
        saved_port = Setting.get_by_name('device_port').value
        saved_ssl = Setting.get_by_name('use_ssl').value
        if saved_address:
            form.device_address.data = saved_address
        if saved_port:
            form.device_port.data = int(saved_port)
        if saved_ssl is not None:
            # saved_ssl might be 'True'/'False' or 1/0
            form.ssl.data = True if str(saved_ssl).lower() == 'true' or str(saved_ssl) == '1' else False
    if form.validate_on_submit():
        next_endpoint = SetupService.save_network_device_settings(form.device_address.data, form.device_port.data,
                                                                  form.ssl.data)
        return redirect(url_for(next_endpoint))
    return render_template('setup/network.html', form=form)

@setup.route('/sslclient', methods=['GET', 'POST'])
def sslclient():
    form = SSLForm()
    if form.validate_on_submit():
        error = SetupService.upload_ssl_certificates(form.ca_cert.data, form.cert.data, form.key.data)
        if error:
            flash(error, 'danger')
            return render_template('setup/sslclient.html', form=form)
        flash('SSL certificates uploaded successfully.', 'success')
        # Move to device configuration step
        stage_setting = Setting.get_by_name('setup_stage')
        if stage_setting:
            stage_setting.value = 6
            db.session.add(stage_setting); db.session.commit()
        return redirect(url_for('setup.device'))
    return render_template('setup/sslclient.html', form=form)

@setup.route('/sslserver', methods=['GET', 'POST'])
def sslserver():
    form = SSLHostForm()
    if request.method == 'GET':
        saved_ssl = Setting.get_by_name('use_ssl').value
        saved_cfg = Setting.get_by_name('ser2sock_config_path').value
        saved_addr = Setting.get_by_name('device_address').value
        saved_port = Setting.get_by_name('device_port').value
        if saved_ssl is not None:
            form.ssl.data = True if str(saved_ssl).lower() == 'true' or str(saved_ssl) == '1' else False
        if saved_cfg:
            form.config_path.data = saved_cfg
        if saved_addr:
            form.device_address.data = saved_addr
        if saved_port:
            form.device_port.data = int(saved_port)
    if form.validate_on_submit():
        error = SetupService.configure_ser2sock(form.ssl.data, form.config_path.data,
                                                form.device_address.data, form.device_port.data)
        if error:
            flash(error, 'danger')
            return render_template('setup/sslserver.html', form=form)
        # If no error, proceed to device configuration
        return redirect(url_for('setup.device'))
    return render_template('setup/sslserver.html', form=form)

@setup.route('/device', methods=['GET', 'POST'])
def device():
    form = DeviceForm()
    if not form.is_submitted():
        # Try to populate from running decoder if available
        if current_app and hasattr(current_app, 'decoder') and current_app.decoder.device:
            form.panel_mode.data = current_app.decoder.device.mode
            form.keypad_address.data = current_app.decoder.device.address
            form.address_mask.data = f"{current_app.decoder.device.address_mask:0>8x}"
            form.internal_address_mask.data = f"{current_app.decoder.internal_address_mask:0>8x}"
            form.lrr_enabled.data = current_app.decoder.device.emulate_lrr
            form.deduplicate.data = current_app.decoder.device.deduplicate
            form.zone_expanders.data = [str(idx+1) for idx, val in enumerate(current_app.decoder.device.emulate_zone) if val]
            form.relay_expanders.data = [str(idx+1) for idx, val in enumerate(current_app.decoder.device.emulate_relay) if val]
        else:
            # Populate from saved settings
            panel_mode_val = Setting.get_by_name('panel_mode').value
            keypad_addr_val = Setting.get_by_name('keypad_address').value
            addr_mask_val = Setting.get_by_name('address_mask').value
            int_mask_val = Setting.get_by_name('internal_address_mask').value
            lrr_val = Setting.get_by_name('lrr_enabled').value
            dedup_val = Setting.get_by_name('deduplicate').value
            if panel_mode_val is not None:
                form.panel_mode.data = int(panel_mode_val)
            if keypad_addr_val:
                form.keypad_address.data = int(keypad_addr_val)
            if addr_mask_val is not None:
                form.address_mask.data = addr_mask_val
            if int_mask_val is not None:
                form.internal_address_mask.data = int_mask_val if isinstance(int_mask_val, int) else int(int_mask_val, 16) if int_mask_val else form.internal_address_mask.data
            if lrr_val is not None:
                form.lrr_enabled.data = True if str(lrr_val).lower() == 'true' or str(lrr_val) == '1' else False
            if dedup_val is not None:
                form.deduplicate.data = True if str(dedup_val).lower() == 'true' or str(dedup_val) == '1' else False
            # MultiCheckbox fields stored as comma-separated booleans
            zone_str = Setting.get_by_name('emulate_zone_expanders').value
            relay_str = Setting.get_by_name('emulate_relay_expanders').value
            if zone_str:
                values = [v == 'True' for v in zone_str.split(',')]
                form.zone_expanders.data = [str(i+1) for i, v in enumerate(values) if v]
            if relay_str:
                values = [v == 'True' for v in relay_str.split(',')]
                form.relay_expanders.data = [str(i+1) for i, v in enumerate(values) if v]
    if form.validate_on_submit():
        # Save panel configuration to database
        zx = [str(x) in form.zone_expanders.data for x in range(1, 6)]
        rx = [str(x) in form.relay_expanders.data for x in range(1, 5)]
        Setting.get_by_name('panel_mode').value = form.panel_mode.data
        Setting.get_by_name('keypad_address').value = form.keypad_address.data
        Setting.get_by_name('address_mask').value = form.address_mask.data
        Setting.get_by_name('internal_address_mask').value = form.internal_address_mask.data
        Setting.get_by_name('lrr_enabled').value = form.lrr_enabled.data
        Setting.get_by_name('deduplicate').value = form.deduplicate.data
        Setting.get_by_name('emulate_zone_expanders').value = ','.join(map(str, zx))
        Setting.get_by_name('emulate_relay_expanders').value = ','.join(map(str, rx))
        stage_setting = Setting.get_by_name('setup_stage')
        if stage_setting:
            stage_setting.value = 7
            db.session.add(stage_setting)
        db.session.commit()
        return redirect(url_for('setup.test'))
    return render_template('setup/device.html', form=form)

@setup.route('/test', methods=['GET', 'POST'])
def test():
    form = TestDeviceForm()
    if not form.is_submitted():
        # Indicate testing stage in progress
        stage_setting = Setting.get_by_name('setup_stage')
        stage_setting.value = 7
        db.session.add(stage_setting); db.session.commit()
    else:
        if SetupService.is_setup_complete():
            flash('Setup complete!', 'success')
            return redirect(url_for('frontend.login'))
        else:
            # Proceed to email setup stage
            stage_setting = Setting.get_by_name('setup_stage')
            if stage_setting:
                stage_setting.value = 8
                db.session.add(stage_setting); db.session.commit()
            return redirect(url_for('setup.email'))
    return render_template('setup/test.html', form=form)

@setup.route('/email', methods=['GET', 'POST'])
def email_setup():
    form = EmailSetupForm()
    if request.method == 'GET':
        # Prepopulate email settings if previously saved
        server = Setting.query.filter_by(name='system_email_server').first()
        port = Setting.query.filter_by(name='system_email_port').first()
        tls = Setting.query.filter_by(name='system_email_tls').first()
        auth = Setting.query.filter_by(name='system_email_auth').first()
        user = Setting.query.filter_by(name='system_email_username').first()
        pwd = Setting.query.filter_by(name='system_email_password').first()
        sender = Setting.query.filter_by(name='system_email_from').first()
        if server: form.mail_server.data = server.value
        if port: form.mail_port.data = int(port.value) if port.value is not None else form.mail_port.data
        if tls: form.use_tls.data = True if str(tls.value).lower() == 'true' or str(tls.value) == '1' else False
        if auth: form.use_auth.data = True if str(auth.value).lower() == 'true' or str(auth.value) == '1' else False
        if user: form.username.data = user.value
        # Do not prefill password for security
        if sender: form.default_sender.data = sender.value
    if form.validate_on_submit():
        error = SetupService.save_email_settings(form.mail_server.data, form.mail_port.data,
                                                 form.use_tls.data, form.use_auth.data,
                                                 form.username.data, form.password.data, form.default_sender.data)
        if error:
            flash(error, 'danger')
            return render_template('setup/email.html', form=form)
        # Advance to account creation stage
        stage_setting = Setting.get_by_name('setup_stage')
        if stage_setting:
            stage_setting.value = 9
            db.session.add(stage_setting); db.session.commit()
        return redirect(url_for('setup.account'))
    return render_template('setup/email.html', form=form)

@setup.route('/account', methods=['GET', 'POST'])
def account():
    form = CreateAccountForm()
    if form.validate_on_submit():
        user, error = SetupService.create_admin_user(form.name.data, form.email.data, form.password.data)
        if error:
            flash(error, 'danger')
            return render_template('setup/account.html', form=form)
        flash('Setup complete! Please log in with your new account.', 'success')
        return redirect(url_for('frontend.login'))
    return render_template('setup/account.html', form=form)
