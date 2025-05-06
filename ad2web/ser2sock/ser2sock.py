# ad2web/ser2sock/ser2sock.py
"""
Cross-platform control utilities for the ser2sock daemon.
Provides functions to detect, reload, stop, and update configuration of the ser2sock process.
"""
import platform
import signal
import subprocess
import logging
import os
import psutil
from sqlalchemy.testing.plugin.plugin_base import read_config
from werkzeug.exceptions import NotFound

logger = logging.getLogger(__name__)

# Attempt to import `sh` for Unix-like convenience; fallback to None on unsupported platforms
try:
    import sh
except ImportError:
    sh = None

class Ser2SockController:
    def __init__(self, config_path=None):
        """
        Initialize the Ser2SockController.
        :param config_path: Optional base path for ser2sock configuration files.
        """
        self.config_path = config_path
        # Determine if the 'sh' library can be used (available on Unix-like systems).
        try:
            import sh
            self._sh = sh
        except ImportError:
            self._sh = None


def exists(self) -> bool:
    """
    Check if the 'ser2sock' executable is present in the system PATH.
    :return: True if ser2sock is found in PATH, False otherwise.
    """
    if self._sh:
        # Use sh.which if available (Unix-like environments)
        return self._sh.which('ser2sock') is not None
    else:
        # Fallback: use shutil.which for cross-platform support
        from shutil import which
        return which('ser2sock') is not None

def start(self):
    """
    Start the ser2sock service as a background process.
    :raises NotFound: if the ser2sock binary is not found.
    """
    if self._sh:
        try:
            # Use sh to start ser2sock in daemon mode (_bg=True runs in background)
            self._sh.ser2sock('-d', _bg=True)
        except self._sh.CommandNotFound:
            raise NotFound("Could not locate ser2sock.")
    else:
        # Fallback: use subprocess to start the process
        import subprocess, sys
        if not self.exists():
            raise NotFound("Could not locate ser2sock.")
        # Launch ser2sock with '-d' (daemonize) flag. On Windows, '-d' may be ignored if unsupported.
        creationflags = 0
        if sys.platform.startswith('win'):
            # On Windows, to not open a console window, use CREATE_NO_WINDOW
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        try:
            subprocess.Popen(['ser2sock', '-d'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
        except Exception as e:
            raise NotFound(f"Failed to start ser2sock: {e}")


def hup(self):
    """
    Reload ser2sock configuration by sending SIGHUP, or restart the service if needed.
    :raises HupFailed: if sending SIGHUP fails due to OS error.
    """
    found = False
    for proc in psutil.process_iter():
        try:
            if proc.name() == 'ser2sock':
                found = True
                if hasattr(signal, 'SIGHUP'):
                    # On Unix, send SIGHUP to prompt config reload
                    os.kill(proc.pid, signal.SIGHUP)
                else:
                    # On non-Unix systems, no SIGHUP; kill the process to restart it
                    proc.kill()
        except OSError as err:
            # If we attempted a HUP and it failed (perhaps permission issues)
            raise HupFailed(f"Error attempting to restart ser2sock (pid {proc.pid}): {err}")
    # If no process was found, or we killed it on a platform without SIGHUP, start a new one
    if not found or not hasattr(signal, 'SIGHUP'):
        self.start()


def stop(self):
    """
    Stop any running ser2sock process.
    """
    # Iterate over all running processes and kill those named 'ser2sock'
    for proc in psutil.process_iter():
        try:
            if proc.name() == 'ser2sock':
                proc.kill()  # force kill, similar to SIGKILL
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Ignore processes that terminate during iteration or those we cannot access
            continue


def update_config(self, path: str, *args, **kwargs):
    """
    Update ser2sock configuration with new settings and reload the service.
    :param path: Filesystem path to the ser2sock configuration directory.
    :param args: Additional positional args (unused).
    :param kwargs: Keyword args for specific settings (device_path, device_baudrate, device_port, use_ssl, etc).
    """
    # Determine the config file path
    config_path = os.path.join(path, 'ser2sock.conf') if path else None
    config = read_config(config_path) if config_path else None

    # Merge existing config with provided overrides
    config_values = {}
    if config and config.has_section('ser2sock'):
        for k, v in config.items('ser2sock'):
            config_values[k] = v
    # Apply overrides from kwargs
    if 'device_path' in kwargs:
        config_values['device'] = kwargs['device_path']
    if 'device_baudrate' in kwargs:
        config_values['baudrate'] = kwargs['device_baudrate']
    if 'device_port' in kwargs:
        config_values['port'] = kwargs['device_port']
    if 'use_ssl' in kwargs:
        config_values['encrypted'] = int(kwargs['use_ssl'])
    # Handle SSL certificate saving if needed
    if config_values.get('encrypted') == 1:
        cert_dir = os.path.join(path, 'certs')
        if not os.path.exists(cert_dir):
            os.mkdir(cert_dir, 0o700)
        ca_cert = kwargs.get('ca_cert')
        server_cert = kwargs.get('server_cert')
        if ca_cert and server_cert:
            ca_cert.export(cert_dir)
            server_cert.export(cert_dir)
            # Update config paths for certificates
            config_values['ca_certificate'] = os.path.join(cert_dir, f"{ca_cert.name}.pem")
            config_values['ssl_certificate'] = os.path.join(cert_dir, f"{server_cert.name}.pem")
            config_values['ssl_key'] = os.path.join(cert_dir, f"{server_cert.name}.key")
    # Save the updated configuration to file
    save_config(config_path, config_values)
    # Signal ser2sock to reload (or restart it)
    self.hup()

