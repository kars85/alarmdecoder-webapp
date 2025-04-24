ad2web/settings/views.py
# -*- coding: utf-8 -*-

# Standard library imports
from __future__ import absolute_import
import io
import json
import os
import platform
import random
import re
import socket
import ssl
import sys
import tarfile
import time
from datetime import datetime, timedelta
import hashlib
import importlib

# Third-party imports
from flask import Blueprint, render_template, current_app, request, flash, Response, url_for, redirect
from flask_login import login_required, current_user
import sh
import six
from six.moves import urllib
from sqlalchemy.exc import SQLAlchemyError

# Conditional third-party imports
try:
    import netifaces
    hasnetifaces = True
except ImportError:
    hasnetifaces = False

try:
    import miniupnpc
    has_upnp = True
except ImportError:
    has_upnp = False

try:
    # Use sh.contrib.service if available, otherwise fallback to sh.service
    try:
        from sh.contrib import service as sh_service
    except ImportError:
        from sh import service as sh_service
    hasservice = True
except ImportError:
    hasservice = False
    sh_service = None # Define it as None if import fails

# Local application imports
from alarmdecoder.panels import ADEMCO, DSC # PANEL_TYPES seems unused
from ..extensions import db
from ..user import User, UserDetail
from ..utils import allowed_file, make_dir, tar_add_directory, tar_add_textfile, INSTANCE_FOLDER_PATH
from ..decorators import admin_required
from ..settings import Setting
from .forms import (ProfileForm, PasswordForm, ImportSettingsForm, HostSettingsForm,
                    EthernetSelectionForm, EthernetConfigureForm, SwitchBranchForm,
                    EmailConfigureForm, UPNPForm, VersionCheckerForm, ExportConfigureForm)
# setup.forms seems unused here: DeviceTypeForm, LocalDeviceForm, NetworkDeviceForm
from .constants import EXPORT_MAP, HOSTS_FILE, HOSTNAME_FILE, NETWORK_FILE, KNOWN_MODULES, DAILY, IP_CHECK_SERVER_URL
# NETWORK_DEVICE, SERIAL_DEVICE seem unused
from ..certificate import Certificate, CA, SERVER
from ..ser2sock import ser2sock
from ..upnp import UPNP
from ..exporter import Exporter
# Notification, NotificationSetting, Zone seem unused
from sh import sudo # Keep sudo import separate for clarity

# Blueprint Configuration
settings = Blueprint('settings', __name__, url_prefix='/settings')

# --- Helper Functions ---

def _get_system_uptime():
    """Retrieves the system uptime from /proc/uptime."""
    try:
        with open('/proc/uptime', 'r') as f:
            uptime_seconds = float(f.readline().split()[0])
            # Format timedelta to exclude microseconds
            uptime_string = str(timedelta(seconds=uptime_seconds)).split('.')[0]
    except (IOError, IndexError, ValueError) as e:
        current_app.logger.warning(f"Could not read system uptime: {e}")
        uptime_string = "N/A"
    return uptime_string

def _get_cpu_temperature():
    """Retrieves the CPU temperature from the system."""
    # Common paths for CPU temperature
    temp_paths = [
        '/sys/class/thermal/thermal_zone0/temp',
        '/sys/class/hwmon/hwmon0/temp1_input',
        '/sys/class/hwmon/hwmon1/temp1_input', # Add more potential paths if needed
    ]
    for temp_file in temp_paths:
        if os.path.isfile(temp_file):
            try:
                with open(temp_file, 'r') as f:
                    # Read and strip whitespace, then convert to float
                    temp_reading = float(f.readline().strip())
                    # Check if the value is likely in millidegrees Celsius
                    if temp_reading > 1000: # Simple heuristic
                        cpu_temp_celsius = temp_reading / 1000.0
                    else:
                        cpu_temp_celsius = temp_reading # Assume it's already in Celsius
                    return f"{cpu_temp_celsius:.1f} °C"
            except (IOError, ValueError) as e:
                current_app.logger.warning(f"Error reading temperature from {temp_file}: {e}")
                continue # Try the next path
    return 'Not supported' # If no path worked

def _list_network_interfaces():
    """Lists available network interfaces using netifaces."""
    if hasnetifaces:
        try:
            return netifaces.interfaces()
        except Exception as e:
            current_app.logger.error(f"Error listing network interfaces: {e}")
            return []
    return []

def _parse_network_file():
    """
    Parses the Linux network configuration file (/etc/network/interfaces).
    Returns a list of configuration blocks.
    NOTE: This parsing is basic and specific to Debian/Ubuntu style interfaces files.
    """
    if not os.path.exists(NETWORK_FILE):
        current_app.logger.warning(f"Network file not found: {NETWORK_FILE}")
        return None
    try:
        with open(NETWORK_FILE, 'r') as f:
            text = f.read()
        # Regex to find lines starting with keywords, handling potential whitespace
        # and ensuring they are at the beginning of a line (^)
        # This is a simple approach; complex files might need more robust parsing.
        indexes = [s.start() for s in re.finditer(r'^\s*(auto|iface|source|mapping|allow-|wpa-)', text, re.MULTILINE)]
        if not indexes:
            return [text] if text.strip() else [] # Return whole file if no keywords, or empty list

        # Split the file content based on these indices to get configuration blocks
        result = [text[indexes[i]:indexes[i+1]] for i in range(len(indexes)-1)] + [text[indexes[-1]:]]
        return result
    except IOError as e:
        current_app.logger.error(f"Could not read network file {NETWORK_FILE}: {e}")
        return None
    except Exception as e:
        current_app.logger.error(f"Error parsing network file {NETWORK_FILE}: {e}")
        return None

def _write_network_file(device_map):
    """Writes the modified network configuration back to the file."""
    if device_map is None:
        current_app.logger.error("Attempted to write None to network file.")
        return False
    try:
        # Ensure the directory exists (though /etc/network usually does)
        os.makedirs(os.path.dirname(NETWORK_FILE), exist_ok=True)
        text = ''.join(device_map)
        with open(NETWORK_FILE, 'w') as f:
            f.write(text)
        current_app.logger.info(f"Successfully wrote network configuration to {NETWORK_FILE}")
        return True
    except IOError as e:
        current_app.logger.error(f"Could not write to network file {NETWORK_FILE}: {e}")
        flash(f'Error writing network configuration: {e}', 'error')
        return False
    except Exception as e:
        current_app.logger.error(f"Unexpected error writing network file {NETWORK_FILE}: {e}")
        flash(f'Unexpected error writing network configuration: {e}', 'error')
        return False

def _get_ethernet_properties_from_map(device, device_map):
    """Extracts configuration block for a specific network device from the parsed map."""
    if device_map is not None:
        # Find the block starting with 'iface <device> inet ...'
        for block in device_map:
            lines = block.strip().split('\n')
            if lines and re.match(rf'^\s*iface\s+{re.escape(device)}\s+inet\s+', lines[0]):
                return block # Return the whole block for this interface
    return None # Return None if not found

def _sethostname(config_file, old_hostname, new_hostname):
    """Updates the hostname in a given configuration file."""
    if not os.path.exists(config_file):
        current_app.logger.warning(f"Hostname config file not found: {config_file}")
        return False
    try:
        with open(config_file, 'r') as f:
            content = f.read()

        # Be more specific to avoid replacing substrings within other words
        # Use word boundaries (\b) if the hostname is expected to be standalone
        # This might need adjustment based on file format (e.g., /etc/hosts)
        # Simple replacement for now, as in original code:
        new_content = content.replace(old_hostname, new_hostname)

        if new_content != content:
            with open(config_file, 'w') as f:
                f.write(new_content)
            current_app.logger.info(f"Updated hostname in {config_file}")
            return True
        else:
            current_app.logger.info(f"Old hostname '{old_hostname}' not found in {config_file}, no changes made.")
            return False # No changes needed or old hostname not found
    except IOError as e:
        current_app.logger.error(f"Error updating hostname in {config_file}: {e}")
        flash(f'Error updating {os.path.basename(config_file)}: {e}', 'error')
        return False
    except Exception as e:
        current_app.logger.error(f"Unexpected error updating hostname in {config_file}: {e}")
        flash(f'Unexpected error updating {os.path.basename(config_file)}: {e}', 'error')
        return False


def _import_model(tar, tarinfo, model):
    """Imports data for a specific SQLAlchemy model from a tar archive member."""
    # Clear existing data for this model - BE CAREFUL, this is destructive.
    try:
        model_name = model.__name__
        current_app.logger.info(f"Deleting existing data for model: {model_name}")
        # Execute delete immediately before proceeding
        db.session.query(model).delete()
        db.session.flush() # Ensure delete is processed before inserts

        filedata = tar.extractfile(tarinfo).read()
        items = json.loads(filedata.decode('utf-8')) # Decode bytes to string

        current_app.logger.info(f"Importing {len(items)} items for model: {model_name}")
        for itm in items:
            m = model()
            for k, v in six.iteritems(itm):
                # Check if the key corresponds to a column in the model's table
                if hasattr(m.__class__, k): # Basic check if attribute exists
                    column = model.__table__.columns.get(k)
                    if column is not None:
                        # Handle DateTime conversion
                        if isinstance(column.type, db.DateTime) and v is not None:
                            try:
                                # Attempt parsing with microseconds
                                v = datetime.strptime(v, '%Y-%m-%d %H:%M:%S.%f')
                            except ValueError:
                                try:
                                     # Fallback to parsing without microseconds
                                    v = datetime.strptime(v, '%Y-%m-%d %H:%M:%S')
                                except ValueError:
                                    current_app.logger.warning(f"Could not parse date string '{v}' for column '{k}' in model {model_name}. Setting to None.")
                                    v = None # Set to None if parsing fails

                        # Handle User password specifically
                        if k == 'password' and model == User:
                            # The import stores the already hashed password.
                            # Set the underlying '_password' attribute directly to avoid re-hashing.
                            setattr(m, '_password', v)
                        else:
                            setattr(m, k, v)
                    else:
                        # Key might be a relationship, not a direct column.
                        # This basic import doesn't handle relationships well.
                        current_app.logger.debug(f"Key '{k}' is not a direct column in model {model_name}. Skipping.")
                else:
                    current_app.logger.warning(f"Attribute '{k}' not found in model {model_name} during import. Skipping.")

            db.session.add(m)
        # No commit here, commit happens after all models are processed in the main import function.
        current_app.logger.info(f"Successfully staged import for model: {model_name}")

    except json.JSONDecodeError as e:
        db.session.rollback() # Rollback changes for this model on error
        current_app.logger.error(f"JSON decode error importing model {model_name} from {tarinfo.name}: {e}")
        raise ValueError(f"Invalid JSON data for {model_name}") from e
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Database error importing model {model_name}: {e}")
        raise ValueError(f"Database error importing {model_name}") from e
    except Exception as e:
        db.session.rollback() # Rollback changes for this model on error
        current_app.logger.error(f"Unexpected error importing model {model_name}: {e}", exc_info=True)
        raise ValueError(f"Failed to import data for {model_name}: {e}") from e


def _import_refresh():
    """Refreshes application state after importing settings."""
    current_app.logger.info("Refreshing application state after import...")
    # Update ser2sock configuration if path exists
    config_path_setting = Setting.get_by_name('ser2sock_config_path')
    if config_path_setting and config_path_setting.value:
        current_app.logger.info(f"Updating ser2sock config at: {config_path_setting.value}")
        try:
            # Retrieve settings with defaults and ensure correct types
            kwargs = {}
            kwargs['device_path'] = Setting.get_by_name('device_path', default='/dev/serial0').value
            kwargs['device_baudrate'] = int(Setting.get_by_name('device_baudrate', default=115200).value)
            kwargs['device_port'] = int(Setting.get_by_name('device_port', default=10000).value)
            kwargs['use_ssl'] = Setting.get_by_name('use_ssl', default=False).value
            # Ensure raw_device_mode is treated as boolean/int(0 or 1) if needed by ser2sock
            raw_mode_val = Setting.get_by_name('raw_device_mode', default=1).value
            kwargs['raw_device_mode'] = int(raw_mode_val) if str(raw_mode_val).isdigit() else (1 if raw_mode_val else 0)


            if kwargs['use_ssl']:
                current_app.logger.info("SSL is enabled, regenerating certificate files.")
                # Regenerate certificate files based on imported DB data
                ca_cert = Certificate.query.filter_by(type=CA).first()
                server_cert = Certificate.query.filter_by(type=SERVER).first()

                if ca_cert and server_cert:
                    kwargs['ca_cert'] = ca_cert
                    kwargs['server_cert'] = server_cert
                    try:
                        Certificate.save_certificate_index()
                        Certificate.save_revocation_list()
                        current_app.logger.info("Certificate index and CRL saved.")
                    except Exception as e:
                        current_app.logger.error(f"Error saving certificate files after import: {e}")
                        flash(f"Warning: Could not update certificate files: {e}", "warning")
                else:
                    current_app.logger.warning("SSL enabled but CA or Server certificate not found in DB after import. Disabling SSL for ser2sock.")
                    flash("Warning: SSL is enabled, but certificates were not found in the imported data. SSL for ser2sock may be disabled.", "warning")
                    kwargs['use_ssl'] = False # Disable SSL if certs are missing

            ser2sock.update_config(config_path_setting.value, **kwargs)
            current_app.logger.info("ser2sock configuration updated.")
        except SQLAlchemyError as e:
             current_app.logger.error(f"Database error retrieving settings for ser2sock refresh: {e}")
             flash("Error retrieving settings to update ser2sock.", "error")
        except ValueError as e:
             current_app.logger.error(f"Invalid setting value during ser2sock refresh: {e}")
             flash(f"Invalid setting value encountered: {e}", "error")
        except Exception as e:
            current_app.logger.error(f"Error updating ser2sock config after import: {e}", exc_info=True)
            flash(f"Error updating ser2sock configuration: {e}", "error")
    else:
        current_app.logger.info("ser2sock config path not set, skipping update.")


    # Reinitialize the decoder connection
    try:
        if hasattr(current_app, 'decoder') and current_app.decoder:
            current_app.logger.info("Reinitializing AlarmDecoder connection...")
            current_app.decoder.close()
            current_app.decoder.init() # Re-initializes based on potentially new settings
            current_app.logger.info("AlarmDecoder connection reinitialized.")
        else:
            current_app.logger.warning("Decoder object not found, cannot reinitialize.")
    except Exception as e:
        current_app.logger.error(f"Error reinitializing decoder after import: {e}", exc_info=True)
        flash(f"Error restarting AlarmDecoder connection: {e}", "error")

    # Refresh other components if necessary (e.g., background threads)
    if hasattr(current_app, 'decoder') and current_app.decoder:
        # Refresh Exporter Thread
        if hasattr(current_app.decoder, '_exporter_thread') and current_app.decoder._exporter_thread:
            try:
                current_app.logger.info("Refreshing exporter thread parameters...")
                current_app.decoder._exporter_thread.prepParams() # Reload export settings
                current_app.logger.info("Exporter thread parameters refreshed.")
            except Exception as e:
                 current_app.logger.error(f"Error refreshing exporter thread parameters: {e}", exc_info=True)
                 flash("Warning: Could not refresh exporter settings.", "warning")
        else:
            current_app.logger.warning("Exporter thread not found, cannot refresh its parameters.")

        # Refresh Version Checker Thread
        if hasattr(current_app.decoder, '_version_thread') and current_app.decoder._version_thread:
            try:
                current_app.logger.info("Refreshing version checker thread parameters...")
                # Reload version check settings
                timeout = int(Setting.get_by_name('version_checker_timeout', default=3600).value)
                disable = Setting.get_by_name('version_checker_disable', default=False).value
                current_app.decoder._version_thread.setTimeout(timeout)
                current_app.decoder._version_thread.setDisable(disable)
                current_app.logger.info("Version checker thread parameters refreshed.")
            except SQLAlchemyError as e:
                 current_app.logger.error(f"Database error retrieving settings for version checker refresh: {e}")
                 flash("Warning: Could not refresh version checker settings.", "warning")
            except ValueError as e:
                 current_app.logger.error(f"Invalid setting value during version checker refresh: {e}")
                 flash(f"Invalid setting value for version checker: {e}", "warning")
            except Exception as e:
                 current_app.logger.error(f"Error refreshing version checker thread parameters: {e}", exc_info=True)
                 flash("Warning: Could not refresh version checker settings.", "warning")
        else:
            current_app.logger.warning("Version checker thread not found, cannot refresh its parameters.")
    current_app.logger.info("Application state refresh complete.")


def get_external_ip():
    """Fetches the public IP address of the server."""
    try:
        # Use a context that doesn't verify SSL certs for the IP check service
        # This is generally discouraged but common for simple IP check services.
        context = ssl._create_unverified_context()
        req = urllib.request.Request(IP_CHECK_SERVER_URL, headers={'User-Agent': 'AlarmDecoder-WebApp/1.0'})
        with urllib.request.urlopen(req, context=context, timeout=5) as response:
            if response.status == 200:
                data = json.load(response)
                return data.get('origin')
            else:
                current_app.logger.warning(f"Could not retrieve external IP: Status code {response.status}")
                return None
    except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, ssl.SSLError, KeyError, AttributeError) as e:
        # Catch AttributeError for cases where response might not have expected methods
        current_app.logger.warning(f"Could not retrieve external IP: {e}")
        return None
    except Exception as e:
        current_app.logger.error(f"Unexpected error retrieving external IP: {e}", exc_info=True)
        return None

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
    # Query user by ID for efficiency and correctness
    user = db.session.get(User, current_user.id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for('frontend.index')) # Or appropriate error page

    # Ensure user_detail exists, create if not (should normally exist via relationship)
    if not user.user_detail:
        user.user_detail = UserDetail()
        db.session.add(user.user_detail) # Add the detail object explicitly if new

    form = ProfileForm(obj=user.user_detail, next=request.args.get('next'))

    # Populate form defaults not covered by obj (like email from User model)
    if not form.is_submitted():
        form.email.data = user.email
        # Role and status are usually not user-editable, handled elsewhere
        # form.role_code.data = user.role_code
        # form.status_code.data = user.status_code

    if form.validate_on_submit():
        # Handle avatar upload
        upload_file = request.files.get(form.avatar_file.name) # Use .get for safety
        if upload_file and upload_file.filename: # Check if a file was actually uploaded
            if allowed_file(upload_file.filename):
                try:
                    user_upload_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], f"user_{user.id}")
                    make_dir(user_upload_dir) # Ensure directory exists

                    # Generate a secure filename based on content hash and date
                    file_content = upload_file.read()
                    upload_file.seek(0) # Reset cursor after reading
                    _, ext = os.path.splitext(upload_file.filename)
                    ext = ext.lower() # Normalize extension
                    today = datetime.now().strftime('%Y%m%d')
                    # Use sha256 for better security than sha1
                    hash_filename = hashlib.sha256(file_content).hexdigest() + "_" + today + ext
                    avatar_path = os.path.join(user_upload_dir, hash_filename)

                    # TODO: Optionally remove the old avatar file if it exists and is different
                    # old_avatar_path = os.path.join(user_upload_dir, user.avatar) if user.avatar else None

                    upload_file.save(avatar_path)
                    user.avatar = hash_filename # Store only the filename
                    current_app.logger.info(f"Saved new avatar for user {user.id}: {hash_filename}")

                    # if old_avatar_path and os.path.exists(old_avatar_path) and old_avatar_path != avatar_path:
                    #     os.remove(old_avatar_path)

                except Exception as e:
                    current_app.logger.error(f"Failed to save avatar for user {user.id}: {e}", exc_info=True)
                    flash('Avatar upload failed.', 'error')
                    # Continue with other profile updates even if avatar fails
            else:
                flash('Invalid file type for avatar.', 'error')

        # Populate User and UserDetail objects from form
        # User fields (only email is directly on the form)
        user.email = form.email.data
        # UserDetail fields (populated via obj=user.user_detail in form init and populate_obj)
        form.populate_obj(user.user_detail)

        try:
            # Add user (implicitly adds user_detail via relationship if cascaded, or add explicitly if needed)
            db.session.add(user)
            db.session.commit()
            flash('Public profile updated successfully.', 'success')

            # Redirect if 'next' parameter exists, otherwise stay on profile page
            next_url = form.next.data or request.args.get('next') # Get from form or original args
            if next_url:
                 # Basic validation to prevent open redirect vulnerability
                if url_for('settings.profile') in next_url or next_url.startswith('/'):
                    return redirect(next_url)
                else:
                    current_app.logger.warning(f"Ignoring potentially unsafe next URL: {next_url}")
            # No else needed, will fall through to render_template if no redirect

        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.error(f"Database error updating profile for user {user.id}: {e}")
            flash('Error saving profile to database.', 'error')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Unexpected error updating profile for user {user.id}: {e}", exc_info=True)
            flash('An unexpected error occurred while saving the profile.', 'error')

    # Pass the user object itself to the template
    return render_template('settings/profile.html', user=user, active="profile", form=form)


@settings.route('/password', methods=['GET', 'POST'])
@login_required
def password():
    """Handles user password changes."""
    user = db.session.get(User, current_user.id) # Use ID
    if not user:
        flash("User not found.", "error")
        return redirect(url_for('frontend.index'))

    form = PasswordForm(next=request.args.get('next'))

    if form.validate_on_submit():
        # Password validation (current password check) happens in the form's validate_password
        # Set the new password (hashing is handled by the User model's password property setter)
        try:
            user.password = form.new_password.data
            db.session.add(user)
            db.session.commit()
            flash('Password updated successfully.', 'success')

            # Redirect if 'next' parameter exists, otherwise back to password page
            next_url = form.next.data or request.args.get('next')
            if next_url:
                # Basic validation to prevent open redirect vulnerability
                if url_for('settings.password') in next_url or next_url.startswith('/'):
                    return redirect(next_url)
                else:
                    current_app.logger.warning(f"Ignoring potentially unsafe next URL: {next_url}")
            # Redirect back to password page to clear form after success if no next_url
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
        # Allow viewing basic info even if not Linux

    uptime = _get_system_uptime()
    cpu_temp = _get_cpu_temperature()
    try:
        hostname_val = socket.getfqdn()
    except socket.gaierror:
        hostname_val = socket.gethostname() # Fallback if FQDN fails

    form = None
    network_interfaces = []
    if is_linux and hasnetifaces:
        network_interfaces = _list_network_interfaces()
        if network_interfaces:
            # Filter out loopback and potentially other non-configurable interfaces if needed
            choices = [(i, i) for i in network_interfaces if i != 'lo' and not i.startswith('docker') and not i.startswith('veth')]
            if choices:
                form = EthernetSelectionForm()
                form.ethernet_devices.choices = choices

                if form.validate_on_submit():
                    selected_device = form.ethernet_devices.data
                    # Validate selected_device against available choices again? Maybe not necessary if form validation works.
                    return redirect(url_for('settings.configure_ethernet_device', device=selected_device))
            else:
                flash('No configurable network interfaces found.', 'info')
        else:
             flash('Could not retrieve network interfaces.', 'warning')
    elif is_linux and not hasnetifaces:
        flash('Python module "netifaces" not found. Network configuration is disabled. Install with: pip install netifaces', 'warning')

    return render_template('settings/host.html',
                           hostname=hostname_val,
                           uptime=uptime,
                           cpu_temp=cpu_temp,
                           form=form, # Pass form even if None
                           interfaces=network_interfaces, # Pass interfaces for display
                           is_linux=is_linux, # Pass flag to template
                           active="host settings")


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
        current_hostname = socket.gethostname() # Fallback

    form = HostSettingsForm()

    if not form.is_submitted():
        form.hostname.data = current_hostname

    if form.validate_on_submit():
        new_hostname = form.hostname.data.strip() # Ensure no leading/trailing whitespace

        if not new_hostname:
             flash('Hostname cannot be empty.', 'error')
             return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")

        if new_hostname == current_hostname:
            flash('Hostname is already set to this value.', 'info')
            return redirect(url_for('settings.host'))

        # Basic hostname validation (RFC 1123 subset)
        if not re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$", new_hostname):
             flash('Invalid hostname format. Use letters, numbers, and hyphens (not at start/end).', 'error')
             return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")

        # Check write permissions before attempting changes
        # Use sudo context for permission checks as well, if files require root
        try:
            with sudo:
                hosts_writable = os.access(HOSTS_FILE, os.W_OK)
                hostname_writable = os.access(HOSTNAME_FILE, os.W_OK)
        except Exception as e:
             # This might fail if sudo itself fails (e.g., password needed, user not in sudoers)
             current_app.logger.error(f"Permission check failed using sudo: {e}")
             flash('Could not verify permissions for configuration files. Ensure sudo is configured correctly.', 'error')
             return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")


        if not hosts_writable:
            flash(f'Cannot write to {HOSTS_FILE}. Check permissions or run webapp as user with sudo rights.', 'error')
        if not hostname_writable:
             flash(f'Cannot write to {HOSTNAME_FILE}. Check permissions or run webapp as user with sudo rights.', 'error')

        # Proceed only if permissions seem okay
        if hosts_writable and hostname_writable:
            files_updated = False
            command_success = False
            try:
                # Update config files using sudo
                with sudo:
                    # It's safer to modify files using commands like sed within sudo if possible,
                    # but Python file I/O within sudo context should also work.
                    # We'll stick to the Python approach as per original code for now.
                    hosts_updated = _sethostname(HOSTS_FILE, current_hostname, new_hostname)
                    hostname_updated = _sethostname(HOSTNAME_FILE, current_hostname, new_hostname)
                    files_updated = hosts_updated or hostname_updated # Track if any file was actually changed

                    if not hosts_updated:
                        current_app.logger.warning(f'Old hostname "{current_hostname}" not found in {HOSTS_FILE}.')
                        # Don't flash warning to user unless it's critical
                    if not hostname_updated:
                        current_app.logger.warning(f'Old hostname "{current_hostname}" not found in {HOSTNAME_FILE}.')

                    # Apply hostname change immediately using hostnamectl (preferred) or hostname
                    try:
                        sh.hostnamectl('set-hostname', new_hostname)
                        current_app.logger.info(f"Hostname set to '{new_hostname}' using hostnamectl.")
                        command_success = True
                    except sh.CommandNotFound:
                        current_app.logger.warning("hostnamectl not found, trying hostname command.")
                        try:
                            sh.hostname(new_hostname) # Older command, might need -b flag depending on version
                            current_app.logger.info(f"Hostname set to '{new_hostname}' using hostname command.")
                            command_success = True
                        except sh.CommandNotFound:
                            current_app.logger.error("Neither hostnamectl nor hostname command found.")
                            flash('Error: Could not find system command to set hostname.', 'error')
                        except sh.ErrorReturnCode as e_hostname:
                            current_app.logger.error(f"Error running hostname command: {e_hostname}")
                            flash(f'Error setting hostname via command: {e_hostname.stderr.decode() or e_hostname.stdout.decode()}', 'error')
                    except sh.ErrorReturnCode as e_hostnamectl:
                        current_app.logger.error(f"Error running hostnamectl: {e_hostnamectl}")
                        flash(f'Error setting hostname via command: {e_hostnamectl.stderr.decode() or e_hostnamectl.stdout.decode()}', 'error')

                    # Restart Avahi daemon if present and hostname command succeeded
                    if command_success and hasservice and sh_service:
                        try:
                            sh_service("avahi-daemon", "restart")
                            current_app.logger.info("Restarted avahi-daemon service.")
                        except sh.ErrorReturnCode as e_service:
                            # Log error but don't block success message for hostname change
                            current_app.logger.warning(f"Failed to restart avahi-daemon: {e_service}")
                            flash('Warning: Could not restart mDNS service (avahi-daemon). Network discovery might be delayed.', 'warning')
                        except Exception as e_service_other:
                             current_app.logger.warning(f"Unexpected error restarting avahi-daemon: {e_service_other}")
                             flash('Warning: Unexpected error restarting mDNS service (avahi-daemon).', 'warning')

            except Exception as e_sudo: # Catch potential sudo/permission issues during file writes or commands
                 current_app.logger.error(f'An unexpected error occurred while setting hostname using sudo: {e_sudo}', exc_info=True)
                 flash(f'An unexpected error occurred while setting hostname: {e_sudo}', 'error')
                 command_success = False # Ensure we don't show success message

            # Provide feedback based on results
            if command_success:
                 flash(f'Hostname changed to "{new_hostname}". A reboot might be required for all services to recognize the change.', 'success')
                 return redirect(url_for('settings.host'))
            elif files_updated:
                 # Files were updated but command failed
                 flash(f'Hostname updated in configuration files, but failed to apply change immediately. Please reboot.', 'warning')
                 return redirect(url_for('settings.host'))
            # else: Error already flashed if files weren't writable or command failed without file changes

        # If permissions were bad or command failed, stay on the page
        return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")

    # Render form on GET or if validation fails
    return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")


@settings.route('/get_ethernet_info/<string:device>', methods=['GET']) # Use GET for retrieving info
@login_required
@admin_required
def get_ethernet_info(device):
    """Returns network information for a specific device as JSON."""
    eth_properties = {'device': device}

    if not hasnetifaces:
        current_app.logger.warning("get_ethernet_info called but netifaces is not available.")
        return json.dumps({'error': 'netifaces module not available'}), 501, {'ContentType':'application/json'} # 501 Not Implemented

    try:
        # Validate device name roughly (prevent potential command injection if used elsewhere)
        if not re.match(r'^[a-zA-Z0-9\.\-\_]+$', device):
             return json.dumps({'error': f'Invalid device name format: "{device}".'}), 400, {'ContentType':'application/json'}

        addresses = netifaces.ifaddresses(device)
        gateways = netifaces.gateways()

        # IPv4 Info (usually a list, take the first one)
        ipv4_list = addresses.get(netifaces.AF_INET, [])
        eth_properties['ipv4'] = ipv4_list[0] if ipv4_list else None

        # IPv6 Info (usually a list, take the first one)
        ipv6_list = addresses.get(netifaces.AF_INET6, [])
        eth_properties['ipv6'] = ipv6_list[0] if ipv6_list else None

        # MAC Address (usually a list, take the first one)
        link_list = addresses.get(netifaces.AF_LINK, [])
        eth_properties['mac_address'] = link_list[0]['addr'] if link_list else None

        # Default Gateway (IPv4)
        default_gateways = gateways.get('default', {})
        ipv4_gateway_info = default_gateways.get(netifaces.AF_INET) # Tuple: (address, interface)
        eth_properties['default_gateway'] = ipv4_gateway_info[0] if ipv4_gateway_info else None

    except ValueError:
        # Handle case where the device name is invalid or not found by netifaces
        current_app.logger.info(f'Device "{device}" not found by netifaces.')
        return json.dumps({'error': f'Device "{device}" not found or invalid.'}), 404, {'ContentType':'application/json'}
    except Exception as e:
        current_app.logger.error(f"Error getting info for device {device}: {e}", exc_info=True)
        return json.dumps({'error': f'Error retrieving information for {device}.'}), 500, {'ContentType':'application/json'}

    return json.dumps(eth_properties), 200, {'ContentType':'application/json'}


@settings.route('/reboot', methods=['POST']) # Use POST for actions with side effects
@login_required
@admin_required
def system_reboot():
    """Initiates a system reboot (Linux only)."""
    if platform.system().lower() != 'linux':
        flash('Reboot is only supported on Linux.', 'error')
        return redirect(url_for('settings.host'))

    try:
        with sudo:
            # Ensure filesystem buffers are written before reboot
            sh.sync()
            # Use systemctl (preferred) or reboot command
            try:
                sh.systemctl('reboot', _bg=True) # Run in background so Flask doesn't wait
                current_app.logger.info("System reboot initiated via systemctl.")
            except sh.CommandNotFound:
                current_app.logger.warning("systemctl not found, trying reboot command.")
                try:
                    sh.reboot(_bg=True)
                    current_app.logger.info("System reboot initiated via reboot command.")
                except sh.CommandNotFound:
                    current_app.logger.error("Neither systemctl nor reboot command found.")
                    flash('Error: Could not find system command to reboot.', 'error')
                    return redirect(url_for('settings.host'))

        flash('Reboot command issued successfully. The device will now restart.', 'success')
        # Redirect immediately, don't wait for the command to fully finish
        # Redirect to a page that indicates status or just home
        return redirect(url_for('frontend.index', message='rebooting')) # Pass status hint

    except sh.ErrorReturnCode as e:
        # ErrorReturnCode_143 (SIGTERM) might happen if the web server is killed quickly
        if e.exit_code == 143:
             flash('Reboot initiated. Connection may be lost.', 'info')
             return redirect(url_for('frontend.index', message='rebooting'))
        else:
             current_app.logger.error(f"Error issuing reboot command: {e}")
             flash(f'Error issuing reboot command: {e.stderr.decode() or e.stdout.decode()}', 'error')
             return redirect(url_for('settings.host')) # Stay on host page on error
    except Exception as e: # Catch potential sudo/permission issues
        current_app.logger.error(f'An unexpected error occurred during reboot: {e}', exc_info=True)
        flash(f'An unexpected error occurred during reboot: {e}', 'error')
        return redirect(url_for('settings.host'))


@settings.route('/shutdown', methods=['POST']) # Use POST for actions with side effects
@login_required
@admin_required
def system_shutdown():
    """Initiates a system shutdown (Linux only)."""
    if platform.system().lower() != 'linux':
        flash('Shutdown is only supported on Linux.', 'error')
        return redirect(url_for('settings.host'))

    try:
        with sudo:
            # Ensure filesystem buffers are written
            sh.sync()
            # Use systemctl (preferred) or halt/poweroff command
            try:
                sh.systemctl('poweroff', _bg=True) # Run in background
                current_app.logger.info("System shutdown initiated via systemctl poweroff.")
            except sh.CommandNotFound:
                current_app.logger.warning("systemctl not found, trying halt command.")
                try:
                    # halt might just stop processes, poweroff is usually preferred
                    sh.poweroff(_bg=True)
                    current_app.logger.info("System shutdown initiated via poweroff command.")
                except sh.CommandNotFound:
                     current_app.logger.warning("poweroff not found, trying halt command.")
                     try:
                         sh.halt(_bg=True)
                         current_app.logger.info("System halt initiated via halt command.")
                     except sh.CommandNotFound:
                        current_app.logger.error("Neither systemctl, poweroff, nor halt command found.")
                        flash('Error: Could not find system command to shut down.', 'error')
                        return redirect(url_for('settings.host'))

        flash('Shutdown command issued successfully. The device will now power off.', 'success')
        # Redirect immediately
        return redirect(url_for('frontend.index', message='shutting down')) # Pass status hint

    except sh.ErrorReturnCode as e:
        # ErrorReturnCode_143 (SIGTERM) might happen
        if e.exit_code == 143:
             flash('Shutdown initiated. Connection may be lost.', 'info')
             return redirect(url_for('frontend.index', message='shutting down'))
        else:
             current_app.logger.error(f"Error issuing shutdown command: {e}")
             flash(f'Error issuing shutdown command: {e.stderr.decode() or e.stdout.decode()}', 'error')
             return redirect(url_for('settings.host')) # Stay on host page on error
    except Exception as e: # Catch potential sudo/permission issues
        current_app.logger.error(f'An unexpected error occurred during shutdown: {e}', exc_info=True)
        flash(f'An unexpected error occurred during shutdown: {e}', 'error')
        return redirect(url_for('settings.host'))


@settings.route('/network/<string:device>', methods=['GET', 'POST'])
@login_required
@admin_required
def configure_ethernet_device(device):
    """
    Configures network settings (DHCP/Static) for a specific device (Linux only).
    NOTE: Modifies /etc/network/interfaces, specific to Debian/Ubuntu. Use with caution.
    """
    # --- Pre-checks ---
    if platform.system().lower() != 'linux':
        flash('Network configuration is only supported on Linux.', 'error')
        return redirect(url_for('settings.host'))

    if not hasnetifaces:
        flash('Python module "netifaces" not found. Network configuration is disabled.', 'error')
        return redirect(url_for('settings.host'))

    # Basic device name validation
    if not re.match(r'^[a-zA-Z0-9\.\-\_]+$', device) or device == 'lo':
        flash(f'Invalid or unsupported device name: "{device}".', 'warning')
        return redirect(url_for('settings.host'))

    form = EthernetConfigureForm()
    form.ethernet_device.data = device # Store device name in hidden field

    # --- Check Permissions and Read Config ---
    can_read_config = os.access(NETWORK_FILE, os.R_OK)
    can_write_config = False # Check later using sudo if needed
    try:
        with sudo:
            can_write_config = os.access(NETWORK_FILE, os.W_OK)
    except Exception as e:
        current_app.logger.warning(f"Could not check write permissions for {NETWORK_FILE} using sudo: {e}")
        # Proceed, but POST will fail if writing is needed and permissions are bad

    if not can_read_config:
        flash(f'{NETWORK_FILE} is not readable! Cannot determine or modify current settings.', 'error')
        # Allow viewing page but functionality will be limited
    if not can_write_config:
        flash(f'{NETWORK_FILE} is not writable! Network settings cannot be saved.', 'warning')
        # Allow viewing page, POST will be blocked later if changes are attempted

    device_map = _parse_network_file() if can_read_config else None
    if device_map is None and can_read_config: # Check if readable but parsing failed
         flash(f'Error parsing {NETWORK_FILE}. Cannot reliably determine current settings.', 'warning')
         # Allow proceeding but form might be empty/defaults

    # --- Get Current Settings (from config file first, then netifaces) ---
    current_config_type = 'dhcp' # Default assumption if not found in file
    current_static_ip = ''
    current_static_netmask = ''
    current_static_gateway = ''

    # Try to parse details from the config file block
    config_block = _get_ethernet_properties_from_map(device, device_map) if device_map else None
    if config_block:
        lines = config_block.strip().split('\n')
        first_line = lines[0].strip()
        if ' static' in first_line:
            current_config_type = 'static'
            for line in lines[1:]: # Skip the 'iface...' line
                parts = line.strip().split()
                if len(parts) == 2:
                    if parts[0] == 'address': current_static_ip = parts[1]
                    elif parts[0] == 'netmask': current_static_netmask = parts[1]
                    elif parts[0] == 'gateway': current_static_gateway = parts[1]
                    # Add parsing for dns-nameservers etc. if needed
        elif ' dhcp' in first_line:
            current_config_type = 'dhcp'
        elif ' manual' in first_line:
            current_config_type = 'manual' # Treat manual as unconfigurable here
            flash(f'Device "{device}" is set to manual configuration. Cannot modify via this interface.', 'warning')
        elif ' loopback' in first_line:
             flash('Cannot configure the loopback device.', 'warning')
             return redirect(url_for('settings.host'))
        # else: Unknown type, assume dhcp?
    elif can_read_config:
        # Device not found in config, might be dynamically managed or unconfigured
        flash(f'Device "{device}" not explicitly configured in {NETWORK_FILE}. Assuming DHCP or managed elsewhere.', 'info')

    # Get live IP info from netifaces as fallback/comparison
    live_ip_address = ''
    live_subnet_mask = ''
    live_gateway = ''
    try:
        addresses = netifaces.ifaddresses(device)
        if netifaces.AF_INET in addresses:
            ipv4_info = addresses[netifaces.AF_INET][0] # Assume first IPv4 address is primary
            live_ip_address = ipv4_info.get('addr', '')
            live_subnet_mask = ipv4_info.get('netmask', '')

        gateways = netifaces.gateways()
        if 'default' in gateways and netifaces.AF_INET in gateways['default']:
            # gateways['default'][netifaces.AF_INET] is like ('192.168.1.1', 'eth0')
            live_gateway = gateways['default'][netifaces.AF_INET][0]
    except (ValueError, KeyError, IndexError):
        # ValueError if device not found by netifaces
        flash(f'Could not retrieve live network details for {device}.', 'warning')
    except Exception as e:
        current_app.logger.warning(f"Error getting live netifaces details for {device}: {e}")


    # --- Populate Form (GET request or failed POST) ---
    if not form.is_submitted():
        form.connection_type.data = current_config_type
        # Populate static fields with config data if static, otherwise live data (or empty)
        form.ip_address.data = current_static_ip if current_config_type == 'static' else live_ip_address
        form.netmask.data = current_static_netmask if current_config_type == 'static' else live_subnet_mask
        form.gateway.data = current_static_gateway if current_config_type == 'static' else live_gateway


    # --- Handle Form Submission (POST request) ---
    if form.validate_on_submit():
        if not can_write_config:
             flash(f'{NETWORK_FILE} is not writable! Cannot save changes.', 'error')
             # Re-render template with current data
             return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write_config)

        if current_config_type == 'manual':
             flash(f'Device "{device}" is set to manual configuration. Cannot modify via this interface.', 'error')
             return redirect(url_for('settings.host'))


        new_connection_type = form.connection_type.data
        new_ip = form.ip_address.data.strip()
        new_netmask = form.netmask.data.strip()
        new_gateway = form.gateway.data.strip()

        # --- Modify device_map based on new settings ---
        if device_map is None:
             flash(f"Could not read or parse {NETWORK_FILE}. Cannot save changes.", "error")
             return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write_config)

        new_device_map = list(device_map) # Work on a copy
        iface_index = -1
        auto_index = -1

        # Find existing 'iface' and 'auto' lines/blocks for the device
        for i, block in enumerate(new_device_map):
            lines = block.strip().split('\n')
            if lines:
                # Check if block starts with 'iface <device> inet ...'
                if re.match(rf'^\s*iface\s+{re.escape(device)}\s+inet\s+', lines[0]):
                    iface_index = i
                # Check if block starts with 'auto <device>'
                elif re.match(rf'^\s*auto\s+{re.escape(device)}\s*$', lines[0]):
                    auto_index = i

        # --- Construct the new configuration block ---
        # Ensure newline at the end of the block
        new_iface_block = f"iface {device} inet {new_connection_type}\n"
        if new_connection_type == 'static':
            # Basic validation for static fields if type is static
            if not all([new_ip, new_netmask]): # Gateway is often optional
                 flash('IP Address and Netmask are required for static configuration.', 'error')
                 return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write_config)
            new_iface_block += f"\taddress {new_ip}\n"
            new_iface_block += f"\tnetmask {new_netmask}\n"
            if new_gateway: # Only add gateway if provided
                new_iface_block += f"\tgateway {new_gateway}\n"
            # Add other static options if needed (dns-nameservers, etc.) here

        # --- Update or Add the configuration in the map ---
        if iface_index != -1:
            # Replace existing iface block
            current_app.logger.info(f"Replacing existing iface block for {device} at index {iface_index}")
            new_device_map[iface_index] = new_iface_block
        else:
            # Add new iface block (usually at the end)
            current_app.logger.info(f"Adding new iface block for {device}")
            new_device_map.append(new_iface_block)
            flash(f'Interface "{device}" was not found in the config file. Added new configuration block.', 'info')

        # Ensure 'auto <device>' line exists if not loopback
        auto_block = f"auto {device}\n"
        if auto_index == -1 and device != 'lo':
            current_app.logger.info(f"Adding '{auto_block.strip()}' line.")
            # Try to insert 'auto' before 'iface'
            try:
                # Find the index of the block we just added/modified
                current_iface_index = new_device_map.index(new_iface_block)
                new_device_map.insert(current_iface_index, auto_block)
            except ValueError:
                 # Should not happen, but as fallback, add at beginning or end
                 new_device_map.insert(0, auto_block)
            flash(f'Added "auto {device}" line to configuration.', 'info')
        elif auto_index != -1 and not new_device_map[auto_index].strip().endswith(device):
             # Handle case where auto line exists but might be for multiple devices (e.g. auto eth0 eth1)
             # This simple logic might not cover all cases well.
             current_app.logger.warning(f"Existing 'auto' line at index {auto_index} might be complex. Ensuring '{device}' is present.")
             if device not in new_device_map[auto_index]:
                 new_device_map[auto_index] = new_device_map[auto_index].strip() + f" {device}\n"


        # --- Write the changes and restart networking ---
        write_success = False
        try:
            with sudo:
                write_success = _write_network_file(new_device_map)
        except Exception as e_sudo_write:
             current_app.logger.error(f"Error writing network file using sudo: {e_sudo_write}", exc_info=True)
             flash(f"Error writing network configuration: {e_sudo_write}", 'error')

        if write_success:
            flash('Network configuration updated. Attempting to apply changes...', 'info')
            restart_success = False
            try:
                with sudo:
                    # Use ifdown/ifup which is common for /etc/network/interfaces
                    # Allow exit code 1 for ifdown (interface already down/not configured)
                    # Use _fg=True to wait for commands to finish? Or _bg=False? Default is False (foreground).
                    current_app.logger.info(f"Running 'ifdown {device}'")
                    sh.ifdown(device, _ok_code=[0, 1])
                    current_app.logger.info(f"Running 'ifup {device}'")
                    sh.ifup(device)
                flash(f'Network interface "{device}" restarted successfully.', 'success')
                restart_success = True
                # Redirect back to host page after successful restart
                return redirect(url_for('settings.host'))

            except sh.ErrorReturnCode as e_if:
                err_output = e_if.stderr.decode() or e_if.stdout.decode()
                current_app.logger.error(f"Error applying network changes for '{device}': {e_if}\nOutput:\n{err_output}")
                flash(f'Error applying network changes for "{device}": {err_output}. Please check system logs or restart networking manually.', 'error')
            except sh.CommandNotFound:
                 current_app.logger.error("'ifdown' or 'ifup' command not found.")
                 flash('Error: "ifdown" or "ifup" command not found. Cannot apply changes automatically. Please restart networking manually.', 'error')
            except Exception as e_restart:
                 current_app.logger.error(f'An unexpected error occurred while applying network changes: {e_restart}', exc_info=True)
                 flash(f'An unexpected error occurred while applying network changes: {e_restart}', 'error')

            # If restart failed but write succeeded, redirect to host page with warning
            if not restart_success:
                 flash('Configuration saved, but failed to apply changes automatically. Manual network restart may be required.', 'warning')
                 return redirect(url_for('settings.host'))
        else:
            # Writing failed (error flashed in _write_network_file or sudo exception)
            # Stay on the config page
             pass # Fall through to render template


    # --- Render Template (GET or failed POST/write) ---
    return render_template('settings/configure_ethernet_device.html',
                           form=form,
                           device=device,
                           active="network settings",
                           can_write=can_write_config) # Pass write permission status


@settings.route('/configure_exports', methods=['GET', 'POST'])
@login_required
@admin_required
def configure_exports():
    """Configures automatic settings export (backup) options."""
    form = ExportConfigureForm()

    # Check if system email is configured, needed if email export is enabled
    email_server = Setting.get_by_name('system_email_server', default='').value
    email_configured = bool(email_server) # True if server setting exists and is not empty/None

    if not form.is_submitted():
        # Populate form with current settings from DB
        form.frequency.data = str(Setting.get_by_name('export_frequency', default=DAILY).value) # Ensure string for SelectField
        form.email.data = Setting.get_by_name('export_email_enable', default=True).value
        form.email_address.data = Setting.get_by_name('export_mailer_to', default='').value # Default to empty string
        form.local_file.data = Setting.get_by_name('enable_local_file_storage', default=True).value
        default_path = os.path.join(INSTANCE_FOLDER_PATH, 'exports')
        # Ensure path is absolute and handle empty value
        local_path = Setting.get_by_name('export_local_path', default=default_path).value or default_path
        form.local_file_path.data = os.path.abspath(local_path)
        form.days_to_keep.data = int(Setting.get_by_name('days_to_keep', default=7).value)

    if form.validate_on_submit():
        # --- Form Validation ---
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
                # Check if path is writable (or creatable)
                path_to_check = form.local_file_path.data
                try:
                    # Check if dir exists and is writable, or if parent is writable to create dir
                    if not os.path.exists(path_to_check):
                        parent_dir = os.path.dirname(path_to_check)
                        if not os.access(parent_dir, os.W_OK | os.X_OK):
                             form.local_file_path.errors.append(f"Cannot write to parent directory '{parent_dir}' to create export path.")
                             errors = True
                    elif not os.access(path_to_check, os.W_OK | os.X_OK):
                         form.local_file_path.errors.append("Local file path exists but is not writable.")
                         errors = True
                except Exception as e:
                     form.local_file_path.errors.append(f"Error checking path permissions: {e}")
                     errors = True

        if errors:
             # Re-render form with validation errors
             return render_template('settings/configure_exports.html', form=form, active='advanced', email_configured=email_configured)

        # --- Save Settings ---
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
                # Use helper method to get or create setting
                Setting.set_value(name, value)

            db.session.commit()
            current_app.logger.info("Export settings updated in database.")

            # --- Refresh Exporter Thread ---
            if hasattr(current_app, 'decoder') and hasattr(current_app.decoder, '_exporter_thread') and current_app.decoder._exporter_thread:
                current_app.logger.info("Refreshing exporter thread parameters...")
                current_app.decoder._exporter_thread.prepParams() # Reload settings in the thread
                current_app.logger.info("Exporter thread parameters refreshed.")
            else:
                 current_app.logger.warning("Exporter thread not found, cannot refresh its parameters.")


            flash('Export settings updated successfully.', 'success')
            return redirect(url_for('settings.index'))

        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.error(f"Database error saving export settings: {e}")
            flash('Error saving export settings to database.', 'error')
        except Exception as e:
            # Catch other potential errors (e.g., during prepParams)
            db.session.rollback() # Rollback DB just in case
            current_app.logger.error(f"Error updating export configuration: {e}", exc_info=True)
            flash(f'An unexpected error occurred: {e}', 'error')


    # Render form on GET or if validation fails
    if not email_configured:
        flash('System email is not configured. Email export option will be disabled until system email is set up.', 'warning')

    return render_template('settings/configure_exports.html', form=form, active='advanced', email_configured=email_configured)


@settings.route('/export', methods=['GET']) # Use GET for simple download trigger
@login_required
@admin_required
def export():
    """Triggers an immediate manual export of settings."""
    try:
        current_app.logger.info("Manual settings export triggered.")
        exporter = Exporter()
        exporter.exportSettings() # Creates the export file
        response = exporter.ReturnResponse() # Returns the file as a download response
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

    # --- Helper Functions ---
    def strip_ansi(line):
        """Removes ANSI escape codes from strings (often present in Git output)."""
        if not isinstance(line, str): return line # Return as-is if not string
        # Basic ANSI color codes
        rc_color = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
        line = rc_color.sub('', line)
        # Other potential escape sequences
        rc_other = re.compile(r'\x1B[=><A-Z]')
        line = rc_other.sub('', line)
        return line.strip()

    def build_remotes_list(remote_output_lines):
        """Parses 'git remote -v' output into a list suitable for SelectField choices."""
        remotes = {}
        for line in remote_output_lines:
            parts = line.strip().split()
            if len(parts) == 3:
                name, url, type_str = parts
                type_val = type_str.strip('()')
                if name not in remotes:
                    remotes[name] = {'url': url, 'types': set()}
                remotes[name]['types'].add(type_val)

        # Format for choices: (value, label)
        choices = []
        for name, data in sorted(remotes.items()):
            types_str = ", ".join(sorted(list(data['types'])))
            label = f"{name} - {data['url']} ({types_str})"
            choices.append((name, label))
        return choices

    def get_git_info(repo_path):
        """Gets current branch, remote, branches, and remotes for a repo."""
        info = {'branch': None, 'remote': None, 'branches': [], 'remotes': [], 'error': None, 'is_dirty': False}
        if not os.path.isdir(os.path.join(repo_path, '.git')):
             info['error'] = f"Not a Git repository: {repo_path}"
             return info

        try:
            # Combine stderr/stdout, disable color, set working dir
            git = sh.git.bake(_cwd=repo_path, _err_to_out=True, c='color.status=false')

            # Check for uncommitted changes (porcelain v1 is stable)
            status_porcelain = git.status("--porcelain=v1").stdout.decode('utf-8').strip()
            info['is_dirty'] = bool(status_porcelain) # True if any output exists

            # Get status to find current branch and tracking remote (branch -sb is less stable than symbolic-ref/rev-parse)
            try:
                # Get current branch name
                current_branch = git('symbolic-ref', '--short', 'HEAD').stdout.decode('utf-8').strip()
                info['branch'] = current_branch
            except sh.ErrorReturnCode:
                # Likely detached HEAD state
                try:
                    current_commit = git('rev-parse', '--short', 'HEAD').stdout.decode('utf-8').strip()
                    info['branch'] = f"HEAD (Detached at {current_commit})"
                except sh.ErrorReturnCode:
                    info['branch'] = "HEAD (Unknown state)" # Fallback
                info['remote'] = None # No remote tracking in detached state

            # If not detached, find the tracking remote
            if info['remote'] is None and info['branch'] and not info['branch'].startswith("HEAD"):
                try:
                    # Get remote name for the current branch
                    remote_name = git('config', f'branch.{current_branch}.remote').stdout.decode('utf-8').strip()
                    info['remote'] = remote_name
                except sh.ErrorReturnCode:
                    # No remote configured for this branch
                    info['remote'] = None

            # Get all unique branch names (local and remote, simplified)
            # 'git branch -a' output can be complex, this gets unique names seen locally/remotely
            branch_output = strip_ansi(git.branch("-a", "--no-color").stdout.decode('utf-8')).splitlines()
            unique_branches = set()
            for line in branch_output:
                line = line.strip()
                if line.startswith('*'): line = line[1:].strip() # Remove current branch indicator '*'
                if '->' in line: continue # Skip symbolic refs like HEAD -> main
                if line.startswith('remotes/'):
                    # Extract branch name from remote ref (e.g., remotes/origin/my-feature -> my-feature)
                    parts = line.split('/', 2) # Split max 2 times
                    if len(parts) == 3:
                         unique_branches.add(parts[2]) # Add the part after remote name
                else:
                    unique_branches.add(line) # Local branch name
            info['branches'] = sorted(list(b for b in unique_branches if not b.startswith("HEAD ("))) # Exclude detached HEAD markers


            # Get remotes and their URLs
            remote_output = git.remote("-v").stdout.decode('utf-8').splitlines()
            info['remotes'] = build_remotes_list(remote_output)

        except sh.ErrorReturnCode as e:
            err_msg = e.stdout.decode('utf-8') or e.stderr.decode('utf-8')
            info['error'] = f"Git command failed in {repo_path}: {err_msg}"
            current_app.logger.error(info['error'])
        except FileNotFoundError:
             info['error'] = f"Git command not found or repository path invalid: {repo_path}."
             current_app.logger.error(info['error'])
        except Exception as e:
            info['error'] = f"Unexpected error accessing git repo {repo_path}: {e}"
            current_app.logger.error(info['error'], exc_info=True)

        return info

    # --- Get Repo Paths ---
    cwd_web = os.getcwd()
    # Assume API library is adjacent to the webapp directory
    cwd_api = os.path.normpath(os.path.join(cwd_web, '..', 'alarmdecoder'))

    # --- Gather Git Info ---
    web_info = get_git_info(cwd_web)
    api_info = get_git_info(cwd_api)

    # Handle errors during info gathering - flash but allow page to load if possible
    if web_info['error']:
        flash(f"Error accessing webapp repository ({cwd_web}): {web_info['error']}", 'error')
    if api_info['error']:
        flash(f"Error accessing API repository ({cwd_api}): {api_info['error']}", 'error')


    # --- Setup Form ---
    form = SwitchBranchForm()

    # Populate form choices dynamically, even if errors occurred (might show empty lists)
    form.branches_web.choices = [(b, b) for b in web_info['branches']]
    form.remotes_web.choices = web_info['remotes'] # Already in (value, label) format
    form.branches_api.choices = [(b, b) for b in api_info['branches']]
    form.remotes_api.choices = api_info['remotes'] # Already in (value, label) format

    # --- Handle Form Submission ---
    if form.validate_on_submit():
        target_branch_web = form.branches_web.data
        target_remote_web = form.remotes_web.data
        target_branch_api = form.branches_api.data
        target_remote_api = form.remotes_api.data

        actions_performed = False
        errors_occurred = False

        # --- Process Webapp Repo ---
        # Check for errors and if a change is actually requested
        if not web_info['error'] and (target_branch_web != web_info['branch'] or target_remote_web != web_info['remote']):
            if web_info['is_dirty']:
                 flash(f'Webapp repo ({cwd_web}) has uncommitted changes. Please commit or stash them before switching branches.', 'error')
                 errors_occurred = True
            else:
                actions_performed = True
                current_app.logger.info(f"Attempting to switch webapp: Branch '{web_info['branch']}' -> '{target_branch_web}', Remote '{web_info['remote']}' -> '{target_remote_web}'")
                try:
                    git_web = sh.git.bake(_cwd=cwd_web, _err_to_out=True)
                    # 1. Checkout branch
                    git_web.checkout(target_branch_web)
                    flash(f'Switched webapp to branch: {target_branch_web}', 'success')
                    # 2. Pull from the selected remote and branch
                    #    This assumes the local branch should track the remote branch.
                    #    Setting upstream explicitly might be needed for complex cases:
                    #    git_web.branch('--set-upstream-to', f'{target_remote_web}/{target_branch_web}', target_branch_web)
                    current_app.logger.info(f"Pulling webapp {target_remote_web}/{target_branch_web}")
                    git_web.pull(target_remote_web, target_branch_web)
                    flash(f'Pulled latest changes for webapp from {target_remote_web}/{target_branch_web}', 'success')

                except sh.ErrorReturnCode as e:
                    errors_occurred = True
                    err_msg = strip_ansi(e.stdout.decode('utf-8') or e.stderr.decode('utf-8'))
                    current_app.logger.error(f"Error updating webapp repository: {err_msg}")
                    flash(f'Error updating webapp repository: {err_msg}', 'error')
                except Exception as e:
                     errors_occurred = True
                     current_app.logger.error(f'Unexpected error updating webapp repository: {e}', exc_info=True)
                     flash(f'Unexpected error updating webapp repository: {e}', 'error')


        # --- Process API Repo ---
        if not api_info['error'] and (target_branch_api != api_info['branch'] or target_remote_api != api_info['remote']):
             if api_info['is_dirty']:
                 flash(f'API repo ({cwd_api}) has uncommitted changes. Please commit or stash them before switching branches.', 'error')
                 errors_occurred = True
             else:
                actions_performed = True
                current_app.logger.info(f"Attempting to switch API: Branch '{api_info['branch']}' -> '{target_branch_api}', Remote '{api_info['remote']}' -> '{target_remote_api}'")
                try:
                    git_api = sh.git.bake(_cwd=cwd_api, _err_to_out=True)
                    # 1. Checkout
                    git_api.checkout(target_branch_api)
                    flash(f'Switched API library to branch: {target_branch_api}', 'success')
                    # 2. Pull
                    current_app.logger.info(f"Pulling API {target_remote_api}/{target_branch_api}")
                    git_api.pull(target_remote_api, target_branch_api)
                    flash(f'Pulled latest changes for API library from {target_remote_api}/{target_branch_api}', 'success')

                except sh.ErrorReturnCode as e:
                    errors_occurred = True
                    err_msg = strip_ansi(e.stdout.decode('utf-8') or e.stderr.decode('utf-8'))
                    current_app.logger.error(f"Error updating API repository: {err_msg}")
                    flash(f'Error updating API repository: {err_msg}', 'error')
                except Exception as e:
                     errors_occurred = True
                     current_app.logger.error(f'Unexpected error updating API repository: {e}', exc_info=True)
                     flash(f'Unexpected error updating API repository: {e}', 'error')

        if not actions_performed and not errors_occurred:
             flash('No changes selected for Git branches or remotes.', 'info')

        # Redirect back to the same page to show updated status and clear POST,
        # unless critical errors occurred preventing status refresh.
        return redirect(url_for('settings.switch_branch'))

    # --- Populate Form Defaults (GET request or failed POST) ---
    # Set default values only if info was successfully retrieved and not detached HEAD
    if not web_info['error'] and web_info['branch'] and not web_info['branch'].startswith("HEAD"):
        form.branches_web.default = web_info['branch']
    if not web_info['error'] and web_info['remote']:
        form.remotes_web.default = web_info['remote']

    if not api_info['error'] and api_info['branch'] and not api_info['branch'].startswith("HEAD"):
        form.branches_api.default = api_info['branch']
    if not api_info['error'] and api_info['remote']:
        form.remotes_api.default = api_info['remote']

    form.process() # Apply defaults after setting them

    return render_template('settings/git.html',
                            form=form,
                            # Pass current info to template for display
                            current_branch_web=web_info['branch'],
                            current_remote_web=web_info['remote'],
                            web_is_dirty=web_info['is_dirty'],
                            current_branch_api=api_info['branch'],
                            current_remote_api=api_info['remote'],
                            api_is_dirty=api_info['is_dirty'],
                            web_error=web_info['error'], # Pass errors for display
                            api_error=api_info['error'],
                            active='git') # Add active state for navigation


@settings.route('/import', methods=['GET', 'POST'])
@login_required
@admin_required
def import_backup():
    """Handles importing settings from a previously exported tar.gz archive."""
    form = ImportSettingsForm()

    if form.validate_on_submit():
        import_file = form.import_file.data
        if not import_file or not import_file.filename:
             flash('No file selected for import.', 'error')
             return render_template('settings/import.html', form=form, active="import") # Stay on page

        current_app.logger.info(f"Starting settings import from file: {import_file.filename}")
        try:
            # Read file content into memory (consider streaming for very large files)
            archive_data = import_file.read()
            fileobj = io.BytesIO(archive_data)

            prefix = 'alarmdecoder-export' # Expected root directory in the archive

            with tarfile.open(mode='r:gz', fileobj=fileobj) as tar:
                # Basic validation: Check if the prefix directory exists
                # Get all members starting with prefix/
                prefixed_members = [m for m in tar.getmembers() if m.name.startswith(prefix + '/')]
                if not prefixed_members:
                     # Check if the prefix itself exists as a directory entry
                     try:
                         tar.getmember(prefix)
                         # Prefix directory exists but is empty or contains no files we process
                         raise ValueError(f"No data files found within the archive's '{prefix}' directory.")
                     except KeyError:
                         raise tarfile.ReadError(f"Archive does not contain the expected root folder '{prefix}'.")

                # Process members within the prefix directory that are files
                members_to_process = [m for m in prefixed_members if m.isfile()]

                if not members_to_process:
                     raise ValueError("No valid data files found within the archive's export directory.")

                # Import data model by model
                imported_models = []
                for member in members_to_process:
                    filename = os.path.basename(member.name)
                    if filename in EXPORT_MAP:
                        model_class = EXPORT_MAP[filename]
                        current_app.logger.info(f"Processing import for {model_class.__name__} from {filename}...")
                        _import_model(tar, member, model_class)
                        imported_models.append(model_class.__name__)
                    else:
                        current_app.logger.warning(f"Skipping unknown file in archive: {member.name}")

                # Commit all changes after successful import staging of all models
                current_app.logger.info(f"Committing imported data for models: {', '.join(imported_models)}")
                db.session.commit()
                current_app.logger.info('Database import successful.')

                # Refresh application state (ser2sock, decoder, etc.)
                _import_refresh()
                current_app.logger.info('Application state refreshed after import.')

                flash('Settings imported successfully.', 'success')
                return redirect(url_for('frontend.index')) # Redirect to dashboard

        except (tarfile.ReadError, KeyError, ValueError) as err:
            db.session.rollback() # Rollback any partial DB changes
            current_app.logger.error(f'Import Error: {err}', exc_info=True)
            flash(f'Import Failed: {err}', 'error')
        except SQLAlchemyError as err:
            db.session.rollback()
            current_app.logger.error(f'Database error during import commit: {err}', exc_info=True)
            flash(f'Import failed due to database error during final commit: {err}', 'error')
        except Exception as err: # Catch unexpected errors during file processing or refresh
            db.session.rollback()
            current_app.logger.error(f'Unexpected error during import process: {err}', exc_info=True)
            flash(f'An unexpected error occurred during import: {err}', 'error')


    # Render template for GET or failed POST
    use_ssl = Setting.get_by_name('use_ssl', default=False).value
    return render_template('settings/import.html', form=form, ssl=use_ssl, active="import")


@settings.route('/diagnostics', methods=['GET'])
@login_required
@admin_required
def system_diagnostics():
    """Displays diagnostic information about the connected AlarmDecoder device."""
    device_settings = {}
    flags_description = {} # To hold descriptions for flags

    if hasattr(current_app, 'decoder') and current_app.decoder and hasattr(current_app.decoder, 'device') and current_app.decoder.device:
        device = current_app.decoder.device
        current_app.logger.debug("Retrieving device diagnostics...")
        try:
            # Use getattr for safe access to potentially missing attributes
            device_settings['address'] = getattr(device, 'address', 'N/A')
            # Ensure configbits/mask are ints before formatting
            configbits = getattr(device, 'configbits', 0)
            address_mask = getattr(device, 'address_mask', 0)
            device_settings['configbits'] = f"0x{configbits:04X}" if isinstance(configbits, int) else 'N/A'
            device_settings['address_mask'] = f"0x{address_mask:04X}" if isinstance(address_mask, int) else 'N/A'
            device_settings['firmware_version'] = getattr(device, 'version_number', 'N/A')
            serial_num = getattr(device, 'serial_number', 'N/A')
            device_settings['serial_number'] = serial_num.upper() if isinstance(serial_num, str) else 'N/A'

            # Mode
            mode_val = getattr(device, 'mode', None)
            if mode_val == ADEMCO: device_settings['mode'] = "ADEMCO/Honeywell"
            elif mode_val == DSC: device_settings['mode'] = "DSC"
            else: device_settings['mode'] = f"Unknown/Other ({mode_val})"

            # Emulation Settings (convert to Yes/No for clarity)
            device_settings['emulate_zone'] = "Yes" if getattr(device, 'emulate_zone', False) else "No"
            device_settings['emulate_relay'] = "Yes" if getattr(device, 'emulate_relay', False) else "No"
            device_settings['emulate_lrr'] = "Yes" if getattr(device, 'emulate_lrr', False) else "No"

            # Other Settings
            device_settings['deduplicate'] = "Yes" if getattr(device, 'deduplicate', False) else "No"

            # Flags (interpret them if possible - needs documentation)
            flags = getattr(device, 'version_flags', 0)
            if isinstance(flags, int):
                device_settings['flags_raw'] = f"0x{flags:04X}"
                # Example flag interpretation (replace with actual meanings)
                # flags_description['Bit 0 (0x0001): Feature X'] = "Enabled" if flags & 0x0001 else "Disabled"
                # flags_description['Bit 1 (0x0002): Mode Y'] = "Active" if flags & 0x0002 else "Inactive"
                # ... add more descriptions based on AlarmDecoder documentation ...
                if not flags_description: # Add placeholder if no flags are documented yet
                     flags_description['Info'] = "Flag meanings depend on firmware version."
            else:
                 device_settings['flags_raw'] = 'N/A'


            current_app.logger.debug("Device diagnostics retrieved successfully.")

        except Exception as e:
            current_app.logger.error(f"Error retrieving device diagnostics: {e}", exc_info=True)
            flash("Error retrieving some device details.", "warning")
            device_settings['error'] = str(e)
    else:
        current_app.logger.warning("Device diagnostics requested but device not connected or initialized.")
        flash("AlarmDecoder device not connected or initialized.", "warning")
        device_settings['error'] = "Device not available"


    return render_template('settings/diagnostics.html',
                           settings=device_settings,
                           flags_description=flags_description,
                           active="diagnostics")


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

    # Get currently loaded modules (top-level names) for quick check
    # Note: This doesn't guarantee the module is fully functional, just that it was loaded.
    loaded_modules = set(m.split('.')[0] for m in sys.modules.keys())

    # Check status of predefined known/required modules
    for module_name in sorted(KNOWN_MODULES):
        found = False
        import_error = None
        version = None

        # Try importing using importlib for a more definitive check and version access
        try:
            module = importlib.import_module(module_name)
            found = True
            # Try to get version number
            version = getattr(module, '__version__', None) or \
                      getattr(module, 'VERSION', None) or \
                      getattr(module, 'version', None)
            if isinstance(version, tuple): # Handle tuple versions (e.g., Flask)
                version = '.'.join(map(str, version))

        except ImportError:
            found = False
            import_error = "Not found or not installed"
            # Check if it was partially loaded before failing (less common)
            if module_name in loaded_modules:
                 import_error += " (but was previously loaded?)"
        except Exception as e: # Catch other potential import issues
            found = False
            import_error = f"Unexpected error during import attempt: {e}"
            current_app.logger.warning(f"Unexpected error importing {module_name}: {e}", exc_info=True)


        imported_status[module_name] = {
            'modname': module_name,
            'found': found,
            'version': version if found else None,
            'error': import_error
        }
        current_app.logger.debug(f"Module {module_name}: Found={found}, Version={version}, Error={import_error}")

    return json.dumps(imported_status), 200, {'ContentType':'application/json'}


@settings.route('/disable_forward', methods=['POST']) # Use POST for actions
@login_required
@admin_required
def disable_forwarding():
    """Disables the currently configured UPNP port forward."""
    if not has_upnp:
        flash('UPnP library (miniupnpc) not installed. Cannot manage port forwarding.', 'error')
        return redirect(url_for('settings.index'))

    # Get the port that was supposedly forwarded
    external_port_setting = Setting.get_by_name('upnp_external_port')
    external_port_str = external_port_setting.value if external_port_setting else None

    if not external_port_str:
        flash('No active port forward configured in settings to disable.', 'info')
        return redirect(url_for('settings.index'))

    try:
        port_to_remove = int(external_port_str) # Ensure it's an integer
        current_app.logger.info(f"Attempting to remove UPnP port forward for external port {port_to_remove}")
        upnp = UPNP(current_app.decoder) # Initialize UPNP helper

        if upnp.removePortForward(port_to_remove):
             flash(f'Successfully requested removal of port forward for external port {port_to_remove}.', 'success')
             current_app.logger.info(f"UPnP removal request successful for port {port_to_remove}.")
        else:
             # removePortForward might return False if the rule didn't exist on the router or failed
             flash(f'Could not remove port forward for external port {port_to_remove} via UPnP. It might have been removed already, failed, or UPnP is disabled on the router.', 'warning')
             current_app.logger.warning(f"UPnP removal request failed or rule not found for port {port_to_remove}.")

        # Clear the settings in the database regardless of UPnP result,
        # as the user's intent is to disable it in the app.
        internal_port_setting = Setting.get_by_name('upnp_internal_port')
        if internal_port_setting:
            internal_port_setting.value = None # Use None or empty string consistently
            db.session.add(internal_port_setting)
        if external_port_setting:
            external_port_setting.value = None
            db.session.add(external_port_setting)
        db.session.commit()
        current_app.logger.info("Cleared UPnP port forward settings in database.")

    except ValueError:
         flash(f'Invalid port number stored in settings: "{external_port_str}". Cannot remove forward.', 'error')
         current_app.logger.error(f"Invalid UPnP port value in settings: {external_port_str}")
    except ImportError: # Should be caught earlier, but double-check
         flash('UPnP library (miniupnpc) not installed.', 'error')
    except Exception as ex:
        # Catch potential errors from miniupnpc or UPNP class
        db.session.rollback() # Rollback DB changes on unexpected error
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
        # Display page but indicate feature is unavailable
        return render_template('settings/port_forward.html', form=None, upnp_unavailable=True, active="port_forward")

    form = UPNPForm()
    upnp_helper = None
    internal_ip = "Detection failed"
    try:
        upnp_helper = UPNP(current_app.decoder) # Initialize UPNP helper early for checks
        internal_ip = upnp_helper.discover() or "Could not detect via UPnP" # Get local IP detected by UPNP helper
    except Exception as e:
        current_app.logger.error(f"Failed to initialize UPnP helper: {e}", exc_info=True)
        flash(f"Error initializing UPnP: {e}", "error")
        # Allow page load but functionality might fail

    external_ip = get_external_ip() or "Could not detect" # Get public IP

    # Get current settings from DB
    current_internal_port_setting = Setting.get_by_name('upnp_internal_port')
    current_external_port_setting = Setting.get_by_name('upnp_external_port')
    # Use .value directly, default handled by get_by_name if needed (though None is better here)
    current_internal_port = current_internal_port_setting.value if current_internal_port_setting else None
    current_external_port = current_external_port_setting.value if current_external_port_setting else None

    if not form.is_submitted():
        # Populate with current DB values or defaults
        form.internal_port.data = int(current_internal_port) if current_internal_port else 443 # Default internal HTTPS port
        # Suggest a random high port for external to avoid common conflicts
        form.external_port.data = int(current_external_port) if current_external_port else random.randint(10000, 60000)

    if form.validate_on_submit():
        target_internal_port = form.internal_port.data
        target_external_port = form.external_port.data

        if not upnp_helper:
             flash("UPnP helper could not be initialized. Cannot perform action.", "error")
        else:
            try:
                # 1. Remove any existing forward managed by this app
                if current_external_port:
                    try:
                        current_external_port_int = int(current_external_port)
                        current_app.logger.info(f"Removing existing port forward for {current_external_port_int} before adding new one.")
                        upnp_helper.removePortForward(current_external_port_int)
                        # Ignore result of removal, proceed to add new one
                    except ValueError:
                         current_app.logger.warning(f"Invalid existing external port value '{current_external_port}', skipping removal.")
                    except Exception as e_rem:
                         current_app.logger.warning(f"Error removing existing port forward {current_external_port}: {e_rem}")
                         # Continue anyway, maybe the rule doesn't exist

                # 2. Add the new port forward
                current_app.logger.info(f"Attempting to add port forward: External {target_external_port} -> Internal {target_internal_port} for IP {internal_ip}")
                add_success = upnp_helper.addPortForward(target_internal_port, target_external_port)

                if add_success:
                    flash(f'Successfully requested UPnP port forward: External {target_external_port} -> Internal {target_internal_port} ({internal_ip})', 'success')
                    current_app.logger.info("UPnP add request successful.")

                    # 3. Save the new settings to DB
                    Setting.set_value('upnp_internal_port', target_internal_port)
                    Setting.set_value('upnp_external_port', target_external_port)
                    db.session.commit()
                    current_app.logger.info("Saved new UPnP ports to database.")

                    # Redirect after successful setup
                    return redirect(url_for('settings.index'))
                else:
                    # addPortForward returned False
                    flash(f'Failed to add port forward via UPnP. Check router UPnP settings and logs. Ensure port {target_external_port} is not already in use.', 'error')
                    current_app.logger.error("UPnP addPortForward returned false.")
                    # Stay on the page, don't save to DB if router failed

            except ValueError:
                 flash('Invalid port number entered.', 'error')
            except ImportError: # Should be caught earlier
                 flash('UPnP library (miniupnpc) not installed.', 'error')
            except Exception as ex:
                db.session.rollback() # Rollback DB changes on error
                current_app.logger.error(f"Error setting up port forwarding: {ex}", exc_info=True)
                flash(f'Error setting up port forwarding: {ex}', 'error')
                # Stay on the page

    # Render template for GET or failed POST
    return render_template('settings/port_forward.html',
                           form=form,
                           current_internal_port=current_internal_port,
                           current_external_port=current_external_port,
                           internal_ip=internal_ip,
                           external_ip=external_ip,
                           upnp_unavailable=False, # Already checked has_upnp
                           active="port_forward")


@settings.route('/configure_updater', methods=['GET', 'POST'])
@login_required
@admin_required
def configure_updater():
    """Configures the automatic version checking settings."""
    form = VersionCheckerForm()
    last_check_time_setting = Setting.get_by_name('version_checker_last_check_time', default=None)
    last_check_timestamp = None
    if last_check_time_setting and last_check_time_setting.value:
        try:
            last_check_timestamp = float(last_check_time_setting.value)
        except (ValueError, TypeError):
            current_app.logger.warning(f"Invalid value for version_checker_last_check_time: {last_check_time_setting.value}")

    if not form.is_submitted():
        # Use get_value helper for defaults and type safety
        form.version_checker_timeout.data = Setting.get_value('version_checker_timeout', default=3600, type_func=int) # Default 1 hour
        form.version_checker_disable.data = Setting.get_value('version_checker_disable', default=False, type_func=bool)

    if form.validate_on_submit():
        timeout = form.version_checker_timeout.data # Already int from form field
        disable = form.version_checker_disable.data # Already bool from form field

        try:
            # Update settings in DB using helper
            Setting.set_value('version_checker_timeout', timeout)
            Setting.set_value('version_checker_disable', disable)
            db.session.commit()
            current_app.logger.info(f"Update checker settings saved: Timeout={timeout}, Disabled={disable}")

            # Update running thread
            if hasattr(current_app, 'decoder') and hasattr(current_app.decoder, '_version_thread') and current_app.decoder._version_thread:
                current_app.logger.info("Updating version checker thread parameters...")
                current_app.decoder._version_thread.setTimeout(timeout)
                current_app.decoder._version_thread.setDisable(disable)
                current_app.logger.info("Version checker thread parameters updated.")
            else:
                current_app.logger.warning("Version checker thread not found, cannot update its parameters.")


            flash('Update checker settings updated.', 'success')
            return redirect(url_for('settings.index'))

        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.error(f"Database error saving update checker settings: {e}")
            flash('Error saving update checker settings to database.', 'error')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating update checker configuration: {e}", exc_info=True)
            flash(f'An unexpected error occurred: {e}', 'error')


    # Format last check time for display
    last_check_str = "Never"
    if last_check_timestamp:
        try:
            last_check_dt = datetime.fromtimestamp(last_check_timestamp)
            # Format consistently, e.g., ISO 8601 like
            last_check_str = last_check_dt.strftime('%Y-%m-%d %H:%M:%S')
        except (ValueError, OSError) as e: # Catch potential timestamp errors
            last_check_str = "Invalid date stored"
            current_app.logger.error(f"Error formatting last check timestamp {last_check_timestamp}: {e}")


    return render_template('settings/updater_config.html',
                           active="advanced",
                           form=form,
                           last_check=last_check_str)


@settings.route('/configure_system_email', methods=['GET', 'POST'])
@login_required
@admin_required
def configure_system_email():
    """Configures system-wide email settings used for notifications and exports."""
    form = EmailConfigureForm()

    if not form.is_submitted():
        # Populate form with current settings from DB or defaults using helper
        form.mail_server.data = Setting.get_value('system_email_server', default='localhost')
        form.port.data = Setting.get_value('system_email_port', default=25, type_func=int)
        form.tls.data = Setting.get_value('system_email_tls', default=False, type_func=bool)
        form.auth_required.data = Setting.get_value('system_email_auth', default=False, type_func=bool)
        form.username.data = Setting.get_value('system_email_username', default='')
        # Password field should generally be empty by default for security
        form.password.data = '' # Don't fetch stored password to display
        form.default_sender.data = Setting.get_value('system_email_from', default='alarmdecoder@localhost')

    if form.validate_on_submit():
        # Basic validation: require username/password if auth is checked
        if form.auth_required.data and (not form.username.data or not form.password.data):
             # Check password field specifically, as username might be pre-filled
             if not form.password.data:
                 form.password.errors.append("Password is required when SMTP authentication is enabled.")
             if not form.username.data:
                  form.username.errors.append("Username is required when SMTP authentication is enabled.")
             flash('Username and Password are required when SMTP authentication is enabled.', 'error')
             # Re-render form with validation error
             return render_template('settings/system_email.html', active="advanced", form=form)

        # --- Save Settings ---
        try:
            Setting.set_value('system_email_server', form.mail_server.data.strip())
            Setting.set_value('system_email_port', form.port.data)
            Setting.set_value('system_email_tls', form.tls.data)
            Setting.set_value('system_email_auth', form.auth_required.data)
            Setting.set_value('system_email_username', form.username.data.strip())
            # Only update password if a new one is provided in the form
            if form.password.data:
                Setting.set_value('system_email_password', form.password.data)
                current_app.logger.info("System email password updated.")
            else:
                 current_app.logger.info("System email password not changed (field was empty).")
            Setting.set_value('system_email_from', form.default_sender.data.strip())

            db.session.commit()
            current_app.logger.info("System email settings updated in database.")

            # TODO: Update Flask-Mail configuration dynamically if used
            # This depends heavily on how Flask-Mail (or other mail library) is initialized.
            # Example (if config is read dynamically):
            # current_app.config['MAIL_SERVER'] = form.mail_server.data.strip()
            # ... update other MAIL_ settings ...
            # Potentially re-initialize mail extension if needed: mail.init_app(current_app)
            # For now, assume restart is needed or config is read on demand.
            flash('System Email settings updated successfully. Application may need restart for changes to take full effect.', 'success')
            return redirect(url_for('settings.index'))

        except SQLAlchemyError as e:
            db.session.rollback()
            current_app.logger.error(f"Database error saving system email settings: {e}")
            flash('Error saving system email settings to database.', 'error')
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error updating system email configuration: {e}", exc_info=True)
            flash(f'An unexpected error occurred: {e}', 'error')

    # Render form on GET or if validation fails
    return render_template('settings/system_email.html', active="advanced", form=form)


# --- Deprecated/Unused Code ---
# The Python source parsing code (_find_imports, ImportVisitor, etc.) seems unused
# and relies on the deprecated 'compiler' module. Removing it.

# def pyfiles(startPath): ...
# class ImportVisitor(object): ... # Relied on deprecated compiler module
# class ImportWalker(ASTVisitor): ... # Relied on deprecated compiler module
# def parse_python_source(fn): ... # Relied on deprecated compiler module
