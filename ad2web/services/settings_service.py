import subprocess
from sqlalchemy.exc import SQLAlchemyError
import os
import platform
import re
import json
import socket
import ssl
import urllib.request
import urllib.error
import urllib.parse
from flask import current_app

try:
    import netifaces

    has_netifaces = True
except ImportError:
    has_netifaces = False
try:
    import miniupnpc

    has_upnp = True
except ImportError:
    has_upnp = False

# Constants (could be moved to a constants.py)
HOSTS_FILE = "/etc/hosts"
HOSTNAME_FILE = "/etc/hostname"
NETWORK_FILE = "/etc/network/interfaces"
IP_CHECK_SERVER_URL = "https://www.httpbin.org/ip"


class SettingsService:
    """Service layer for handling settings retrieval, persistence, and system operations."""

    @staticmethod
    def get_setting(name, default=None, type_func=None):
        """Retrieve a setting value from the database, with optional type conversion."""
        from ad2web.settings.models import Setting
        return Setting.get_value(name, default=default, type_func=type_func)

    @staticmethod
    def set_setting(name, value):
        """Set (create or update) a setting value in the database."""
        from ad2web.settings.models import Setting
        Setting.set_value(name, value)

    @staticmethod
    def list_network_interfaces():
        """List available network interfaces (excluding loopback and docker/veth)."""
        if not has_netifaces:
            return []
        try:
            interfaces = netifaces.interfaces()
            # Filter out loopback and virtual interfaces
            return [iface for iface in interfaces if
                    iface != 'lo' and not iface.startswith('docker') and not iface.startswith('veth')]
        except Exception as e:
            current_app.logger.error(f"Error listing network interfaces: {e}")
            return []

    @staticmethod
    def get_system_uptime():
        """Get the system uptime as a formatted string (HH:MM:SS)."""
        try:
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.readline().split()[0])
                uptime_string = str(os.timesdelta(seconds=uptime_seconds)).split('.')[0]  # use timedelta for formatting
        except Exception as e:
            current_app.logger.warning(f"Could not read system uptime: {e}")
            uptime_string = "N/A"
        return uptime_string

    @staticmethod
    def get_cpu_temperature():
        """Get the CPU temperature in Celsius if available, otherwise 'Not supported'."""
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
                        # Check if likely in millidegrees
                        if temp_reading > 1000:
                            cpu_temp_c = temp_reading / 1000.0
                        else:
                            cpu_temp_c = temp_reading
                        return f"{cpu_temp_c:.1f} °C"
                except Exception as e:
                    current_app.logger.warning(f"Error reading temperature from {temp_file}: {e}")
                    continue
        return "Not supported"

    @staticmethod
    def get_external_ip():
        """Fetch the public external IP address of the server."""
        try:
            context = ssl._create_unverified_context()
            req = urllib.request.Request(IP_CHECK_SERVER_URL, headers={'User-Agent': 'AlarmDecoder-WebApp/1.0'})
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                if response.status == 200:
                    data = json.load(response)
                    return data.get('origin')
                else:
                    current_app.logger.warning(f"Could not retrieve external IP: Status code {response.status}")
                    return None
        except Exception as e:
            current_app.logger.warning(f"Could not retrieve external IP: {e}")
            return None

    @staticmethod
    def parse_network_file():
        """Parse the network interfaces file into configuration blocks."""
        if not os.path.exists(NETWORK_FILE):
            current_app.logger.warning(f"Network file not found: {NETWORK_FILE}")
            return None
        try:
            with open(NETWORK_FILE, 'r') as f:
                text = f.read()
            indexes = [m.start() for m in
                       re.finditer(r'^\s*(auto|iface|source|mapping|allow-|wpa-)', text, flags=re.MULTILINE)]
            if not indexes:
                return [text] if text.strip() else []
            blocks = [text[indexes[i]:indexes[i + 1]] for i in range(len(indexes) - 1)] + [text[indexes[-1]:]]
            return blocks
        except Exception as e:
            current_app.logger.error(f"Error parsing network file {NETWORK_FILE}: {e}")
            return None

    @staticmethod
    def write_network_file(blocks):
        """Write the modified network configuration blocks back to the file."""
        if blocks is None:
            current_app.logger.error("Attempted to write None to network file.")
            return False
        try:
            os.makedirs(os.path.dirname(NETWORK_FILE), exist_ok=True)
            text = ''.join(blocks)
            with open(NETWORK_FILE, 'w') as f:
                f.write(text)
            current_app.logger.info(f"Successfully wrote network configuration to {NETWORK_FILE}")
            return True
        except Exception as e:
            current_app.logger.error(f"Could not write to network file {NETWORK_FILE}: {e}")
            return False

    @staticmethod
    def get_interface_config_block(device, blocks):
        """Extract the configuration block for a specific device from parsed blocks."""
        if blocks is None:
            return None
        for block in blocks:
            lines = block.strip().split('\n')
            if lines and re.match(rf'^\s*iface\s+{re.escape(device)}\s+inet\s+', lines[0]):
                return block
        return None

    def update_hostname(new_hostname: str) -> bool:
        """Update system hostname via hostnamectl + /etc/hosts (Linux only)."""
        if platform.system().lower() != 'linux':
            raise RuntimeError("Hostname changes are only supported on Linux.")

        old_hostname = socket.getfqdn()

        # 1) Check writability under sudo for each file
        try:
            subprocess.run(
                ['sudo', 'test', '-w', HOSTS_FILE],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            hosts_writable = True
        except subprocess.CalledProcessError:
            hosts_writable = False

        try:
            subprocess.run(
                ['sudo', 'test', '-w', HOSTNAME_FILE],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            hostname_writable = True
        except subprocess.CalledProcessError:
            hostname_writable = False

        if not (hosts_writable and hostname_writable):
            missing = []
            if not hosts_writable:    missing.append(HOSTS_FILE)
            if not hostname_writable: missing.append(HOSTNAME_FILE)
            raise PermissionError(f"Cannot write to: {', '.join(missing)}")

        # 2) Use hostnamectl to change the hostname
        try:
            subprocess.run(
                ['sudo', 'hostnamectl', 'set-hostname', new_hostname],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as e:
            current_app.logger.error(f"Failed to set system hostname: {e}")
            raise RuntimeError("Could not set system hostname.")

        # 3) Patch /etc/hosts in-place via sudo+sed
        sed_expr = f"s/{old_hostname}/{new_hostname}/g"
        try:
            subprocess.run(
                ['sudo', 'sed', '-i', sed_expr, HOSTS_FILE],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError as e:
            current_app.logger.error(f"Failed to update {HOSTS_FILE}: {e}")
            raise RuntimeError(f"Could not update {HOSTS_FILE}")

        return True

    @staticmethod
    def _sethostname_in_file(config_file, old_hostname, new_hostname):
        """Internal helper to replace hostname in a given file."""
        if not os.path.exists(config_file):
            current_app.logger.warning(f"Hostname config file not found: {config_file}")
            return False
        try:
            with open(config_file, 'r') as f:
                content = f.read()
            new_content = content.replace(old_hostname, new_hostname)
            if new_content != content:
                with open(config_file, 'w') as f:
                    f.write(new_content)
                current_app.logger.info(f"Updated hostname in {config_file}")
                return True
            else:
                current_app.logger.info(f"No hostname change needed in {config_file}.")
                return False
        except Exception as e:
            current_app.logger.error(f"Error updating hostname in {config_file}: {e}")
            return False

    @staticmethod
    def reboot_system():
        """Issue a system reboot command (Linux only)."""
        if platform.system().lower() != 'linux':
            raise RuntimeError("Reboot is only supported on Linux.")
        try:
            # Try systemctl first
            subprocess.Popen(['systemctl', 'reboot'])
            current_app.logger.info("System reboot initiated via systemctl.")
        except FileNotFoundError:
            # systemctl not found, try the direct reboot command
            try:
                subprocess.Popen(['reboot'])
                current_app.logger.info("System reboot initiated via reboot command.")
            except Exception as e:
                current_app.logger.error(f"Failed to initiate reboot: {e}")
                raise

    @staticmethod
    def shutdown_system():
        """Issue a system shutdown command (Linux only)."""
        if platform.system().lower() != 'linux':
            raise RuntimeError("Shutdown is only supported on Linux.")
        try:
            subprocess.Popen(['systemctl', 'poweroff'])
            current_app.logger.info("System shutdown initiated via systemctl.")
        except FileNotFoundError:
            try:
                subprocess.Popen(['poweroff'])
                current_app.logger.info("System shutdown initiated via poweroff command.")
            except FileNotFoundError:
                try:
                    subprocess.Popen(['halt'])
                    current_app.logger.info("System shutdown initiated via halt command.")
                except Exception as e:
                    current_app.logger.error(f"No shutdown command succeeded: {e}")
                    raise

    @staticmethod
    def refresh_exporter_thread(decoder):
        """Refresh the parameters of the exporter thread if running."""
        if hasattr(decoder, '_exporter_thread') and decoder._exporter_thread:
            current_app.logger.info("Refreshing exporter thread parameters...")
            try:
                decoder._exporter_thread.prepParams()
                current_app.logger.info("Exporter thread parameters refreshed.")
            except Exception as e:
                current_app.logger.error(f"Error refreshing exporter thread: {e}")
                raise

    @staticmethod
    def refresh_version_thread(decoder, timeout, disable):
        """Update the version checker thread with new settings if running."""
        if hasattr(decoder, '_version_thread') and decoder._version_thread:
            current_app.logger.info("Updating version checker thread parameters...")
            decoder._version_thread.setTimeout(int(timeout))
            decoder._version_thread.setDisable(bool(disable))
            current_app.logger.info("Version checker thread parameters updated.")
