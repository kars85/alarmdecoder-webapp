from flask import Blueprint, render_template, current_app, request, flash, url_for, redirect, Response, jsonify
from flask_login import login_required, current_user
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..user import User, UserDetail
from ..decorators import admin_required
from ..settings.models import Setting
from .forms import (ProfileForm, PasswordForm, ImportSettingsForm, HostSettingsForm,
                    EthernetSelectionForm, EthernetConfigureForm, SwitchBranchForm,
                    EmailConfigureForm, UPNPForm, VersionCheckerForm, ExportConfigureForm)
from .constants import DAILY, NONE  # other constants like HOSTS_FILE, NETWORK_FILE, etc., are used in service
from ..certificate import Certificate, CA, SERVER
from ..ser2sock import ser2sock
from ..upnp import UPNP
from ..exporter import Exporter
from ..services.settings_service import SettingsService

settings = Blueprint('settings', __name__, url_prefix='/settings')

# --- Routes ---

@settings.route('/')
@login_required
def index():
    """Displays the main settings page."""
    use_ssl = Setting.get_by_name('use_ssl', default=False).value
    return render_template('settings/index.html', ssl=use_ssl, active='index')

@settings.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Handles user profile updates (details and avatar)."""
    user = db.session.get(User, current_user.id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for('frontend.index'))
    # Ensure user_detail exists
    if not user.user_detail:
        user.user_detail = UserDetail()
        db.session.add(user.user_detail)
    form = ProfileForm(obj=user.user_detail, next=request.args.get('next'))
    if not form.is_submitted():
        form.email.data = user.email
    if form.validate_on_submit():
        # Handle avatar upload
        upload_file = request.files.get(form.avatar_file.name)
        if upload_file and upload_file.filename:
            if hasattr(upload_file, 'filename') and upload_file.filename != '':
                if upload_file and upload_file.filename and hasattr(upload_file, 'read'):
                    if upload_file and upload_file.filename:
                        if upload_file.filename.rsplit('.', 1)[1].lower() in current_app.config.get('ALLOWED_EXTENSIONS', []):
                            try:
                                user_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], f"user_{user.id}")
                                from ..utils import make_dir, allowed_file
                                make_dir(user_dir)
                                # Save file with secure hashed name
                                file_content = upload_file.read()
                                upload_file.seek(0)
                                _, ext = os.path.splitext(upload_file.filename)
                                ext = ext.lower()
                                from datetime import datetime
                                today = datetime.now().strftime('%Y%m%d')
                                import hashlib
                                hash_filename = hashlib.sha256(file_content).hexdigest() + "_" + today + ext
                                avatar_path = os.path.join(user_dir, hash_filename)
                                upload_file.save(avatar_path)
                                user.avatar = hash_filename
                                current_app.logger.info(f"Saved new avatar for user {user.id}: {hash_filename}")
                            except Exception as e:
                                current_app.logger.error(f"Failed to save avatar for user {user.id}: {e}", exc_info=True)
                                flash('Avatar upload failed.', 'error')
                        else:
                            flash('Invalid file type for avatar.', 'error')
        # Update User and UserDetail from form
        user.email = form.email.data
        user_detail = user.user_detail
        user_detail.sex = form.sex_code.data
        user_detail.age = form.age.data
        user_detail.phone = form.phone.data
        user_detail.url = form.url.data
        user_detail.deposit = form.deposit.data
        user_detail.location = form.location.data
        user_detail.bio = form.bio.data
        try:
            db.session.commit()
            flash('Profile updated successfully.', 'success')
            return redirect(url_for('settings.profile'))
        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.error(f"Database error updating profile: {e}")
            flash('Error saving profile. Please try again.', 'error')
    # Render profile page (on GET or validation failure)
    return render_template('settings/profile.html', form=form, active='profile')

@settings.route('/password', methods=['GET', 'POST'])
@login_required
def password():
    """Handles user password changes."""
    user = db.session.get(User, current_user.id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for('frontend.index'))
    form = PasswordForm(next=request.args.get('next'))
    if form.validate_on_submit():
        try:
            # current password check is in validate_password
            user.password = form.new_password.data  # password hashing via property
            db.session.commit()
            flash('Password updated successfully.', 'success')
            next_url = form.next.data or request.args.get('next')
            if next_url:
                # Prevent open redirects
                if url_for('settings.password') in next_url or next_url.startswith('/'):
                    return redirect(next_url)
                else:
                    current_app.logger.warning(f"Ignoring potentially unsafe next URL: {next_url}")
            # No next_url: redirect back to password page to clear form
            return redirect(url_for('settings.password'))
        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.error(f"Database error updating password for user {user.id}: {e}")
            flash('Error updating password in database.', 'error')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Unexpected error updating password for user {user.id}: {e}", exc_info=True)
            flash('An unexpected error occurred while updating the password.', 'error')
    use_ssl = Setting.get_by_name('use_ssl', default=False).value
    return render_template('settings/password.html', user=user, active="password", form=form, ssl=use_ssl)

@settings.route('/host', methods=['GET', 'POST'])
@login_required
@admin_required
def host():
    """Displays host system information and network interface selection."""
    operating_system = platform.system()
    is_linux = operating_system.lower() == 'linux'
    if not is_linux:
        flash('Host network/system configuration is currently only supported on Linux systems.', 'warning')
    # Gather system info
    uptime = SettingsService.get_system_uptime() if is_linux else "N/A"
    cpu_temp = SettingsService.get_cpu_temperature() if is_linux else "N/A"
    try:
        hostname_val = socket.getfqdn()
    except socket.gaierror:
        hostname_val = socket.gethostname()
    form = None
    interfaces = []
    if is_linux and has_netifaces:
        interfaces = SettingsService.list_network_interfaces()
        if interfaces:
            choices = [(iface, iface) for iface in interfaces]
            if choices:
                form = EthernetSelectionForm()
                form.ethernet_devices.choices = choices
                if form.validate_on_submit():
                    selected_device = form.ethernet_devices.data
                    return redirect(url_for('settings.configure_ethernet_device', device=selected_device))
            else:
                flash('No configurable network interfaces found.', 'info')
        else:
            flash('Could not retrieve network interfaces.', 'warning')
    elif is_linux and not has_netifaces:
        flash('Python module "netifaces" not found. Network configuration is disabled. Install via pip.', 'warning')
    return render_template('settings/host.html',
                           hostname=hostname_val,
                           uptime=uptime,
                           cpu_temp=cpu_temp,
                           form=form,
                           interfaces=interfaces,
                           is_linux=is_linux,
                           active="host")

@settings.route('/hostname', methods=['GET', 'POST'])
@login_required
@admin_required
def hostname():
    """Handles changing the system hostname (Linux only)."""
    if platform.system().lower() != 'linux':
        flash('Hostname changes are only supported on Linux.', 'error')
        return redirect(url_for('settings.host'))
    try:
        current_hostname = socket.getfqdn()
    except socket.gaierror:
        current_hostname = socket.gethostname()
    form = HostSettingsForm()
    if not form.is_submitted():
        form.hostname.data = current_hostname
    if form.validate_on_submit():
        new_hostname = form.hostname.data.strip()
        if not new_hostname:
            flash('Hostname cannot be empty.', 'error')
            return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")
        if new_hostname == current_hostname:
            flash('Hostname is already set to this value.', 'info')
            return redirect(url_for('settings.host'))
        if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$", new_hostname):
            flash('Invalid hostname format. Use letters, numbers, and hyphens (not at start/end).', 'error')
            return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")
        try:
            SettingsService.update_hostname(new_hostname)
            flash('Hostname updated. It will take effect on next reboot.', 'success')
            return redirect(url_for('settings.host'))
        except Exception as e:
            current_app.logger.error(f"Hostname change failed: {e}", exc_info=True)
            flash(f'Error changing hostname: {e}', 'error')
    return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")

@settings.route('/get_ethernet_info/<string:device>', methods=['GET'])
@login_required
@admin_required
def get_ethernet_info(device):
    """Returns network information for a specific device as JSON."""
    if not has_netifaces:
        return Response(json.dumps({'error': 'netifaces module not available'}), status=501, mimetype='application/json')
    # Validate device name format
    if not re.match(r'^[a-zA-Z0-9\.\-\_]+$', device):
        return Response(json.dumps({'error': f'Invalid device name format: "{device}".'}), status=400, mimetype='application/json')
    try:
        info = {}
        addresses = netifaces.ifaddresses(device)
        gateways = netifaces.gateways()
        ipv4_list = addresses.get(netifaces.AF_INET, [])
        info['ipv4'] = ipv4_list[0] if ipv4_list else None
        ipv6_list = addresses.get(netifaces.AF_INET6, [])
        info['ipv6'] = ipv6_list[0] if ipv6_list else None
        link_list = addresses.get(netifaces.AF_LINK, [])
        info['mac_address'] = link_list[0]['addr'] if link_list else None
        default_gw = gateways.get('default', {})
        ipv4_gw = default_gw.get(netifaces.AF_INET)
        info['default_gateway'] = ipv4_gw[0] if ipv4_gw else None
    except ValueError:
        current_app.logger.info(f'Device "{device}" not found by netifaces.')
        return Response(json.dumps({'error': f'Device "{device}" not found or invalid.'}), status=404, mimetype='application/json')
    except Exception as e:
        current_app.logger.error(f"Error getting info for device {device}: {e}", exc_info=True)
        return Response(json.dumps({'error': f'Error retrieving information for {device}.'}), status=500, mimetype='application/json')
    return Response(json.dumps({'device': device, **info}), status=200, mimetype='application/json')

@settings.route('/reboot', methods=['POST'])
@login_required
@admin_required
def system_reboot():
    """Initiates a system reboot (Linux only)."""
    if platform.system().lower() != 'linux':
        flash('Reboot is only supported on Linux.', 'error')
        return redirect(url_for('settings.host'))
    try:
        SettingsService.reboot_system()
        flash('Reboot command issued successfully. The device will now restart.', 'success')
        # Redirect immediately, likely the server will go down
        return redirect(url_for('frontend.index', message='rebooting'))
    except ErrorReturnCode as e:
        if e.exit_code == 143:  # process terminated (possible if server is shutting down)
            flash('Reboot initiated. Connection may be lost.', 'info')
            return redirect(url_for('frontend.index', message='rebooting'))
        else:
            current_app.logger.error(f"Error issuing reboot command: {e}")
            output_msg = e.stderr.decode() or e.stdout.decode() if hasattr(e, 'stderr') else str(e)
            flash(f'Error issuing reboot command: {output_msg}', 'error')
            return redirect(url_for('settings.host'))
    except Exception as e:
        current_app.logger.error(f'Unexpected error during reboot: {e}', exc_info=True)
        flash(f'An unexpected error occurred during reboot: {e}', 'error')
        return redirect(url_for('settings.host'))

@settings.route('/shutdown', methods=['POST'])
@login_required
@admin_required
def system_shutdown():
    """Initiates a system shutdown (Linux only)."""
    if platform.system().lower() != 'linux':
        flash('Shutdown is only supported on Linux.', 'error')
        return redirect(url_for('settings.host'))
    try:
        SettingsService.shutdown_system()
        flash('Shutdown command issued. The system will power off.', 'success')
        return redirect(url_for('frontend.index', message='shutdown'))
    except Exception as e:
        current_app.logger.error(f'Error issuing shutdown: {e}', exc_info=True)
        flash(f'Error issuing shutdown command: {e}', 'error')
        return redirect(url_for('settings.host'))

@settings.route('/network/<string:device>', methods=['GET', 'POST'])
@login_required
@admin_required
def configure_ethernet_device(device):
    """Configures network (DHCP/Static) for a given interface (Linux only)."""
    if platform.system().lower() != 'linux':
        flash('Network configuration is only supported on Linux.', 'error')
        return redirect(url_for('settings.host'))
    if not has_netifaces:
        flash('Python module "netifaces" not found. Cannot configure network.', 'error')
        return redirect(url_for('settings.host'))
    if not re.match(r'^[a-zA-Z0-9\.\-\_]+$', device) or device == 'lo':
        flash(f'Invalid or unsupported device name: "{device}".', 'warning')
        return redirect(url_for('settings.host'))
    form = EthernetConfigureForm()
    form.ethernet_device.data = device
    # Check file permissions
    can_read = os.access(NETWORK_FILE, os.R_OK)
    can_write = False
    try:
        with sudo:
            can_write = os.access(NETWORK_FILE, os.W_OK)
    except Exception as e:
        current_app.logger.warning(f"Could not check write permissions for {NETWORK_FILE} with sudo: {e}")
    if not can_read:
        flash(f'{NETWORK_FILE} is not readable! Cannot determine current settings.', 'error')
    if not can_write:
        flash(f'{NETWORK_FILE} is not writable! Network settings cannot be saved.', 'warning')
    # Parse existing network config
    device_blocks = SettingsService.parse_network_file() if can_read else None
    if device_blocks is None and can_read:
        flash(f'Error parsing {NETWORK_FILE}. Current settings may be unavailable.', 'warning')
    # Get current config for device
    current_type = 'dhcp'
    current_ip = ''
    current_netmask = ''
    current_gateway = ''
    block = SettingsService.get_interface_config_block(device, device_blocks) if device_blocks else None
    if block:
        lines = block.strip().split('\n')
        first_line = lines[0].strip()
        if ' static' in first_line:
            current_type = 'static'
            for line in lines[1:]:
                parts = line.strip().split()
                if len(parts) == 2:
                    if parts[0] == 'address': current_ip = parts[1]
                    elif parts[0] == 'netmask': current_netmask = parts[1]
                    elif parts[0] == 'gateway': current_gateway = parts[1]
        elif ' dhcp' in first_line:
            current_type = 'dhcp'
        elif ' manual' in first_line:
            current_type = 'manual'
            flash(f'Device "{device}" is set to manual configuration. Cannot modify via this interface.', 'error')
            return redirect(url_for('settings.host'))
        elif ' loopback' in first_line:
            flash('Cannot configure the loopback device.', 'warning')
            return redirect(url_for('settings.host'))
    elif can_read:
        flash(f'Device "{device}" not explicitly configured in {NETWORK_FILE}. Assuming DHCP.', 'info')
    # Get live network info as fallback
    live_ip = ''
    live_mask = ''
    live_gateway = ''
    try:
        addr_info = netifaces.ifaddresses(device)
        if netifaces.AF_INET in addr_info:
            ipv4_info = addr_info[netifaces.AF_INET][0]
            live_ip = ipv4_info.get('addr', '')
            live_mask = ipv4_info.get('netmask', '')
        gw_info = netifaces.gateways().get('default', {})
        if netifaces.AF_INET in gw_info.get('default', {}):
            live_gateway = gw_info['default'][netifaces.AF_INET][0]
    except Exception as e:
        current_app.logger.warning(f"Could not retrieve live network details for {device}: {e}")
    # Pre-populate form fields
    if not form.is_submitted():
        form.connection_type.data = current_type
        if current_type == 'static':
            form.ip_address.data = current_ip
            form.netmask.data = current_netmask
            form.gateway.data = current_gateway
        else:
            form.ip_address.data = live_ip
            form.netmask.data = live_mask
            form.gateway.data = live_gateway
    if form.validate_on_submit():
        if not can_write:
            flash(f'{NETWORK_FILE} is not writable! Cannot save changes.', 'error')
            return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write)
        if current_type == 'manual':
            flash(f'Device "{device}" is set to manual configuration. Cannot modify via this interface.', 'error')
            return redirect(url_for('settings.host'))
        new_type = form.connection_type.data
        new_ip = form.ip_address.data.strip()
        new_mask = form.netmask.data.strip()
        new_gw = form.gateway.data.strip()
        if device_blocks is None:
            flash(f"Could not read or parse {NETWORK_FILE}. Cannot save changes.", "error")
            return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write)
        # Build new iface block
        new_block = f"iface {device} inet {new_type}\n"
        if new_type == 'static':
            if not new_ip or not new_mask:
                flash('IP Address and Subnet Mask are required for static configuration.', 'error')
                return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write)
            new_block += f"\taddress {new_ip}\n"
            new_block += f"\tnetmask {new_mask}\n"
            if new_gw:
                new_block += f"\tgateway {new_gw}\n"
        # Insert or replace configuration in blocks list
        updated_blocks = list(device_blocks)
        iface_index = -1
        auto_index = -1
        for i, blk in enumerate(updated_blocks):
            lines = blk.strip().split('\n')
            if lines:
                if re.match(rf'^\s*iface\s+{re.escape(device)}\s+inet\s+', lines[0]):
                    iface_index = i
                elif re.match(rf'^\s*auto\s+{re.escape(device)}\s*$', lines[0]):
                    auto_index = i
        if iface_index != -1:
            updated_blocks[iface_index] = new_block
        else:
            updated_blocks.append(new_block)
            flash(f'Interface "{device}" was not in config; added new configuration.', 'info')
        # Ensure 'auto device' exists
        auto_line = f"auto {device}\n"
        if device != 'lo':
            if auto_index == -1:
                try:
                    idx = updated_blocks.index(new_block)
                    updated_blocks.insert(idx, auto_line)
                except ValueError:
                    updated_blocks.insert(0, auto_line)
                flash(f'Added "auto {device}" to configuration.', 'info')
            else:
                if device not in updated_blocks[auto_index]:
                    updated_blocks[auto_index] = updated_blocks[auto_index].strip() + f" {device}\n"
        # Write changes and apply
        success = False
        try:
            with sudo:
                success = SettingsService.write_network_file(updated_blocks)
        except Exception as e:
            current_app.logger.error(f"Error writing network file via sudo: {e}", exc_info=True)
            flash(f"Error writing network configuration: {e}", 'error')
        if success:
            flash('Network configuration updated. Applying changes...', 'info')
            try:
                with sudo:
                    current_app.logger.info(f"Running 'ifdown {device}'")
                    from sh import ifdown, ifup
                    ifdown(device, _ok_code=[0, 1])
                    current_app.logger.info(f"Running 'ifup {device}'")
                    ifup(device)
                flash(f'Network interface "{device}" restarted successfully.', 'success')
                return redirect(url_for('settings.host'))
            except ErrorReturnCode as e:
                err_output = e.stderr.decode() or e.stdout.decode() if hasattr(e, 'stderr') else str(e)
                current_app.logger.error(f"Error applying network changes for '{device}': {e}\nOutput:\n{err_output}")
                flash(f'Error applying network changes for "{device}": {err_output}. You may need to restart networking manually.', 'error')
            except CommandNotFound:
                current_app.logger.error("'ifdown' or 'ifup' command not found.")
                flash('"ifdown" or "ifup" command not found. Please restart networking manually.', 'error')
            except Exception as e:
                current_app.logger.error(f'Unexpected error applying network changes: {e}', exc_info=True)
                flash(f'Unexpected error applying network changes: {e}', 'error')
            # If we get here, restart failed but file was written
            flash('Configuration saved, but failed to apply changes automatically. A manual network restart may be required.', 'warning')
            return redirect(url_for('settings.host'))
    # GET or validation failure:
    return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write)

@settings.route('/configure_exports', methods=['GET', 'POST'])
@login_required
@admin_required
def configure_exports():
    """Configures automatic settings export (backup) options."""
    form = ExportConfigureForm()
    # Check if system email is configured (required for email export)
    email_server = Setting.get_by_name('system_email_server', default='').value
    email_configured = bool(email_server)
    if not form.is_submitted():
        form.frequency.data = SettingsService.get_setting('export_frequency', default=DAILY, type_func=int)
        form.email.data = SettingsService.get_setting('export_email_enable', default=True, type_func=bool)
        form.email_address.data = SettingsService.get_setting('export_mailer_to', default='')
        form.local_file.data = SettingsService.get_setting('enable_local_file_storage', default=True, type_func=bool)
        default_path = os.path.join(current_app.instance_path, 'exports')
        local_path = SettingsService.get_setting('export_local_path', default=default_path)
        form.local_file_path.data = os.path.abspath(local_path or default_path)
        form.days_to_keep.data = SettingsService.get_setting('days_to_keep', default=7, type_func=int)
    if form.validate_on_submit():
        errors = False
        if form.email.data and not email_configured:
            flash('Cannot enable email exports because system email is not configured.', 'error')
            errors = True
        if form.email.data and not form.email_address.data:
            form.email_address.errors.append("Email address is required when email export is enabled.")
            errors = True
        if form.local_file.data:
            if not form.local_file_path.data:
                form.local_file_path.errors.append("Local file path is required when local storage is enabled.")
                errors = True
            else:
                path = form.local_file_path.data
                try:
                    if not os.path.exists(path):
                        parent_dir = os.path.dirname(path)
                        if not os.access(parent_dir, os.W_OK | os.X_OK):
                            form.local_file_path.errors.append(f"Cannot write to parent directory '{parent_dir}' to create export path.")
                            errors = True
                    elif not os.access(path, os.W_OK | os.X_OK):
                        form.local_file_path.errors.append("Local file path exists but is not writable.")
                        errors = True
                except Exception as e:
                    form.local_file_path.errors.append(f"Error checking path permissions: {e}")
                    errors = True
        if errors:
            return render_template('settings/configure_exports.html', form=form, active='advanced', email_configured=email_configured)
        # Save settings
        settings_to_update = {
            'export_frequency': int(form.frequency.data),
            'export_email_enable': form.email.data,
            'export_mailer_to': form.email_address.data.strip(),
            'enable_local_file_storage': form.local_file.data,
            'export_local_path': os.path.abspath(form.local_file_path.data.strip()),
            'days_to_keep': form.days_to_keep.data
        }
        try:
            for name, value in settings_to_update.items():
                Setting.set_value(name, value)
            db.session.commit()
            current_app.logger.info("Export settings updated in database.")
            # Refresh exporter thread if running
            if hasattr(current_app, 'decoder'):
                try:
                    SettingsService.refresh_exporter_thread(current_app.decoder)
                except Exception as e:
                    current_app.logger.warning(f"Exporter thread not refreshed: {e}")
            flash('Export settings updated successfully.', 'success')
            return redirect(url_for('settings.index'))
        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.error(f"Database error saving export settings: {e}")
            flash('Error saving export settings to database.', 'error')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating export configuration: {e}", exc_info=True)
            flash(f'An unexpected error occurred: {e}', 'error')
    if not email_configured:
        flash('System email is not configured. Email export option is disabled until system email is set up.', 'warning')
    return render_template('settings/configure_exports.html', form=form, active='advanced', email_configured=email_configured)

@settings.route('/export', methods=['GET'])
@login_required
@admin_required
def export():
    """Triggers an immediate manual export of settings."""
    try:
        current_app.logger.info("Manual settings export triggered.")
        exporter = Exporter()
        exporter.exportSettings()  # generate export archive
        response = exporter.ReturnResponse()  # get flask Response with file
        current_app.logger.info("Manual settings export created successfully.")
        return response
    except Exception as e:
        current_app.logger.error(f"Manual export failed: {e}", exc_info=True)
        flash(f'Error creating export file: {e}', 'error')
        return redirect(url_for('settings.index'))

@settings.route('/git', methods=['GET', 'POST'])
@login_required
@admin_required
def switch_branch():
    """Handles switching Git branches for the webapp and API library."""
    form = SwitchBranchForm()
    # Determine repository paths
    web_repo_path = current_app.config.get('WEB_REPO_PATH', os.path.abspath(os.curdir))
    api_repo_path = current_app.config.get('API_REPO_PATH', None)
    git_info_web = {'error': None}
    git_info_api = {'error': None}
    # Populate form choices (remotes and branches) for both repos
    if not form.is_submitted():
        # Initialize fields for form
        form.remotes_web.choices = []
        form.branches_web.choices = []
        form.remotes_api.choices = []
        form.branches_api.choices = []
        # Populate if repos exist
        import sh
        git = sh.git.bake(_cwd=web_repo_path, _err_to_out=True)
        try:
            # Get remotes for webapp repo
            remote_lines = git.remote("-v").stdout.decode('utf-8').splitlines()
            form.remotes_web.choices = _build_remotes_list(remote_lines)
            # Get branches for webapp repo
            branch_lines = git.branch("-r").stdout.decode('utf-8').splitlines()
            form.branches_web.choices = [(line.strip(), line.strip()) for line in branch_lines if line.strip()]
        except Exception as e:
            git_info_web['error'] = f"Error reading webapp repository: {e}"
        if api_repo_path:
            git_api = sh.git.bake(_cwd=api_repo_path, _err_to_out=True)
            try:
                remote_lines = git_api.remote("-v").stdout.decode('utf-8').splitlines()
                form.remotes_api.choices = _build_remotes_list(remote_lines)
                branch_lines = git_api.branch("-r").stdout.decode('utf-8').splitlines()
                form.branches_api.choices = [(line.strip(), line.strip()) for line in branch_lines if line.strip()]
            except Exception as e:
                git_info_api['error'] = f"Error reading API repository: {e}"
        # Also fill current branch info for display
        git_info_web = _get_git_info(web_repo_path)
        git_info_api = _get_git_info(api_repo_path) if api_repo_path else {'branch': None, 'remote': None}
    if form.validate_on_submit():
        # Perform branch switch
        new_remote_web = form.remotes_web.data
        new_branch_web = form.branches_web.data
        new_remote_api = form.remotes_api.data
        new_branch_api = form.branches_api.data
        import sh
        try:
            git = sh.git.bake(_cwd=web_repo_path)
            git.fetch("--all")
            git.checkout(f"{new_remote_web}/{new_branch_web}", B=new_branch_web)
            current_app.logger.info(f"Webapp repository switched to {new_remote_web}/{new_branch_web}.")
            if api_repo_path:
                git_api = sh.git.bake(_cwd=api_repo_path)
                git_api.fetch("--all")
                git_api.checkout(f"{new_remote_api}/{new_branch_api}", B=new_branch_api)
                current_app.logger.info(f"API repository switched to {new_remote_api}/{new_branch_api}.")
            flash('Code branch switched successfully. Please restart the application for changes to take effect.', 'success')
            return redirect(url_for('settings.switch_branch'))
        except Exception as e:
            current_app.logger.error(f"Error switching branches: {e}", exc_info=True)
            flash(f'Error switching branches: {e}', 'error')
    # Helper functions for remotes and git info inside the view
    def _build_remotes_list(lines):
        remotes = {}
        for line in lines:
            parts = line.strip().split()
            if len(parts) == 3:
                name, url, type_str = parts
                type_val = type_str.strip('()')
                if name not in remotes:
                    remotes[name] = {'url': url, 'types': set()}
                remotes[name]['types'].add(type_val)
        choices = []
        for name, data in sorted(remotes.items()):
            types_str = ", ".join(sorted(data['types']))
            label = f"{name} - {data['url']} ({types_str})"
            choices.append((name, label))
        return choices
    def _get_git_info(repo_path):
        info = {'branch': None, 'remote': None, 'branches': [], 'remotes': [], 'error': None, 'is_dirty': False}
        if not repo_path or not os.path.isdir(os.path.join(repo_path, '.git')):
            info['error'] = f"Not a Git repository: {repo_path}"
            return info
        import sh
        git = sh.git.bake(_cwd=repo_path, _err_to_out=True, c='color.ui=false')
        try:
            status_porcelain = git.status("--porcelain").stdout.decode('utf-8').strip()
            info['is_dirty'] = bool(status_porcelain)
            try:
                current_branch = git('symbolic-ref', '--short', 'HEAD').stdout.decode('utf-8').strip()
                info['branch'] = current_branch
            except sh.ErrorReturnCode:
                try:
                    current_commit = git('rev-parse', '--short', 'HEAD').stdout.decode('utf-8').strip()
                    info['branch'] = f"HEAD (detached at {current_commit})"
                except sh.ErrorReturnCode:
                    info['branch'] = "HEAD (unknown state)"
                info['remote'] = None
            if info['remote'] is None and info['branch']:
                try:
                    tracking = git('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}').stdout.decode('utf-8').strip()
                    if tracking:
                        # tracking format: origin/branch
                        parts = tracking.split('/')
                        if len(parts) >= 2:
                            info['remote'] = parts[0]
                except sh.ErrorReturnCode:
                    info['remote'] = None
            branches_out = git.branch('-r').stdout.decode('utf-8').splitlines()
            info['branches'] = [b.strip() for b in branches_out if b.strip()]
            remotes_out = git.remote('-v').stdout.decode('utf-8').splitlines()
            info['remotes'] = _build_remotes_list(remotes_out)
        except Exception as e:
            info['error'] = str(e)
            current_app.logger.error(f"Error getting git info for {repo_path}: {e}")
        return info
    # Render the branch switch page
    return render_template('settings/git.html',
                           form=form,
                           current_remote_web=git_info_web.get('remote') or 'origin',
                           current_branch_web=git_info_web.get('branch') or 'unknown',
                           current_remote_api=git_info_api.get('remote') or 'origin',
                           current_branch_api=git_info_api.get('branch') or 'unknown',
                           active="advanced")

@settings.route('/import', methods=['GET', 'POST'])
@login_required
@admin_required
def import_backup():
    """Handles importing a settings backup from an uploaded file."""
    form = ImportSettingsForm()
    if form.validate_on_submit():
        file = request.files.get(form.import_file.name)
        if not file or file.filename == '':
            flash('No file selected for import.', 'error')
        else:
            try:
                import tarfile, io, six
                tar_bytes = file.read()
                tar_stream = io.BytesIO(tar_bytes)
                with tarfile.open(fileobj=tar_stream, mode='r:gz') as tar:
                    for member in tar.getmembers():
                        if member.isfile():
                            filename = os.path.basename(member.name)
                            if filename in settings.constants.EXPORT_MAP:
                                model_class = settings.constants.EXPORT_MAP[filename]
                                current_app.logger.info(f"Processing import for {model_class.__name__} from {filename}...")
                                SettingsService._import_model(tar, member, model_class)
                    db.session.commit()
                flash('Settings import completed successfully.', 'success')
                return redirect(url_for('settings.index'))
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"Import failed: {e}", exc_info=True)
                flash(f'Import failed: {e}', 'error')
    use_ssl = Setting.get_by_name('use_ssl', default=False).value
    return render_template('settings/import.html', form=form, ssl=use_ssl, active="import")

@settings.route('/diagnostics', methods=['GET'])
@login_required
@admin_required
def system_diagnostics():
    """Displays diagnostic information about the connected AlarmDecoder device."""
    device_settings = {}
    flags_description = {}
    if hasattr(current_app, 'decoder') and getattr(current_app.decoder, 'device', None):
        device = current_app.decoder.device
        current_app.logger.debug("Retrieving device diagnostics...")
        try:
            device_settings['address'] = getattr(device, 'address', 'N/A')
            configbits = getattr(device, 'configbits', 0)
            address_mask = getattr(device, 'address_mask', 0)
            device_settings['configbits'] = f"0x{configbits:04X}" if isinstance(configbits, int) else 'N/A'
            device_settings['address_mask'] = f"0x{address_mask:04X}" if isinstance(address_mask, int) else 'N/A'
            device_settings['firmware'] = getattr(device, 'version_number', 'N/A')
            serial_num = getattr(device, 'serial_number', 'N/A')
            device_settings['serial'] = serial_num.upper() if isinstance(serial_num, str) else 'N/A'
            mode_val = getattr(device, 'mode', None)
            if mode_val == Certificate or mode_val == CA:  # using Certificate constants ADEMCO, DSC
                device_settings['mode'] = "ADEMCO/Honeywell"
            elif mode_val == SERVER:  # or DSC constant
                device_settings['mode'] = "DSC"
            else:
                device_settings['mode'] = f"Unknown/Other ({mode_val})"
            device_settings['emulate_zone'] = "Yes" if getattr(device, 'emulate_zone', False) else "No"
            device_settings['emulate_relay'] = "Yes" if getattr(device, 'emulate_relay', False) else "No"
            device_settings['emulate_lrr'] = "Yes" if getattr(device, 'emulate_lrr', False) else "No"
            device_settings['deduplicate'] = "Yes" if getattr(device, 'deduplicate', False) else "No"
            # Flags (version_flags)
            flags = getattr(device, 'version_flags', 0)
            if isinstance(flags, int):
                device_settings['flags'] = f"0x{flags:04X}"
                if not flags_description:
                    flags_description['Info'] = "Flag meanings depend on firmware version."
            else:
                device_settings['flags'] = 'N/A'
            current_app.logger.debug("Device diagnostics retrieved successfully.")
        except Exception as e:
            current_app.logger.error(f"Error retrieving device diagnostics: {e}", exc_info=True)
            flash("Error retrieving some device details.", "warning")
            device_settings['error'] = str(e)
    else:
        current_app.logger.warning("Device diagnostics requested but device not connected.")
        flash("AlarmDecoder device not connected or initialized.", "warning")
        device_settings['error'] = "Device not available"
    return render_template('settings/diagnostics.html', settings=device_settings, flags_description=flags_description, active="diagnostics")

@settings.route('/advanced', methods=['GET'])
@login_required
@admin_required
def advanced():
    """Displays the advanced settings landing page."""
    return render_template('settings/advanced.html', active="advanced")

@settings.route('/get_imports_list', methods=['GET'])
@login_required
@admin_required
def get_system_imports():
    """Returns a JSON list of known/required modules and their import status."""
    imported_status = {}
    current_app.logger.debug("Checking system imports...")
    loaded_modules = set(m.split('.')[0] for m in sys.modules.keys())
    for module_name in sorted(settings.constants.KNOWN_MODULES):
        found = False
        import_error = None
        version = None
        try:
            module = importlib.import_module(module_name)
            found = True
            version = getattr(module, '__version__', None) or getattr(module, 'VERSION', None) or getattr(module, 'version', None)
            if isinstance(version, tuple):
                version = '.'.join(map(str, version))
        except ImportError:
            import_error = "Not installed"
            if module_name in loaded_modules:
                import_error += " (partially loaded)"
        except Exception as e:
            import_error = f"Error importing: {e}"
            current_app.logger.warning(f"Unexpected error importing {module_name}: {e}", exc_info=True)
        imported_status[module_name] = {
            'modname': module_name,
            'found': found,
            'version': version if found else None,
            'error': import_error
        }
        current_app.logger.debug(f"Module {module_name}: Found={found}, Version={version}, Error={import_error}")
    return Response(json.dumps(imported_status), status=200, mimetype='application/json')

@settings.route('/disable_forward', methods=['POST'])
@login_required
@admin_required
def disable_forwarding():
    """Disables the currently configured UPNP port forward."""
    if not has_upnp:
        flash('UPnP library (miniupnpc) not installed. Cannot manage port forwarding.', 'error')
        return redirect(url_for('settings.index'))
    external_port_setting = Setting.get_by_name('upnp_external_port')
    external_port_val = external_port_setting.value if external_port_setting else None
    if not external_port_val:
        flash('No active port forward configured to disable.', 'info')
        return redirect(url_for('settings.index'))
    try:
        port_to_remove = int(external_port_val)
        current_app.logger.info(f"Removing UPnP port forward for external port {port_to_remove}")
        upnp_helper = UPNP(current_app.decoder)
        if upnp_helper.removePortForward(port_to_remove):
            flash(f'Successfully removed port forward for external port {port_to_remove}.', 'success')
            current_app.logger.info(f"UPnP removal successful for port {port_to_remove}.")
        else:
            flash(f'Could not remove port forward for external port {port_to_remove} via UPnP (it may not exist or the router rejected it).', 'warning')
            current_app.logger.warning(f"UPnP removal failed or rule not found for port {port_to_remove}.")
        # Clear stored port forward settings regardless
        internal_port_setting = Setting.get_by_name('upnp_internal_port')
        if internal_port_setting:
            internal_port_setting.value = None
            db.session.add(internal_port_setting)
        if external_port_setting:
            external_port_setting.value = None
            db.session.add(external_port_setting)
        db.session.commit()
        current_app.logger.info("Cleared UPnP port forward settings in database.")
    except ValueError:
        flash(f'Invalid port number stored: "{external_port_val}". Cannot remove forward.', 'error')
        current_app.logger.error(f"Invalid UPnP port value in settings: {external_port_val}")
    except Exception as ex:
        db.session.rollback()
        current_app.logger.error(f"Error removing port forward: {ex}", exc_info=True)
        flash(f'Error removing port forward: {ex}', 'error')
    return redirect(url_for('settings.index'))

@settings.route('/port_forward', methods=['GET', 'POST'])
@login_required
@admin_required
def port_forwarding():
    """Configures UPNP port forwarding."""
    if not has_upnp:
        flash('UPnP library (miniupnpc) not installed. Cannot manage port forwarding.', 'error')
        return render_template('settings/port_forward.html', form=None, upnp_unavailable=True, active="port_forward")
    form = UPNPForm()
    upnp_helper = None
    internal_ip = "Detection failed"
    try:
        upnp_helper = UPNP(current_app.decoder)
        internal_ip = upnp_helper.discover() or "Could not detect via UPnP"
    except Exception as e:
        current_app.logger.error(f"Failed to initialize UPnP helper: {e}", exc_info=True)
        flash(f"Error initializing UPnP: {e}", "error")
    external_ip = SettingsService.get_external_ip() or "Could not detect"
    current_internal_port = Setting.get_by_name('upnp_internal_port').value
    current_external_port = Setting.get_by_name('upnp_external_port').value
    if not form.is_submitted():
        form.internal_port.data = int(current_internal_port) if current_internal_port else 443
        form.external_port.data = int(current_external_port) if current_external_port else random.randint(10000, 60000)
    if form.validate_on_submit():
        target_internal = form.internal_port.data
        target_external = form.external_port.data
        if not upnp_helper:
            flash("UPnP helper could not be initialized. Cannot perform action.", "error")
        else:
            try:
                if current_external_port:
                    try:
                        old_ext = int(current_external_port)
                        current_app.logger.info(f"Removing existing port forward {old_ext} before adding new one.")
                        upnp_helper.removePortForward(old_ext)
                    except Exception as e_rem:
                        current_app.logger.warning(f"Error removing existing port forward {current_external_port}: {e_rem}")
                current_app.logger.info(f"Adding port forward: External {target_external} -> Internal {target_internal} for IP {internal_ip}")
                success = upnp_helper.addPortForward(target_internal, target_external)
                if success:
                    flash(f'Successfully added UPnP port forward: External {target_external} -> Internal {target_internal} ({internal_ip})', 'success')
                    Setting.set_value('upnp_internal_port', target_internal)
                    Setting.set_value('upnp_external_port', target_external)
                    db.session.commit()
                    current_app.logger.info("Saved new UPnP ports to database.")
                    return redirect(url_for('settings.index'))
                else:
                    flash(f'Failed to add port forward via UPnP. Ensure port {target_external} is available and UPnP is enabled on the router.', 'error')
                    current_app.logger.error("UPnP addPortForward returned False.")
            except ValueError:
                flash('Invalid port number entered.', 'error')
            except Exception as ex:
                db.session.rollback()
                current_app.logger.error(f"Error setting up port forwarding: {ex}", exc_info=True)
                flash(f'Error setting up port forwarding: {ex}', 'error')
    return render_template('settings/port_forward.html',
                           form=form,
                           current_internal_port=current_internal_port,
                           current_external_port=current_external_port,
                           internal_ip=internal_ip,
                           external_ip=external_ip,
                           upnp_unavailable=False,
                           active="port_forward")
