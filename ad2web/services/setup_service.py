import os, glob, platform, logging
from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import MultipleResultsFound
from ..extensions import db
from ..settings.models import Setting
from ..certificate.models import Certificate
from ..certificate.constants import CA, SERVER, INTERNAL, ACTIVE as CERT_ACTIVE
from ..user.models import User
from ..user.constants import ADMIN as USER_ADMIN, ACTIVE as USER_ACTIVE
from ..ser2sock import ser2sock
from alarmdecoder.panels import ADEMCO

log = logging.getLogger(__name__)

class SetupService:
    """Service layer for the setup wizard logic."""

    @staticmethod
    def is_setup_complete():
        """Check if initial setup is complete."""
        return Setting.get_by_name('setup_complete', default=False).value is True

    @staticmethod
    def create_admin_user(username: str, email: str, password: str):
        """Create the initial admin user and mark setup as complete."""
        # Check for existing user with same name or email
        existing_user = User.query.filter((User.name == username) | (User.email == email)).first()
        if existing_user:
            return None, "A user with that username or email already exists."
        try:
            user = User(name=username, email=email, password=password, role_code=USER_ADMIN, status_code=USER_ACTIVE)
            db.session.add(user)
            setup_complete = Setting.get_by_name('setup_complete', default=False)
            setup_complete.value = True
            db.session.add(setup_complete)
            setup_stage = Setting.get_by_name('setup_stage', default=None)
            if setup_stage:
                setup_stage.value = 100  # 100 denotes setup complete
                db.session.add(setup_stage)
            db.session.commit()
            return user, None
        except IntegrityError as e:
            db.session.rollback()
            log.error(f"Failed to create admin user: {e}")
            return None, "Failed to create admin user. Ensure the data is unique and valid."
        except Exception as e:
            db.session.rollback()
            log.exception("Unexpected error during admin user creation")
            return None, f"Unexpected error: {e}"

    @staticmethod
    def set_device_type(device_type: str, device_location: str):
        """Save the selected device type and location, returning the next setup endpoint."""
        dt_setting = Setting.get_by_name('device_type')
        dl_setting = Setting.get_by_name('device_location')
        dt_setting.value = device_type
        dl_setting.value = device_location
        db.session.add(dt_setting)
        db.session.add(dl_setting)
        next_endpoint = f"setup.{device_location}"
        stage_setting = Setting.get_by_name('setup_stage', default=None)
        if stage_setting:
            stage_map = {'setup.local': 3, 'setup.network': 2}
            stage_setting.value = stage_map.get(next_endpoint, 0)
            db.session.add(stage_setting)
        db.session.commit()
        return next_endpoint

    @staticmethod
    def find_usb_devices():
        """Scan for USB-connected AlarmDecoder devices (AD2USB)."""
        system = platform.system()
        if system not in ['Darwin', 'Windows']:
            pattern = '/dev/ttyUSB*'
        else:
            pattern = '/dev/tty.usb*'
        ports = sorted(glob.glob(pattern))
        devices = []
        for port in ports:
            dev_path = port if port.startswith('/dev/') else os.path.join('/dev', os.path.basename(port))
            devices.append(dev_path)
        return devices

    @staticmethod
    def save_local_device_settings(device_type: str, device_path: str, baudrate: int, confirm_management: bool):
        """Save settings for a locally connected device. Returns (next_endpoint, warning_message)."""
        path_setting = Setting.get_by_name('device_path')
        baud_setting = Setting.get_by_name('device_baudrate')
        manage_setting = Setting.get_by_name('managed_ser2sock')
        path_setting.value = device_path
        baud_setting.value = baudrate
        manage_setting.value = bool(confirm_management)
        db.session.add(path_setting)
        db.session.add(baud_setting)
        db.session.add(manage_setting)
        next_endpoint = 'setup.sslserver' if confirm_management else 'setup.device'
        stage_setting = Setting.get_by_name('setup_stage', default=None)
        if stage_setting:
            stage_map = {'setup.sslserver': 5, 'setup.device': 6}
            stage_setting.value = stage_map.get(next_endpoint, 6)
            db.session.add(stage_setting)
        db.session.commit()
        warning = None
        if not confirm_management:
            if ser2sock.exists():
                try:
                    ser2sock.stop()
                    cfg_path = Setting.get_by_name('ser2sock_config_path', default='/etc/ser2sock').value
                    ser2sock.update_config(cfg_path, device_path='')
                except OSError:
                    warning = "ser2sock is running and could not be stopped. Please stop it manually."
        return next_endpoint, warning

    @staticmethod
    def save_network_device_settings(device_address: str, device_port: int, use_ssl: bool):
        """Save settings for a network-connected device. Returns next endpoint."""
        addr_setting = Setting.get_by_name('device_address')
        port_setting = Setting.get_by_name('device_port')
        ssl_setting = Setting.get_by_name('use_ssl')
        addr_setting.value = device_address.strip()
        port_setting.value = device_port
        ssl_setting.value = bool(use_ssl)
        db.session.add(addr_setting)
        db.session.add(port_setting)
        db.session.add(ssl_setting)
        next_endpoint = 'setup.sslclient' if use_ssl else 'setup.device'
        stage_setting = Setting.get_by_name('setup_stage', default=None)
        if stage_setting:
            stage_map = {'setup.sslclient': 4, 'setup.device': 6}
            stage_setting.value = stage_map.get(next_endpoint, 6)
            db.session.add(stage_setting)
        db.session.commit()
        return next_endpoint

    @staticmethod
    def upload_ssl_certificates(ca_file, client_cert_file, client_key_file):
        """Store uploaded CA and client certificates for secure connection. Returns error message or None."""
        try:
            ca_data = ca_file.stream.read() if ca_file and hasattr(ca_file, 'stream') else b''
            cert_data = client_cert_file.stream.read() if client_cert_file and hasattr(client_cert_file, 'stream') else b''
            key_data = client_key_file.stream.read() if client_key_file and hasattr(client_key_file, 'stream') else b''
            # Save CA certificate
            try:
                ca_cert = Certificate.query.filter_by(name='AlarmDecoder CA').first()
                if ca_cert is None:
                    ca_cert = Certificate(name='AlarmDecoder CA', certificate=ca_data, key=b'', type=CA)
                    db.session.add(ca_cert)
                else:
                    ca_cert.certificate = ca_data
                    ca_cert.key = b''
            except MultipleResultsFound:
                db.session.rollback()
                log.error("Duplicate 'AlarmDecoder CA' certificates found.")
                return "Duplicate CA certificate records found."
            # Save internal client certificate
            try:
                internal_cert = Certificate.query.filter_by(name='AlarmDecoder Internal').first()
                if internal_cert is None:
                    internal_cert = Certificate(name='AlarmDecoder Internal', certificate=cert_data, key=key_data, type=INTERNAL)
                    db.session.add(internal_cert)
                else:
                    internal_cert.certificate = cert_data
                    internal_cert.key = key_data
            except MultipleResultsFound:
                db.session.rollback()
                log.error("Duplicate 'AlarmDecoder Internal' certificates found.")
                return "Duplicate internal certificate records found."
            # Enable SSL usage
            ssl_setting = Setting.get_by_name('use_ssl')
            ssl_setting.value = True
            db.session.add(ssl_setting)
            db.session.commit()
            log.info("SSL client certificates updated successfully.")
            return None
        except Exception as e:
            db.session.rollback()
            log.exception("Error saving SSL certificates")
            return f"Failed to save SSL certificates: {e}"

    @staticmethod
    def _generate_certs():
        """Generate default CA, Server, and Internal certificates if not present."""
        ca = Certificate.query.filter_by(type=CA).first()
        if ca is None:
            ca = Certificate(name='AlarmDecoder CA', description='CA cert for AlarmDecoder', status=CERT_ACTIVE, type=CA)
            ca.generate(common_name='AlarmDecoder CA')
            db.session.add(ca)
            db.session.commit()
            server_cert = Certificate(name='AlarmDecoder Server', description='Server cert for ser2sock', status=CERT_ACTIVE, type=SERVER, ca_id=ca.id)
            server_cert.generate(common_name='AlarmDecoder Server', parent=ca)
            db.session.add(server_cert)
            internal_cert = Certificate(name='AlarmDecoder Internal', description='Internal cert for webapp', status=CERT_ACTIVE, type=INTERNAL, ca_id=ca.id)
            internal_cert.generate(common_name='AlarmDecoder Internal', parent=ca)
            db.session.add(internal_cert)
            db.session.commit()

    @staticmethod
    def configure_ser2sock(use_ssl: bool, config_path: str, device_address: str, device_port: int):
        """Configure and start the managed ser2sock service. Returns error message or None."""
        manage_setting = Setting.get_by_name('manage_ser2sock')
        ssl_setting = Setting.get_by_name('use_ssl')
        cfg_path_setting = Setting.get_by_name('ser2sock_config_path')
        addr_setting = Setting.get_by_name('device_address')
        port_setting = Setting.get_by_name('device_port')
        loc_setting = Setting.get_by_name('device_location')
        manage_setting.value = True
        ssl_setting.value = bool(use_ssl)
        cfg_path_setting.value = config_path
        addr_setting.value = device_address.strip()
        port_setting.value = device_port
        loc_setting.value = 'network'
        for s in [manage_setting, ssl_setting, cfg_path_setting, addr_setting, port_setting, loc_setting]:
            db.session.add(s)
        stage_setting = Setting.get_by_name('setup_stage', default=None)
        if stage_setting:
            stage_setting.value = 6
            db.session.add(stage_setting)
        db.session.commit()
        try:
            if use_ssl:
                SetupService._generate_certs()
            ca_cert = Certificate.query.filter_by(type=CA).first()
            server_cert = Certificate.query.filter_by(type=SERVER).first()
            config_opts = {
                'device_path': Setting.get_by_name('device_path').value or '',
                'device_port': device_port,
                'device_baudrate': Setting.get_by_name('device_baudrate').value,
                'raw_device_mode': 1,
                'use_ssl': bool(use_ssl),
                'ca_cert': ca_cert,
                'server_cert': server_cert
            }
            ser2sock.update_config(config_path, **config_opts)
        except RuntimeError as err:
            log.error(f"ser2sock configuration error: {err}")
            return str(err)
        except Exception as err:
            # Includes ser2sock.HupFailed or ser2sock.NotFound or others
            log.exception("ser2sock setup failed")
            if hasattr(err, 'args'):
                err_msg = err.args[0] if err.args else str(err)
            else:
                err_msg = str(err)
            if 'HupFailed' in err_msg:
                return f"We had an issue restarting ser2sock: {err}"
            if 'NotFound' in err_msg:
                return "ser2sock was not found on the system."
            return f"Unexpected Error: {err}"
        return None

    @staticmethod
    def save_email_settings(mail_server: str, mail_port: int, use_tls: bool, use_auth: bool, username: str, password: str, default_sender: str):
        """Save SMTP email configuration settings."""
        server_setting = Setting.get_by_name('system_email_server', default='')
        port_setting = Setting.get_by_name('system_email_port', default=25)
        tls_setting = Setting.get_by_name('system_email_tls', default=False)
        auth_setting = Setting.get_by_name('system_email_auth', default=False)
        user_setting = Setting.get_by_name('system_email_username', default='')
        pass_setting = Setting.get_by_name('system_email_password', default='')
        from_setting = Setting.get_by_name('system_email_from', default='')
        server_setting.value = mail_server.strip()
        port_setting.value = int(mail_port)
        tls_setting.value = bool(use_tls)
        auth_setting.value = bool(use_auth)
        user_setting.value = username.strip() if username else ''
        if password and password.strip():
            pass_setting.value = password
        from_setting.value = default_sender.strip()
        for s in [server_setting, port_setting, tls_setting, auth_setting, user_setting, pass_setting, from_setting]:
            db.session.add(s)
        try:
            db.session.commit()
            log.info(f"Email settings updated: {mail_server}:{mail_port} TLS={use_tls} Auth={use_auth}")
            return None
        except Exception as e:
            db.session.rollback()
            log.exception("Failed to save email settings")
            return f"Failed to save email settings: {e}"
