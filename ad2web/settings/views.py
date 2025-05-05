# ad2web/settings/views.py
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
import subprocess # Added for running external commands
import shlex # Added for safely splitting command strings if needed
import errno
# Third-party imports
from flask import Blueprint, render_template, current_app, request, flash, Response, url_for, redirect
from flask_login import login_required, current_user
# Removed: import sh
import six
import urllib.request
import urllib.parse
import urllib.error
from sqlalchemy.exc import SQLAlchemyError

# --- Platform Detection ---
IS_WINDOWS = platform.system().lower() == 'windows'
IS_LINUX = platform.system().lower() == 'linux'

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

# Conditional sh.service import (only relevant for Linux)
hasservice = False
sh_service = None
if IS_LINUX:
    try:
        # Use sh.contrib.service if available, otherwise fallback to sh.service
        try:
            from sh.contrib import service as sh_service_linux
            sh_service = sh_service_linux
            hasservice = True
        except ImportError:
            try:
                from sh import service as sh_service_linux
                sh_service = sh_service_linux
                hasservice = True
            except ImportError:
                current_app.logger.warning("Could not import sh.service or sh.contrib.service. Service control disabled.")
                pass # Keep hasservice False
    except ImportError:
         current_app.logger.warning("Could not import 'sh' library. Linux-specific commands requiring it will be disabled.")
         pass # Keep hasservice False


# Local application imports
from alarmdecoder.panels import ADEMCO, DSC
from ..extensions import db
from ..user import User, UserDetail
from ..utils import allowed_file, make_dir, tar_add_directory, tar_add_textfile, INSTANCE_FOLDER_PATH

from ..settings import Setting
from .forms import (ProfileForm, PasswordForm, ImportSettingsForm, HostSettingsForm,
                    EthernetSelectionForm, EthernetConfigureForm, SwitchBranchForm,
                    EmailConfigureForm, UPNPForm, VersionCheckerForm, ExportConfigureForm)
from .constants import EXPORT_MAP, HOSTS_FILE, HOSTNAME_FILE, NETWORK_FILE, KNOWN_MODULES, DAILY, IP_CHECK_SERVER_URL
from ..certificate import Certificate, CA, SERVER
from ..ser2sock import ser2sock
from ..upnp import UPNP
from ..exporter import Exporter
# Removed: from sh import sudo

# Blueprint Configuration
settings = Blueprint('settings', __name__, url_prefix='/settings')

# --- Helper Functions ---

def _run_command(command_list, cwd=None, capture_output=True, check=False, error_msg_prefix="Command failed"):
    """Runs a command using subprocess, handling Windows/Linux differences."""
    try:
        current_app.logger.debug(f"Running command: {' '.join(command_list)} {'in '+cwd if cwd else ''}")
        result = subprocess.run(
            command_list,
            cwd=cwd,
            capture_output=capture_output,
            text=True, # Decode stdout/stderr as text
            check=check, # Raise CalledProcessError if return code is non-zero
            shell=IS_WINDOWS # Use shell=True cautiously on Windows if needed, avoid on Linux
                               # For basic commands like 'git' or 'shutdown', shell=False is safer.
                               # Let's stick with False unless proven necessary.
        )
        current_app.logger.debug(f"Command finished with code {result.returncode}")
        # Log stdout/stderr for debugging if needed
        # if result.stdout: current_app.logger.debug(f"Stdout: {result.stdout.strip()}")
        # if result.stderr: current_app.logger.debug(f"Stderr: {result.stderr.strip()}")
        return result
    except FileNotFoundError:
        current_app.logger.error(f"Error: Command '{command_list[0]}' not found.")
        raise FileNotFoundError(f"Command '{command_list[0]}' not found. Is it installed and in the system PATH?")
    except subprocess.CalledProcessError as e:
        current_app.logger.error(f"{error_msg_prefix}: {e}")
        current_app.logger.error(f"Stderr: {e.stderr.strip() if e.stderr else 'N/A'}")
        current_app.logger.error(f"Stdout: {e.stdout.strip() if e.stdout else 'N/A'}")
        raise # Re-raise the exception to be handled by the caller
    except Exception as e:
        current_app.logger.error(f"Unexpected error running command '{' '.join(command_list)}': {e}", exc_info=True)
        raise # Re-raise

def _get_system_uptime():
    """Retrieves the system uptime."""
    if IS_LINUX:
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                uptime_string = str(timedelta(seconds=uptime_seconds)).split('.')[0]
        except (IOError, IndexError, ValueError, FileNotFoundError) as e:
            current_app.logger.warning(f"Could not read system uptime from /proc/uptime: {e}")
            uptime_string = "N/A"
    elif IS_WINDOWS:
        try:
            # Use WMI via subprocess (less ideal than using a library like psutil or pywin32, but avoids new deps)
            # This command gets the LastBootUpTime
            # Example WMI output: LastBootUpTime=20231027100000.123456+060
            cmd = ['wmic', 'os', 'get', 'LastBootUpTime', '/VALUE']
            result = _run_command(cmd)
            if result.returncode == 0 and result.stdout:
                match = re.search(r"LastBootUpTime=(\d{14})\.\d+([+-]\d+)", result.stdout)
                if match:
                    boot_time_str = match.group(1)
                    # WMI time format: YYYYMMDDHHMMSS
                    boot_time = datetime.strptime(boot_time_str, "%Y%m%d%H%M%S")
                    # Note: This doesn't account for the timezone offset from WMI easily without more complex parsing.
                    # It calculates uptime based on the system's current timezone understanding of the boot time.
                    uptime_delta = datetime.now() - boot_time
                    uptime_string = str(uptime_delta).split('.')[0] # Remove microseconds
                else:
                    current_app.logger.warning("Could not parse LastBootUpTime from WMI output.")
                    uptime_string = "N/A (Parse failed)"
            else:
                 current_app.logger.warning(f"WMI command failed or returned no output. Code: {result.returncode}")
                 uptime_string = "N/A (WMI failed)"

        except (FileNotFoundError, subprocess.CalledProcessError, ValueError, Exception) as e:
            current_app.logger.warning(f"Could not get Windows uptime via WMI: {e}")
            uptime_string = "N/A"
            # Consider adding psutil as a dependency for a robust cross-platform solution:
            # import psutil
            # boot_time_timestamp = psutil.boot_time()
            # uptime_seconds = time.time() - boot_time_timestamp
            # uptime_string = str(timedelta(seconds=uptime_seconds)).split('.')[0]
    else:
        uptime_string = "Unsupported OS"
    return uptime_string

def _get_cpu_temperature():
    """Retrieves the CPU temperature."""
    if IS_LINUX:
        # Common paths for CPU temperature on Linux
        temp_paths = [
            '/sys/class/thermal/thermal_zone0/temp',
            '/sys/class/hwmon/hwmon0/temp1_input',
            '/sys/class/hwmon/hwmon1/temp1_input',
        ]
        for temp_file in temp_paths:
            if os.path.isfile(temp_file):
                try:
                    with open(temp_file, 'r') as f:
                        temp_reading = float(f.readline().strip())
                        if temp_reading > 1000: # Check for millidegrees
                            cpu_temp_celsius = temp_reading / 1000.0
                        else:
                            cpu_temp_celsius = temp_reading
                        return f"{cpu_temp_celsius:.1f} °C"
                except (IOError, ValueError, FileNotFoundError) as e:
                    current_app.logger.warning(f"Error reading temperature from {temp_file}: {e}")
                    continue
        return 'Not Available' # If no Linux path worked
    elif IS_WINDOWS:
        # Getting CPU temp on Windows is complex and often requires external libraries (like OpenHardwareMonitor via WMI or python libs)
        # Using psutil (if installed) would be the cleanest way:
        # try:
        #     import psutil
        #     temps = psutil.sensors_temperatures()
        #     # Find the core temp, structure varies
        #     for name, entries in temps.items():
        #         if 'coretemp' in name.lower() or 'cpu' in name.lower(): # Heuristics
        #              for entry in entries:
        #                  if entry.label and ('core' in entry.label.lower() or 'package' in entry.label.lower()):
        #                       return f"{entry.current:.1f} °C"
        #              # Fallback to first entry if no specific label found
        #              if entries: return f"{entries[0].current:.1f} °C"
        #     return 'Not Available (psutil)'
        # except ImportError:
        #     return 'Not Supported (psutil not installed)'
        # except Exception as e:
        #      current_app.logger.warning(f"Error getting temp via psutil: {e}")
        #      return 'Not Available (Error)'

        # Fallback: Indicate not supported without extra libraries
        return 'Not Supported on Windows (Requires external libraries like psutil or OpenHardwareMonitor)'
    else:
        return 'Unsupported OS'

def _list_network_interfaces():
    """Lists available network interfaces using netifaces."""
    # netifaces works on Windows too
    if hasnetifaces:
        try:
            # Filter out loopback and potentially other non-physical interfaces
            interfaces = netifaces.interfaces()
            # Basic filtering might need adjustment based on common Windows interface names
            # (e.g., 'Loopback Pseudo-Interface 1')
            filtered_interfaces = [
                i for i in interfaces
                if not i.lower().startswith('loopback')
                and not i.lower().startswith('isatap')
                # Add other prefixes to filter if needed
            ]
            return filtered_interfaces
        except Exception as e:
            current_app.logger.error(f"Error listing network interfaces: {e}")
            return []
    return [] # Return empty list if netifaces not available

# --- Linux-Specific Network File Handling ---
# These functions are tightly coupled to /etc/network/interfaces and should only run on Linux.

def _parse_network_file():
    """
    Parses the Linux network configuration file (/etc/network/interfaces).
    Returns a list of configuration blocks. ONLY FOR LINUX.
    """
    if not IS_LINUX: return None # Do not run on non-Linux

    if not os.path.exists(NETWORK_FILE):
        current_app.logger.warning(f"Linux network file not found: {NETWORK_FILE}")
        return None
    try:
        with open(NETWORK_FILE, 'r') as f:
            text = f.read()
        indexes = [s.start() for s in re.finditer(r'^\s*(auto|iface|source|mapping|allow-|wpa-)', text, re.MULTILINE)]
        if not indexes:
            return [text] if text.strip() else []

        result = [text[indexes[i]:indexes[i+1]] for i in range(len(indexes)-1)] + [text[indexes[-1]:]]
        return result
    except IOError as e:
        current_app.logger.error(f"Could not read Linux network file {NETWORK_FILE}: {e}")
        return None
    except Exception as e:
        current_app.logger.error(f"Error parsing Linux network file {NETWORK_FILE}: {e}")
        return None

def _write_network_file(device_map):
    """Writes the modified network configuration back to the file. ONLY FOR LINUX."""
    if not IS_LINUX:
        current_app.logger.error("Attempted to call _write_network_file on non-Linux system.")
        return False

    if device_map is None:
        current_app.logger.error("Attempted to write None to Linux network file.")
        return False
    try:
        # Ensure the directory exists (though /etc/network usually does)
        os.makedirs(os.path.dirname(NETWORK_FILE), exist_ok=True)
        text = ''.join(device_map)
        # IMPORTANT: Writing system files usually requires root privileges.
        # This will likely fail unless the web server is run as root (not recommended)
        # or specific permissions are granted, or it's called via a helper with sudo.
        # Since we removed `sh.sudo`, this write will probably fail in practice.
        # The route using this should check permissions and warn.
        with open(NETWORK_FILE, 'w') as f:
            f.write(text)
        current_app.logger.info(f"Successfully wrote network configuration to {NETWORK_FILE}")
        return True
    except IOError as e:
        # Check for permission denied specifically
        if e.errno == errno.EACCES: # Requires 'import errno'
             current_app.logger.error(f"Permission denied writing to Linux network file {NETWORK_FILE}. Requires root privileges.", exc_info=True)
             flash(f'Permission denied writing to {NETWORK_FILE}. This action requires administrator/root privileges.', 'error')
        else:
             current_app.logger.error(f"Could not write to Linux network file {NETWORK_FILE}: {e}", exc_info=True)
             flash(f'Error writing network configuration: {e}', 'error')
        return False
    except Exception as e:
        current_app.logger.error(f"Unexpected error writing Linux network file {NETWORK_FILE}: {e}", exc_info=True)
        flash(f'Unexpected error writing network configuration: {e}', 'error')
        return False

def _get_ethernet_properties_from_map(device, device_map):
    """Extracts configuration block for a specific network device from the parsed map. ONLY FOR LINUX."""
    if not IS_LINUX or device_map is None: return None

    for block in device_map:
        lines = block.strip().split('\n')
        if lines and re.match(rf'^\s*iface\s+{re.escape(device)}\s+inet\s+', lines[0]):
            return block
    return None

# --- Hostname Setting (Linux Specific Implementation) ---

def _sethostname(config_file, old_hostname, new_hostname):
    """Updates the hostname in a given configuration file. ONLY FOR LINUX."""
    if not IS_LINUX:
        current_app.logger.error(f"Attempted to call _sethostname on non-Linux system for file {config_file}.")
        return False

    if not os.path.exists(config_file):
        current_app.logger.warning(f"Linux hostname config file not found: {config_file}")
        return False
    try:
        # Check write permission before reading (more relevant now without sudo)
        if not os.access(config_file, os.W_OK):
             current_app.logger.error(f"Permission denied writing to {config_file}. Requires root privileges.")
             flash(f'Permission denied writing to {config_file}. Requires administrator/root privileges.', 'error')
             return False

        with open(config_file, 'r') as f:
            content = f.read()

        new_content = content.replace(old_hostname, new_hostname)

        if new_content != content:
            with open(config_file, 'w') as f:
                f.write(new_content)
            current_app.logger.info(f"Updated hostname in {config_file}")
            return True
        else:
            current_app.logger.info(f"Old hostname '{old_hostname}' not found in {config_file}, no changes made.")
            return False
    except IOError as e:
        if e.errno == errno.EACCES: # Requires 'import errno'
             current_app.logger.error(f"Permission denied operating on {config_file}: {e}", exc_info=True)
             flash(f'Permission denied operating on {config_file}. Requires administrator/root privileges.', 'error')
        else:
            current_app.logger.error(f"Error updating hostname in {config_file}: {e}", exc_info=True)
            flash(f'Error updating {os.path.basename(config_file)}: {e}', 'error')
        return False
    except Exception as e:
        current_app.logger.error(f"Unexpected error updating hostname in {config_file}: {e}", exc_info=True)
        flash(f'Unexpected error updating {os.path.basename(config_file)}: {e}', 'error')
        return False


# --- Import/Export Helpers (Platform Independent) ---

def _import_model(tar, tarinfo, model):
    """Imports data for a specific SQLAlchemy model from a tar archive member."""
    # (Code seems platform-independent, no changes needed here)
    # ... (original code remains the same)
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
    # (Code seems platform-independent, no changes needed here)
    # ... (original code remains the same)
    current_app.logger.info("Refreshing application state after import...")
    # Update ser2sock configuration if path exists
    config_path_setting = Setting.get_by_name('ser2sock_config_path')
    if config_path_setting and config_path_setting.value:
        current_app.logger.info(f"Updating ser2sock config at: {config_path_setting.value}")
        try:
            # Retrieve settings with defaults and ensure correct types
            kwargs = {}
            kwargs['device_path'] = Setting.get_by_name('device_path', default='/dev/serial0').value # Note: Path might need adjustment on Windows (e.g., COM1)
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
    # (Code seems platform-independent, no changes needed here)
    # ... (original code remains the same)
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
    # (No platform dependency)
    # ... (original code remains the same)
    use_ssl = Setting.get_by_name('use_ssl', default=False).value
    return render_template('settings/index.html', ssl=use_ssl, active='index')


@settings.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    """Handles user profile updates (details and avatar)."""
    # (No platform dependency)
    # ... (original code remains the same)
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
    # (No platform dependency)
    # ... (original code remains the same)
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
def host():
    """Displays host system information and network interface selection."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # Fetch general info (platform independent parts)
        uptime = _get_system_uptime()
        cpu_temp = _get_cpu_temperature()
        try:
            # socket.getfqdn/gethostname work on Windows too
            hostname_val = socket.getfqdn()
        except socket.gaierror:
            hostname_val = socket.gethostname()

        form = None
        network_interfaces = []
        can_configure_interfaces = IS_LINUX and hasnetifaces # Explicitly check for Linux here

        if not IS_LINUX:
            flash('Host network configuration (changing settings) is currently only supported on Linux systems based on /etc/network/interfaces.', 'warning')

        if hasnetifaces:
            network_interfaces = _list_network_interfaces() # Get interfaces on any OS if netifaces is present
            if network_interfaces:
                 # Only allow selection for configuration on Linux
                 if can_configure_interfaces:
                     choices = [(i, i) for i in network_interfaces] # Show all non-loopback etc. interfaces found by _list_network_interfaces
                     if choices:
                         form = EthernetSelectionForm()
                         form.ethernet_devices.choices = choices

                         if form.validate_on_submit():
                             selected_device = form.ethernet_devices.data
                             # Redirect only if configuring is supported
                             return redirect(url_for('settings.configure_ethernet_device', device=selected_device))
                     else:
                          # Should not happen if network_interfaces is populated, but good practice
                          flash('No configurable network interfaces found.', 'info')
            else:
                 flash('Could not retrieve network interfaces (netifaces returned empty list).', 'warning')
        else: # netifaces not installed
            flash('Python module "netifaces" not found. Network interface listing and configuration are disabled. Install with: pip install netifaces', 'warning')


        return render_template('settings/host.html',
                               hostname=hostname_val,
                               uptime=uptime,
                               cpu_temp=cpu_temp,
                               form=form,
                               interfaces=network_interfaces, # Pass interfaces for display regardless of OS
                               can_configure_interfaces=can_configure_interfaces, # Flag for template logic
                               is_linux=IS_LINUX, # Keep for potential other template uses
                               active="host settings")
    return inner()
@settings.route('/hostname', methods=['GET', 'POST'])
@login_required
def hostname():
    """Handles changing the system hostname (Linux only feature)."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        if not IS_LINUX:
            flash('Changing the system hostname via this interface is only supported on Linux.', 'error')
            return redirect(url_for('settings.host'))

        # --- Linux Specific Hostname Change Logic ---
        try:
            current_hostname = socket.getfqdn()
        except socket.gaierror:
            current_hostname = socket.gethostname() # Fallback

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

            # Check write permissions (without sudo) - Requires web server user to have rights
            # Import errno for checking specific error codes
            import errno
            hosts_writable = os.access(HOSTS_FILE, os.W_OK)
            hostname_writable = os.access(HOSTNAME_FILE, os.W_OK)

            if not hosts_writable:
                flash(f'Cannot write to {HOSTS_FILE}. Check permissions. This action typically requires administrator/root privileges.', 'error')
            if not hostname_writable:
                 flash(f'Cannot write to {HOSTNAME_FILE}. Check permissions. This action typically requires administrator/root privileges.', 'error')

            # Proceed only if permissions seem okay (this is a weak check without sudo)
            if hosts_writable and hostname_writable:
                files_updated = False
                command_success = False
                try:
                    # Update config files directly (will fail if permissions insufficient)
                    hosts_updated = _sethostname(HOSTS_FILE, current_hostname, new_hostname)
                    hostname_updated = _sethostname(HOSTNAME_FILE, current_hostname, new_hostname)
                    files_updated = hosts_updated or hostname_updated

                    if not hosts_updated: current_app.logger.warning(f'Old hostname "{current_hostname}" not found in {HOSTS_FILE}.')
                    if not hostname_updated: current_app.logger.warning(f'Old hostname "{current_hostname}" not found in {HOSTNAME_FILE}.')

                    # Apply hostname change immediately using system commands (requires privileges)
                    cmd_hostnamectl = ['hostnamectl', 'set-hostname', new_hostname]
                    cmd_hostname = ['hostname', new_hostname] # Older command

                    try:
                        _run_command(cmd_hostnamectl, check=True, error_msg_prefix="hostnamectl failed")
                        current_app.logger.info(f"Hostname set to '{new_hostname}' using hostnamectl.")
                        command_success = True
                    except (FileNotFoundError, subprocess.CalledProcessError) as e_ctl:
                        current_app.logger.warning(f"hostnamectl failed ({e_ctl}), trying hostname command.")
                        try:
                            _run_command(cmd_hostname, check=True, error_msg_prefix="hostname command failed")
                            current_app.logger.info(f"Hostname set to '{new_hostname}' using hostname command.")
                            command_success = True
                        except (FileNotFoundError, subprocess.CalledProcessError) as e_host:
                            current_app.logger.error(f"Neither hostnamectl nor hostname command succeeded. Error: {e_host}")
                            # Use stderr from the exception if available
                            err_output = e_host.stderr.strip() if isinstance(e_host, subprocess.CalledProcessError) and e_host.stderr else str(e_host)
                            flash(f'Error: Could not set hostname using system commands. Check permissions or if commands exist. ({err_output})', 'error')
                        except Exception as e_host_other: # Catch unexpected errors from _run_command
                             current_app.logger.error(f"Unexpected error running hostname command: {e_host_other}", exc_info=True)
                             flash(f'Unexpected error setting hostname via command: {e_host_other}', 'error')

                    # Restart Avahi daemon if present and hostname command succeeded
                    if command_success and hasservice and sh_service:
                        try:
                            # Use the imported sh_service directly (only available/imported on Linux)
                            sh_service("avahi-daemon", "restart") # sh.service still uses sh library features
                            current_app.logger.info("Restarted avahi-daemon service.")
                        except Exception as e_service: # Catch potential sh.ErrorReturnCode or other errors
                            current_app.logger.warning(f"Failed to restart avahi-daemon: {e_service}")
                            flash('Warning: Could not restart mDNS service (avahi-daemon). Network discovery might be delayed.', 'warning')

                except Exception as e_update: # Catch errors during file writes (_sethostname flashes its own) or unexpected issues
                     current_app.logger.error(f'An unexpected error occurred while setting hostname: {e_update}', exc_info=True)
                     flash(f'An unexpected error occurred while setting hostname: {e_update}', 'error')
                     command_success = False

                # Provide feedback
                if command_success:
                     flash(f'Hostname changed to "{new_hostname}". A reboot might be required for all services to recognize the change.', 'success')
                     return redirect(url_for('settings.host'))
                elif files_updated:
                     flash(f'Hostname updated in configuration files, but failed to apply change immediately (permissions?). Please reboot.', 'warning')
                     return redirect(url_for('settings.host'))
                # else: Error already flashed if files weren't writable or command failed without file changes

            # If permissions were bad or command failed, stay on the page
            return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")

        # Render form on GET or if validation fails
        return render_template('settings/hostname.html', hostname=current_hostname, form=form, active="hostname")

    return inner()
@settings.route('/get_ethernet_info/<string:device>', methods=['GET'])
@login_required
def get_ethernet_info(device):
    """Returns network information for a specific device as JSON (uses netifaces, cross-platform)."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        eth_properties = {'device': device}

        if not hasnetifaces:
            current_app.logger.warning("get_ethernet_info called but netifaces is not available.")
            return json.dumps({'error': 'netifaces module not available'}), 501, {'ContentType':'application/json'}

        try:
            # Basic validation for device name
            if not re.match(r'^[a-zA-Z0-9\.\-\_\s\(\)]+$', device): # Allow spaces, parens for Windows names
                 return json.dumps({'error': f'Invalid device name format: "{device}".'}), 400, {'ContentType':'application/json'}

            # netifaces calls work on Windows too
            addresses = netifaces.ifaddresses(device)
            gateways = netifaces.gateways()

            # IPv4 Info
            ipv4_list = addresses.get(netifaces.AF_INET, [])
            eth_properties['ipv4'] = ipv4_list[0] if ipv4_list else None

            # IPv6 Info
            ipv6_list = addresses.get(netifaces.AF_INET6, [])
            eth_properties['ipv6'] = ipv6_list[0] if ipv6_list else None

            # MAC Address
            # AF_LINK may not be available or structured the same on Windows.
            # Try AF_LINK first, then potentially look for MAC in other families if needed.
            link_list = addresses.get(netifaces.AF_LINK, [])
            eth_properties['mac_address'] = link_list[0]['addr'] if link_list else 'N/A'
            # Add fallback for Windows if AF_LINK doesn't work reliably? Could parse `ipconfig /all` but that's heavy.
            # psutil.net_if_addrs() provides MAC cross-platform:
            # import psutil; addrs = psutil.net_if_addrs(); mac = next((a.address for a in addrs[device] if a.family == psutil.AF_LINK), None)

            # Default Gateway (IPv4)
            default_gateways = gateways.get('default', {})
            ipv4_gateway_info = default_gateways.get(netifaces.AF_INET)
            eth_properties['default_gateway'] = ipv4_gateway_info[0] if ipv4_gateway_info else None

        except ValueError:
            current_app.logger.info(f'Device "{device}" not found by netifaces.')
            return json.dumps({'error': f'Device "{device}" not found or invalid.'}), 404, {'ContentType':'application/json'}
        except Exception as e:
            current_app.logger.error(f"Error getting info for device {device}: {e}", exc_info=True)
            return json.dumps({'error': f'Error retrieving information for {device}.'}), 500, {'ContentType':'application/json'}

        return json.dumps(eth_properties), 200, {'ContentType':'application/json'}
    return inner()
@settings.route('/reboot', methods=['POST'])
@login_required
def system_reboot():
    """Initiates a system reboot (requires privileges)."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        cmd = None
        if IS_LINUX:
            # Prefer systemctl if available, fallback to reboot
            # Check if systemctl exists? Not easily without running it. Assume common Linux distros have one or the other.
            # We won't check for sync command existence, assume it's there or harmless if not.
            # No need for sync with modern filesystems/shutdown procedures? Maybe remove.
            # sync_cmd = ['sync']
            # try: _run_command(sync_cmd)
            # except Exception: pass # Ignore sync errors
            cmd_systemctl = ['systemctl', 'reboot']
            cmd_reboot = ['reboot']
            # How to run in background properly with subprocess? Popen?
            # For now, run foreground and let webserver handle timeout/disconnect.
            try:
                 _run_command(cmd_systemctl, check=True, error_msg_prefix="systemctl reboot failed")
                 cmd = cmd_systemctl # Mark which command was intended
            except (FileNotFoundError, subprocess.CalledProcessError) as e_ctl:
                 current_app.logger.warning(f"systemctl reboot failed ({e_ctl}), trying reboot command.")
                 try:
                     _run_command(cmd_reboot, check=True, error_msg_prefix="reboot command failed")
                     cmd = cmd_reboot
                 except (FileNotFoundError, subprocess.CalledProcessError) as e_reboot:
                      current_app.logger.error(f"Neither systemctl nor reboot command succeeded: {e_reboot}")
                      err_output = e_reboot.stderr.strip() if isinstance(e_reboot, subprocess.CalledProcessError) and e_reboot.stderr else str(e_reboot)
                      flash(f'Error: Could not find/execute system command to reboot. ({err_output}) Requires privileges.', 'error')
                      return redirect(url_for('settings.host'))
                 except Exception as e_reboot_other:
                      current_app.logger.error(f"Unexpected error running reboot command: {e_reboot_other}", exc_info=True)
                      flash(f'Unexpected error issuing reboot command: {e_reboot_other}. Requires privileges.', 'error')
                      return redirect(url_for('settings.host'))
            except Exception as e_ctl_other:
                current_app.logger.error(f"Unexpected error running systemctl reboot: {e_ctl_other}", exc_info=True)
                flash(f'Unexpected error issuing reboot command: {e_ctl_other}. Requires privileges.', 'error')
                return redirect(url_for('settings.host'))

        elif IS_WINDOWS:
            # Force (-f), reboot (-r), timeout 0 (-t 0)
            cmd_shutdown = ['shutdown', '/r', '/t', '0', '/f']
            try:
                _run_command(cmd_shutdown, check=True, error_msg_prefix="Windows shutdown /r failed")
                cmd = cmd_shutdown
            except (FileNotFoundError, subprocess.CalledProcessError) as e_win:
                 current_app.logger.error(f"Windows reboot command failed: {e_win}")
                 err_output = e_win.stderr.strip() if isinstance(e_win, subprocess.CalledProcessError) and e_win.stderr else str(e_win)
                 flash(f'Error issuing Windows reboot command: {err_output}. Requires administrator privileges.', 'error')
                 return redirect(url_for('settings.host'))
            except Exception as e_win_other:
                 current_app.logger.error(f"Unexpected error running Windows reboot command: {e_win_other}", exc_info=True)
                 flash(f'Unexpected error issuing reboot command: {e_win_other}. Requires administrator privileges.', 'error')
                 return redirect(url_for('settings.host'))

        else:
            flash('Reboot is not supported on this operating system.', 'error')
            return redirect(url_for('settings.host'))

        # If command was successfully initiated (no exception raised by check=True)
        if cmd:
            current_app.logger.info(f"System reboot initiated via {' '.join(cmd)}.")
            flash('Reboot command issued successfully. The device will now restart. Connection will be lost.', 'success')
            # Redirect immediately, don't wait
            return redirect(url_for('frontend.index', message='rebooting'))
        else:
             # Should have been caught above, but as a fallback
             flash('Failed to issue reboot command.', 'error')
             return redirect(url_for('settings.host'))
    return inner()

@settings.route('/shutdown', methods=['POST'])
@login_required
def system_shutdown():
    """Initiates a system shutdown (requires privileges)."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        cmd = None
        if IS_LINUX:
            # Prefer systemctl poweroff, fallback to poweroff, then halt
            # sync_cmd = ['sync']
            # try: _run_command(sync_cmd)
            # except Exception: pass # Ignore sync errors
            cmd_systemctl = ['systemctl', 'poweroff']
            cmd_poweroff = ['poweroff']
            cmd_halt = ['halt'] # Halt might not power off hardware

            try:
                 _run_command(cmd_systemctl, check=True, error_msg_prefix="systemctl poweroff failed")
                 cmd = cmd_systemctl
            except (FileNotFoundError, subprocess.CalledProcessError) as e_ctl:
                 current_app.logger.warning(f"systemctl poweroff failed ({e_ctl}), trying poweroff command.")
                 try:
                     _run_command(cmd_poweroff, check=True, error_msg_prefix="poweroff command failed")
                     cmd = cmd_poweroff
                 except (FileNotFoundError, subprocess.CalledProcessError) as e_poweroff:
                     current_app.logger.warning(f"poweroff command failed ({e_poweroff}), trying halt command.")
                     try:
                          _run_command(cmd_halt, check=True, error_msg_prefix="halt command failed")
                          cmd = cmd_halt
                     except (FileNotFoundError, subprocess.CalledProcessError) as e_halt:
                          current_app.logger.error(f"No shutdown command (systemctl poweroff, poweroff, halt) succeeded: {e_halt}")
                          err_output = e_halt.stderr.strip() if isinstance(e_halt, subprocess.CalledProcessError) and e_halt.stderr else str(e_halt)
                          flash(f'Error: Could not find/execute system command to shut down. ({err_output}) Requires privileges.', 'error')
                          return redirect(url_for('settings.host'))
                     except Exception as e_halt_other:
                        current_app.logger.error(f"Unexpected error running halt command: {e_halt_other}", exc_info=True)
                        flash(f'Unexpected error issuing shutdown command: {e_halt_other}. Requires privileges.', 'error')
                        return redirect(url_for('settings.host'))
                 except Exception as e_poweroff_other:
                    current_app.logger.error(f"Unexpected error running poweroff command: {e_poweroff_other}", exc_info=True)
                    flash(f'Unexpected error issuing shutdown command: {e_poweroff_other}. Requires privileges.', 'error')
                    return redirect(url_for('settings.host'))
            except Exception as e_ctl_other:
                 current_app.logger.error(f"Unexpected error running systemctl poweroff: {e_ctl_other}", exc_info=True)
                 flash(f'Unexpected error issuing shutdown command: {e_ctl_other}. Requires privileges.', 'error')
                 return redirect(url_for('settings.host'))

        elif IS_WINDOWS:
            # Force (-f), shutdown (-s), timeout 0 (-t 0)
            cmd_shutdown = ['shutdown', '/s', '/t', '0', '/f']
            try:
                _run_command(cmd_shutdown, check=True, error_msg_prefix="Windows shutdown /s failed")
                cmd = cmd_shutdown
            except (FileNotFoundError, subprocess.CalledProcessError) as e_win:
                 current_app.logger.error(f"Windows shutdown command failed: {e_win}")
                 err_output = e_win.stderr.strip() if isinstance(e_win, subprocess.CalledProcessError) and e_win.stderr else str(e_win)
                 flash(f'Error issuing Windows shutdown command: {err_output}. Requires administrator privileges.', 'error')
                 return redirect(url_for('settings.host'))
            except Exception as e_win_other:
                 current_app.logger.error(f"Unexpected error running Windows shutdown command: {e_win_other}", exc_info=True)
                 flash(f'Unexpected error issuing shutdown command: {e_win_other}. Requires administrator privileges.', 'error')
                 return redirect(url_for('settings.host'))
        else:
            flash('Shutdown is not supported on this operating system.', 'error')
            return redirect(url_for('settings.host'))

        if cmd:
            current_app.logger.info(f"System shutdown initiated via {' '.join(cmd)}.")
            flash('Shutdown command issued successfully. The device will now power off. Connection will be lost.', 'success')
            return redirect(url_for('frontend.index', message='shutting down'))
        else:
             flash('Failed to issue shutdown command.', 'error')
             return redirect(url_for('settings.host'))
    return inner()

@settings.route('/network/<string:device>', methods=['GET', 'POST'])
@login_required
def configure_ethernet_device(device):
    """
    Configures network settings (DHCP/Static) for a specific device.
    LINUX ONLY: Modifies /etc/network/interfaces, specific to Debian/Ubuntu.
    WINDOWS: Disabled, as configuration is fundamentally different.
    """
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        if not IS_LINUX:
            flash('Manual network interface configuration via this method is only supported on Linux systems using /etc/network/interfaces.', 'error')
            return redirect(url_for('settings.host'))

        # --- Linux Specific Network Configuration ---
        if not hasnetifaces:
            flash('Python module "netifaces" not found. Network configuration is disabled.', 'error')
            return redirect(url_for('settings.host'))

        if not re.match(r'^[a-zA-Z0-9\.\-\_]+$', device) or device == 'lo':
            flash(f'Invalid or unsupported device name: "{device}".', 'warning')
            return redirect(url_for('settings.host'))

        form = EthernetConfigureForm()
        form.ethernet_device.data = device

        # Check permissions (without sudo)
        import errno
        can_read_config = os.access(NETWORK_FILE, os.R_OK)
        can_write_config = os.access(NETWORK_FILE, os.W_OK)

        if not can_read_config:
            flash(f'{NETWORK_FILE} is not readable! Cannot determine current settings.', 'error')
        if not can_write_config:
            flash(f'{NETWORK_FILE} is not writable! Network settings cannot be saved. This typically requires administrator/root privileges.', 'warning')

        device_map = _parse_network_file() if can_read_config else None
        if device_map is None and can_read_config:
             flash(f'Error parsing {NETWORK_FILE}. Cannot reliably determine current settings.', 'warning')

        # --- Get Current Settings (Linux specific) ---
        current_config_type = 'dhcp'
        current_static_ip = ''
        current_static_netmask = ''
        current_static_gateway = ''

        config_block = _get_ethernet_properties_from_map(device, device_map) if device_map else None
        if config_block:
            # (Parsing logic remains the same)
            # ...
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
            elif ' dhcp' in first_line:
                current_config_type = 'dhcp'
            elif ' manual' in first_line:
                current_config_type = 'manual'
                flash(f'Device "{device}" is set to manual configuration. Cannot modify via this interface.', 'warning')
            elif ' loopback' in first_line:
                 flash('Cannot configure the loopback device.', 'warning')
                 return redirect(url_for('settings.host'))
        elif can_read_config:
            flash(f'Device "{device}" not explicitly configured in {NETWORK_FILE}. Assuming DHCP or managed elsewhere.', 'info')

        # Get live IP info from netifaces
        live_ip_address, live_subnet_mask, live_gateway = '', '', ''
        try:
            addresses = netifaces.ifaddresses(device)
            if netifaces.AF_INET in addresses:
                ipv4_info = addresses[netifaces.AF_INET][0]
                live_ip_address = ipv4_info.get('addr', '')
                live_subnet_mask = ipv4_info.get('netmask', '')
            gateways = netifaces.gateways()
            if 'default' in gateways and netifaces.AF_INET in gateways['default']:
                live_gateway = gateways['default'][netifaces.AF_INET][0]
        except (ValueError, KeyError, IndexError, Exception) as e:
            flash(f'Could not retrieve live network details for {device}. Error: {e}', 'warning')

        if not form.is_submitted():
            form.connection_type.data = current_config_type
            form.ip_address.data = current_static_ip if current_config_type == 'static' else live_ip_address
            form.netmask.data = current_static_netmask if current_config_type == 'static' else live_subnet_mask
            form.gateway.data = current_static_gateway if current_config_type == 'static' else live_gateway

        if form.validate_on_submit():
            if not can_write_config:
                 flash(f'{NETWORK_FILE} is not writable! Cannot save changes. Requires administrator/root privileges.', 'error')
                 return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write_config)

            if current_config_type == 'manual':
                 flash(f'Device "{device}" is set to manual configuration. Cannot modify via this interface.', 'error')
                 return redirect(url_for('settings.host'))

            new_connection_type = form.connection_type.data
            new_ip = form.ip_address.data.strip()
            new_netmask = form.netmask.data.strip()
            new_gateway = form.gateway.data.strip()

            if device_map is None:
                 flash(f"Could not read or parse {NETWORK_FILE}. Cannot save changes.", "error")
                 return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write_config)

            # --- Modify device_map (Logic remains the same) ---
            new_device_map = list(device_map)
            iface_index, auto_index = -1, -1
            for i, block in enumerate(new_device_map):
                lines = block.strip().split('\n')
                if lines:
                    if re.match(rf'^\s*iface\s+{re.escape(device)}\s+inet\s+', lines[0]): iface_index = i
                    elif re.match(rf'^\s*auto\s+{re.escape(device)}\s*$', lines[0]): auto_index = i
            # ... (Construct new_iface_block based on form data) ...
            new_iface_block = f"iface {device} inet {new_connection_type}\n"
            if new_connection_type == 'static':
                if not all([new_ip, new_netmask]):
                     flash('IP Address and Netmask are required for static configuration.', 'error')
                     return render_template('settings/configure_ethernet_device.html', form=form, device=device, active="network settings", can_write=can_write_config)
                new_iface_block += f"\taddress {new_ip}\n"
                new_iface_block += f"\tnetmask {new_netmask}\n"
                if new_gateway: new_iface_block += f"\tgateway {new_gateway}\n"

            # ... (Update or Add block to new_device_map) ...
            if iface_index != -1:
                current_app.logger.info(f"Replacing existing iface block for {device} at index {iface_index}")
                new_device_map[iface_index] = new_iface_block
            else:
                current_app.logger.info(f"Adding new iface block for {device}")
                new_device_map.append(new_iface_block)
                flash(f'Interface "{device}" was not found in the config file. Added new configuration block.', 'info')

            # ... (Ensure 'auto <device>' line exists) ...
            auto_block = f"auto {device}\n"
            if auto_index == -1 and device != 'lo':
                current_app.logger.info(f"Adding '{auto_block.strip()}' line.")
                try:
                    current_iface_index = new_device_map.index(new_iface_block)
                    new_device_map.insert(current_iface_index, auto_block)
                except ValueError:
                     new_device_map.insert(0, auto_block)
                flash(f'Added "auto {device}" line to configuration.', 'info')
            elif auto_index != -1 and not new_device_map[auto_index].strip().endswith(device):
                 current_app.logger.warning(f"Existing 'auto' line at index {auto_index} might be complex. Ensuring '{device}' is present.")
                 if device not in new_device_map[auto_index]:
                     new_device_map[auto_index] = new_device_map[auto_index].strip() + f" {device}\n"


            # --- Write changes and restart networking (Linux specific, requires privileges) ---
            write_success = _write_network_file(new_device_map) # This function now checks permissions and flashes errors

            if write_success:
                flash('Network configuration updated. Attempting to apply changes...', 'info')
                restart_success = False
                cmd_ifdown = ['ifdown', device]
                cmd_ifup = ['ifup', device]
                try:
                    # Run commands without sudo - will fail if permissions insufficient
                    current_app.logger.info(f"Running 'ifdown {device}'")
                    # Allow exit code 1 for ifdown (interface already down/not configured)
                    _run_command(cmd_ifdown, check=False) # Don't check=True, check manually
                    # Check result manually if needed, or just proceed to ifup
                    current_app.logger.info(f"Running 'ifup {device}'")
                    _run_command(cmd_ifup, check=True, error_msg_prefix="ifup failed") # Check ifup

                    flash(f'Network interface "{device}" restart requested successfully.', 'success')
                    restart_success = True
                    return redirect(url_for('settings.host'))

                except (FileNotFoundError, subprocess.CalledProcessError) as e_if:
                    err_output = e_if.stderr.strip() if isinstance(e_if, subprocess.CalledProcessError) and e_if.stderr else str(e_if)
                    current_app.logger.error(f"Error applying network changes for '{device}': {e_if}\nOutput:\n{err_output}")
                    flash(f'Error applying network changes for "{device}": {err_output}. Requires privileges. Please check system logs or restart networking manually.', 'error')
                except Exception as e_restart:
                     current_app.logger.error(f'An unexpected error occurred while applying network changes: {e_restart}', exc_info=True)
                     flash(f'An unexpected error occurred while applying network changes: {e_restart}. Requires privileges.', 'error')

                if not restart_success:
                     flash('Configuration saved, but failed to apply changes automatically (permissions?). Manual network restart may be required.', 'warning')
                     return redirect(url_for('settings.host'))
            else:
                # Writing failed (error flashed in _write_network_file)
                 pass # Fall through to render template

        return render_template('settings/configure_ethernet_device.html',
                               form=form, device=device, active="network settings",
                               can_write=can_write_config) # Pass write permission status
    return inner()

@settings.route('/configure_exports', methods=['GET', 'POST'])
@login_required
def configure_exports():
    """Configures automatic settings export (backup) options."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency)
        # ... (original code remains the same)
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
                            # Ensure parent exists before checking write access
                            if not os.path.isdir(parent_dir):
                                 form.local_file_path.errors.append(f"Parent directory '{parent_dir}' does not exist.")
                                 errors = True
                            elif not os.access(parent_dir, os.W_OK | os.X_OK):
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
    return inner()

@settings.route('/export', methods=['GET'])
@login_required
def export():
    """Triggers an immediate manual export of settings."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency)
        # ... (original code remains the same)
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
    return inner()

@settings.route('/git', methods=['GET', 'POST'])
@login_required

def switch_branch():
    """Handles switching Git branches using subprocess (cross-platform)."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # --- Helper: Run Git Command ---
        def _run_git_command(args, repo_path):
            """ Helper to run git commands using subprocess """
            cmd = ['git'] + args
            try:
                # Use the general _run_command helper
                result = _run_command(cmd, cwd=repo_path, capture_output=True, check=False) # check=False, handle errors manually
                return result
            except FileNotFoundError:
                 # Specific error if git isn't installed/found
                 raise FileNotFoundError(f"Git command ('git') not found in PATH. Is Git installed?")
            # Other exceptions (like unexpected errors in _run_command) will propagate

        # --- Helper: Parse Git Output ---
        def strip_ansi(line):
            """Removes ANSI escape codes."""
            if not isinstance(line, str): return line
            # Added more comprehensive regex from common libraries
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            return ansi_escape.sub('', line).strip()

        def build_remotes_list(remote_output_lines):
            """Parses 'git remote -v' output."""
            remotes = {}
            for line in remote_output_lines:
                parts = line.strip().split()
                if len(parts) == 3:
                    name, url, type_str = parts
                    type_val = type_str.strip('()')
                    if name not in remotes:
                        remotes[name] = {'url': url, 'types': set()}
                    remotes[name]['types'].add(type_val)
            choices = []
            for name, data in sorted(remotes.items()):
                types_str = ", ".join(sorted(list(data['types'])))
                label = f"{name} - {data['url']} ({types_str})"
                choices.append((name, label))
            return choices

        def get_git_info(repo_path):
            """Gets current branch, remote, branches, remotes for a repo using subprocess."""
            info = {'branch': None, 'remote': None, 'branches': [], 'remotes': [], 'error': None, 'is_dirty': False}
            git_dir = os.path.join(repo_path, '.git')
            if not os.path.isdir(git_dir):
                 info['error'] = f"Not a Git repository or path invalid: {repo_path} (expected .git dir at {git_dir})"
                 # Log clearly if path itself doesn't exist
                 if not os.path.isdir(repo_path):
                      current_app.logger.error(f"Repository path does not exist: {repo_path}")
                 else:
                      current_app.logger.error(f"No .git directory found in: {repo_path}")
                 return info

            try:
                # Check for uncommitted changes
                status_result = _run_git_command(['status', '--porcelain=v1', '--no-optional-locks'], repo_path)
                if status_result.returncode != 0:
                     raise subprocess.CalledProcessError(status_result.returncode, status_result.args, status_result.stdout, status_result.stderr)
                info['is_dirty'] = bool(status_result.stdout.strip())

                # Get current branch name
                branch_result = _run_git_command(['symbolic-ref', '--short', 'HEAD', '--no-optional-locks'], repo_path)
                current_branch = None
                if branch_result.returncode == 0:
                    current_branch = branch_result.stdout.strip()
                    info['branch'] = current_branch
                else:
                    # Likely detached HEAD
                    commit_result = _run_git_command(['rev-parse', '--short', 'HEAD', '--no-optional-locks'], repo_path)
                    if commit_result.returncode == 0:
                        info['branch'] = f"HEAD (Detached at {commit_result.stdout.strip()})"
                    else:
                        info['branch'] = "HEAD (Unknown state)"
                    info['remote'] = None # No remote tracking in detached state

                # Find tracking remote if on a branch
                if current_branch and info['remote'] is None:
                     remote_result = _run_git_command(['config', f'branch.{current_branch}.remote'], repo_path)
                     if remote_result.returncode == 0:
                          info['remote'] = remote_result.stdout.strip()
                     else:
                          info['remote'] = None # No remote configured for this branch

                # Get all branches (local and simplified remote)
                all_branches_result = _run_git_command(['branch', '-a', '--no-color', '--no-optional-locks'], repo_path)
                if all_branches_result.returncode != 0:
                     raise subprocess.CalledProcessError(all_branches_result.returncode, all_branches_result.args, all_branches_result.stdout, all_branches_result.stderr)
                unique_branches = set()
                for line in all_branches_result.stdout.splitlines():
                    line = strip_ansi(line) # Strip ANSI just in case
                    if line.startswith('*'): line = line[1:].strip()
                    if '->' in line: continue
                    if line.startswith('remotes/'):
                        parts = line.split('/', 2)
                        if len(parts) == 3: unique_branches.add(parts[2])
                    else:
                        unique_branches.add(line)
                info['branches'] = sorted(list(b for b in unique_branches if not b.startswith("HEAD (")))

                # Get remotes
                remotes_result = _run_git_command(['remote', '-v'], repo_path)
                if remotes_result.returncode != 0:
                     raise subprocess.CalledProcessError(remotes_result.returncode, remotes_result.args, remotes_result.stdout, remotes_result.stderr)
                info['remotes'] = build_remotes_list(remotes_result.stdout.splitlines())

            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                err_msg = f"Git command failed in {repo_path}. Error: {e}."
                # Include stderr if available
                if hasattr(e, 'stderr') and e.stderr: err_msg += f" Stderr: {strip_ansi(e.stderr.strip())}"
                if hasattr(e, 'stdout') and e.stdout: err_msg += f" Stdout: {strip_ansi(e.stdout.strip())}" # Sometimes errors go to stdout
                info['error'] = err_msg
                current_app.logger.error(err_msg)
            except Exception as e:
                info['error'] = f"Unexpected error accessing git repo {repo_path}: {e}"
                current_app.logger.error(info['error'], exc_info=True)

            return info

        # --- Main Logic ---
        cwd_web = os.getcwd()
        # Adjust API path finding if needed (ensure it works cross-platform)
        cwd_api = os.path.abspath(os.path.join(cwd_web, '..', 'alarmdecoder')) # Use abspath for robustness

        # Check if paths actually exist before trying git operations
        web_repo_exists = os.path.isdir(os.path.join(cwd_web, '.git'))
        api_repo_exists = os.path.isdir(os.path.join(cwd_api, '.git'))

        web_info = get_git_info(cwd_web) if web_repo_exists else {'error': f"Webapp path is not a Git repository: {cwd_web}"}
        api_info = get_git_info(cwd_api) if api_repo_exists else {'error': f"API path is not a Git repository: {cwd_api}"}


        if web_info['error']: flash(f"Webapp Git Error ({cwd_web}): {web_info['error']}", 'error')
        if api_info['error']: flash(f"API Git Error ({cwd_api}): {api_info['error']}", 'error')

        form = SwitchBranchForm()
        form.branches_web.choices = [(b, b) for b in web_info.get('branches', [])]
        form.remotes_web.choices = web_info.get('remotes', [])
        form.branches_api.choices = [(b, b) for b in api_info.get('branches', [])]
        form.remotes_api.choices = api_info.get('remotes', [])

        if form.validate_on_submit():
            target_branch_web = form.branches_web.data
            target_remote_web = form.remotes_web.data
            target_branch_api = form.branches_api.data
            target_remote_api = form.remotes_api.data

            actions_performed = False
            errors_occurred = False

            # --- Process Webapp Repo ---
            if not web_info.get('error') and web_repo_exists and \
               (target_branch_web != web_info.get('branch') or target_remote_web != web_info.get('remote')):
                if web_info.get('is_dirty'):
                     flash(f'Webapp repo ({cwd_web}) has uncommitted changes. Please commit or stash.', 'error')
                     errors_occurred = True
                else:
                    actions_performed = True
                    current_app.logger.info(f"Attempting switch webapp: Branch '{web_info.get('branch')}'->'{target_branch_web}', Remote '{web_info.get('remote')}'->'{target_remote_web}'")
                    try:
                        # 1. Checkout branch
                        checkout_res = _run_git_command(['checkout', target_branch_web, '--no-optional-locks'], cwd_web)
                        if checkout_res.returncode != 0: raise subprocess.CalledProcessError(checkout_res.returncode, checkout_res.args, checkout_res.stdout, checkout_res.stderr)
                        flash(f'Switched webapp to branch: {target_branch_web}', 'success')

                        # 2. Pull from selected remote
                        current_app.logger.info(f"Pulling webapp {target_remote_web}/{target_branch_web}")
                        pull_res = _run_git_command(['pull', target_remote_web, target_branch_web, '--no-optional-locks'], cwd_web)
                        if pull_res.returncode != 0: raise subprocess.CalledProcessError(pull_res.returncode, pull_res.args, pull_res.stdout, pull_res.stderr)
                        flash(f'Pulled webapp changes from {target_remote_web}/{target_branch_web}', 'success')

                    except (subprocess.CalledProcessError, FileNotFoundError) as e:
                        errors_occurred = True
                        err_msg = strip_ansi(e.stderr.strip() if hasattr(e, 'stderr') and e.stderr else str(e))
                        current_app.logger.error(f"Error updating webapp repository: {err_msg}")
                        flash(f'Error updating webapp repository: {err_msg}', 'error')
                    except Exception as e:
                        errors_occurred = True
                        current_app.logger.error(f'Unexpected error updating webapp repository: {e}', exc_info=True)
                        flash(f'Unexpected error updating webapp repository: {e}', 'error')

            # --- Process API Repo ---
            if not api_info.get('error') and api_repo_exists and \
               (target_branch_api != api_info.get('branch') or target_remote_api != api_info.get('remote')):
                 if api_info.get('is_dirty'):
                     flash(f'API repo ({cwd_api}) has uncommitted changes. Please commit or stash.', 'error')
                     errors_occurred = True
                 else:
                    actions_performed = True
                    current_app.logger.info(f"Attempting switch API: Branch '{api_info.get('branch')}'->'{target_branch_api}', Remote '{api_info.get('remote')}'->'{target_remote_api}'")
                    try:
                        # 1. Checkout
                        checkout_res = _run_git_command(['checkout', target_branch_api, '--no-optional-locks'], cwd_api)
                        if checkout_res.returncode != 0: raise subprocess.CalledProcessError(checkout_res.returncode, checkout_res.args, checkout_res.stdout, checkout_res.stderr)
                        flash(f'Switched API library to branch: {target_branch_api}', 'success')

                        # 2. Pull
                        current_app.logger.info(f"Pulling API {target_remote_api}/{target_branch_api}")
                        pull_res = _run_git_command(['pull', target_remote_api, target_branch_api, '--no-optional-locks'], cwd_api)
                        if pull_res.returncode != 0: raise subprocess.CalledProcessError(pull_res.returncode, pull_res.args, pull_res.stdout, pull_res.stderr)
                        flash(f'Pulled API changes from {target_remote_api}/{target_branch_api}', 'success')

                    except (subprocess.CalledProcessError, FileNotFoundError) as e:
                        errors_occurred = True
                        err_msg = strip_ansi(e.stderr.strip() if hasattr(e, 'stderr') and e.stderr else str(e))
                        current_app.logger.error(f"Error updating API repository: {err_msg}")
                        flash(f'Error updating API repository: {err_msg}', 'error')
                    except Exception as e:
                         errors_occurred = True
                         current_app.logger.error(f'Unexpected error updating API repository: {e}', exc_info=True)
                         flash(f'Unexpected error updating API repository: {e}', 'error')

            if not actions_performed and not errors_occurred:
                 flash('No changes selected for Git branches or remotes.', 'info')

            # Redirect to refresh status
            return redirect(url_for('settings.switch_branch'))

        # --- Populate Form Defaults ---
        if not web_info.get('error'):
            if web_info.get('branch') and not web_info.get('branch','').startswith("HEAD"):
                form.branches_web.default = web_info['branch']
            if web_info.get('remote'):
                form.remotes_web.default = web_info['remote']
        if not api_info.get('error'):
            if api_info.get('branch') and not api_info.get('branch','').startswith("HEAD"):
                form.branches_api.default = api_info['branch']
            if api_info.get('remote'):
                form.remotes_api.default = api_info['remote']

        form.process()

        return render_template('settings/git.html', form=form,
                                current_branch_web=web_info.get('branch'), current_remote_web=web_info.get('remote'),
                                web_is_dirty=web_info.get('is_dirty'), web_error=web_info.get('error'),
                                current_branch_api=api_info.get('branch'), current_remote_api=api_info.get('remote'),
                                api_is_dirty=api_info.get('is_dirty'), api_error=api_info.get('error'),
                                active='git')
    return inner()

@settings.route('/import', methods=['GET', 'POST'])
@login_required

def import_backup():
    """Handles importing settings from a previously exported tar.gz archive."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency)
        # ... (original code remains the same)
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
    return inner()

@settings.route('/diagnostics', methods=['GET'])
@login_required
def system_diagnostics():
    """Displays diagnostic information about the connected AlarmDecoder device."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
    # (No platform dependency in this function itself)
    # ... (original code remains the same)
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
    return inner()

@settings.route('/advanced', methods=['GET'])
@login_required
def advanced():
    """Displays the advanced settings landing page."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency)
        return render_template('settings/advanced.html', active="advanced")
    return inner()

@settings.route('/get_imports_list', methods=['GET'])
@login_required

def get_system_imports():
    """Returns a JSON list of known/required modules and their import status."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency)
        # ... (original code remains the same)
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
    return inner()

@settings.route('/disable_forward', methods=['POST'])
@login_required

def disable_forwarding():
    """Disables the currently configured UPNP port forward."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency in this function itself, relies on has_upnp flag)
        # ... (original code remains the same)
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
    return inner()

@settings.route('/port_forward', methods=['GET', 'POST'])
@login_required
def port_forwarding():
    """Configures UPNP port forwarding."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency in this function itself, relies on has_upnp flag)
        # ... (original code remains the same)
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
            form.internal_port.data = int(current_internal_port) if current_internal_port and str(current_internal_port).isdigit() else 443 # Default internal HTTPS port
            # Suggest a random high port for external to avoid common conflicts
            form.external_port.data = int(current_external_port) if current_external_port and str(current_external_port).isdigit() else random.randint(10000, 60000)

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
                               upnp_unavailable=(not has_upnp), # Pass unavailability status
                               active="port_forward")
    return inner()

@settings.route('/configure_updater', methods=['GET', 'POST'])
@login_required
def configure_updater():
    """Configures the automatic version checking settings."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency)
        # ... (original code remains the same)
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
    return inner()

@settings.route('/configure_system_email', methods=['GET', 'POST'])
@login_required
def configure_system_email():
    """Configures system-wide email settings used for notifications and exports."""
    from ..decorators import admin_required    # Import inside the function
    @admin_required
    def inner():
        # (No platform dependency)
        # ... (original code remains the same)
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
    return inner()