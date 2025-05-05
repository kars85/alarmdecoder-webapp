import os
import psutil
import signal
from collections import OrderedDict
import six
import platform
import subprocess
import logging
logger = logging.getLogger(__name__)
# Attempt to import `sh` for Unix-like convenience; fallback to None on unsupported platforms
try:
    import sh
except ImportError:
    sh = None

DEFAULT_SETTINGS = OrderedDict([
    ('daemonize', 1),
    ('device', ''),
    ('raw_device_mode', 1),
    ('baudrate', 19200),
    ('port', 10000),
    ('preserve_connections', 1),
    ('bind_ip', '0.0.0.0'),
    ('send_terminal_init', 0),
    ('device_open_delay', 5000),
    ('encrypted', '0'),
    ('ca_certificate', ''),
    ('ssl_certificate', ''),
    ('ssl_key', ''),
    ('ssl_crl', '/etc/ser2sock/ser2sock.crl'),
])

class NotFound(Exception):
    """Exception generated when ser2sock is not found."""
    pass

class HupFailed(Exception):
    """Exception generated when ser2sock fails to be hupped."""
    pass

def read_config(path):
    """
    Reads an existing ser2sock configuration.

    :param path: Path to the configuration file.
    :type path: string
    :returns: A SafeConfigParser to operate on the configuration.
    """
    config = six.moves.configparser.SafeConfigParser()
    config.read(path)

    return config

def save_config(path, config_values):
    """
    Saves the ser2sock configuration.

    :param path: Path to the configuration file.
    :type path: string
    :param config_values: Configuration values to use
    :type config_values: dict
    """
    config = read_config(path)

    try:
        config.add_section('ser2sock')
    except six.moves.configparser.DuplicateSectionError:
        pass

    # Include default entries
    config_entries = OrderedDict(list(DEFAULT_SETTINGS.items()) + list(config_values.items()))

    for k, v in config_entries.items():
        config.set('ser2sock', k, str(v))

    with open(path, 'w') as configfile:
        config.write(configfile)

def exists() -> bool:
    """
    Return True if the ser2sock process is currently running.
    Uses `sh.pgrep` on Unix if available, or falls back to subprocess-based checks.
    On Windows, uses `tasklist` to detect the running executable.
    """
    if sh:
        try:
            sh.pgrep("ser2sock")
            return True
        except sh.ErrorReturnCode:
            return False

    system = platform.system()
    if system == "Windows":
        try:
            output = subprocess.check_output(["tasklist"], text=True)
            return "ser2sock.exe" in output
        except Exception:
            return False
    else:
        # Unix fallback: pgrep
        try:
            subprocess.run(
                ["pgrep", "ser2sock"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

def start():
    """
    Starts ser2sock
    """
    try:
        sh.ser2sock('-d', _bg=True)
    except sh.CommandNotFound:
        raise NotFound('Could not locate ser2sock.')

def stop() -> None:
    """
    Terminate the ser2sock daemon process.
    Uses `sh.kill` if available; on Windows, uses `taskkill`, otherwise `pkill`.
    """
    if sh:
        try:
            sh.kill("ser2sock", "-TERM")
            return
        except Exception as e:
            logger.error(f"Failed to stop ser2sock via sh: {e}")

    system = platform.system()
    if system == "Windows":
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "ser2sock.exe"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.error(f"Failed to stop ser2sock via taskkill: {e}")
    else:
        try:
            subprocess.run(["pkill", "ser2sock"], check=True)
        except Exception as e:
            logger.error(f"Failed to stop ser2sock via subprocess: {e}")


def hup() -> None:
    """
    Send SIGHUP to the ser2sock daemon to reload its configuration without stopping it.
    Uses `sh.kill` if available; otherwise, invokes `pkill -HUP ser2sock` on Unix.
    """
    if sh:
        try:
            sh.kill("ser2sock", "-HUP")
            return
        except Exception as e:
            logger.error(f"Failed to send HUP via sh: {e}")

    # Fallback implementation for non-Windows
    if platform.system() != "Windows":
        try:
            subprocess.run(["pkill", "-HUP", "ser2sock"], check=True)
        except Exception as e:
            logger.error(f"Failed to send HUP via subprocess: {e}")

def update_config(path, *args, **kwargs):
    """
    Updates the ser2sock configuration with new settings, saves the index
    and revocation list, and hups ser2sock.

    :param path: Path to the ser2sock configuration directory
    :type path: string
    :param args: Argument list
    :type args: list
    :param kwargs: Keyward arguments
    :type kwargs: dict
    """
    try:
        if path is not None:
            config = read_config(os.path.join(path, 'ser2sock.conf'))
        else:
            config = None

        if config is not None:
            # Pre-populate with existing settings from the config.
            config_values = {}
            if config.has_section('ser2sock'):
                for k, v in config.items('ser2sock'):
                    config_values[k] = v

            # Set any settings that were provided in our kwargs.
            if 'device_path' in list(kwargs.keys()):
                config_values['device'] = kwargs['device_path']
            if 'device_baudrate' in list(kwargs.keys()):
                config_values['baudrate'] = kwargs['device_baudrate']
            if 'device_port' in list(kwargs.keys()):
                config_values['port'] = kwargs['device_port']
            if 'use_ssl' in list(kwargs.keys()):
                config_values['encrypted'] = int(kwargs['use_ssl'])

            if 'encrypted' in config_values and config_values['encrypted'] == 1:
                cert_path = os.path.join(path, 'certs')
                if not os.path.exists(cert_path):
                    os.mkdir(cert_path, 0o700)

                ca_cert = kwargs['ca_cert'] if 'ca_cert' in list(kwargs.keys()) else None
                server_cert = kwargs['server_cert'] if 'server_cert' in list(kwargs.keys()) else None

                if ca_cert is not None and server_cert is not None:
                    ca_cert.export(cert_path)
                    server_cert.export(cert_path)

                    config_values['ca_certificate'] = os.path.join(cert_path, '{}.pem'.format(ca_cert.name))
                    config_values['ssl_certificate'] = os.path.join(cert_path, '{}.pem'.format(server_cert.name))
                    config_values['ssl_key'] = os.path.join(cert_path, '{}.key'.format(server_cert.name))

            save_config(os.path.join(path, 'ser2sock.conf'), config_values)
            hup()

    except OSError as err:
        raise RuntimeError('Error updating ser2sock configuration: {}'.format(err))
