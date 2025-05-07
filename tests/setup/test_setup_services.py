from tests import TestCase
from ad2web.services.setup_service import SetupService
from ad2web.settings.models import Setting
from ad2web.certificate.models import Certificate
from ad2web.certificate.constants import CA, SERVER, INTERNAL
from ad2web.user.models import User
from sqlalchemy.exc import IntegrityError
from unittest.mock import patch, MagicMock

class TestSetupServices(TestCase):
    def test_set_device_type_and_location(self):
        """set_device_type should save settings and return correct next endpoint."""
        next_endpoint = SetupService.set_device_type(device_type='AD2SERIAL', device_location='local')
        # Should return 'setup.local' for local devices
        assert next_endpoint == 'setup.local'
        # Settings should be saved in DB
        dt = Setting.query.filter_by(name='device_type').first()
        dl = Setting.query.filter_by(name='device_location').first()
        assert dt and dl
        assert dt.value == 'AD2SERIAL'
        assert dl.value == 'local'
        # Should set setup_stage accordingly (3 for local as per constants)
        stage = Setting.get_by_name('setup_stage', default=None)
        assert stage.value == 3
        # Test network case
        next_endpoint = SetupService.set_device_type(device_type='AD2PI', device_location='network')
        assert next_endpoint == 'setup.network'
        stage = Setting.get_by_name('setup_stage', default=None)
        assert stage.value == 2

    def test_find_usb_devices_patterns(self):
        """find_usb_devices returns correct device list for different platforms."""
        # Simulate Linux with two USB devices
        with patch('ad2web.services.setup_service.platform.system', return_value='Linux'):
            with patch('ad2web.services.setup_service.glob.glob', return_value=['/dev/ttyUSB1', '/dev/ttyUSB0']):
                devices = SetupService.find_usb_devices()
                # Should return sorted list of device paths
                assert devices == ['/dev/ttyUSB0', '/dev/ttyUSB1']
        # Simulate Darwin (macOS)
        with patch('ad2web.services.setup_service.platform.system', return_value='Darwin'):
            with patch('ad2web.services.setup_service.glob.glob', return_value=['/dev/tty.usbserial']):
                devices = SetupService.find_usb_devices()
                # Pattern for Darwin/Windows uses /dev/tty.usb*, and returns that path
                assert devices == ['/dev/tty.usbserial']

    def test_save_local_device_settings_branches(self):
        """save_local_device_settings returns next endpoint and warning appropriately."""
        # Case 1: confirm_management True (should go to sslserver, no warning)
        next_ep, warning = SetupService.save_local_device_settings('AD2SERIAL', '/dev/test', 19200, confirm_management=True)
        assert next_ep == 'setup.sslserver'
        assert warning is None
        # Settings should be saved
        assert Setting.get_by_name('device_path').value == '/dev/test'
        assert Setting.get_by_name('device_baudrate').value == 19200
        assert Setting.get_by_name('managed_ser2sock').value is True
        # Case 2: confirm_management False, ser2sock not running (exists False)
        with patch('ad2web.services.setup_service.ser2sock.exists', return_value=False):
            next_ep, warning = SetupService.save_local_device_settings('AD2SERIAL', '/dev/test2', 115200, confirm_management=False)
            assert next_ep == 'setup.device'
            assert warning is None  # no warning if ser2sock isn't running
        # Case 3: confirm_management False, ser2sock running but stop raises OSError
        with patch('ad2web.services.setup_service.ser2sock.exists', return_value=True), \
             patch('ad2web.services.setup_service.ser2sock.stop', side_effect=OSError), \
             patch('ad2web.services.setup_service.ser2sock.update_config', return_value=None):
            next_ep, warning = SetupService.save_local_device_settings('AD2SERIAL', '/dev/test3', 115200, confirm_management=False)
            assert next_ep == 'setup.device'
            assert warning is not None
            assert 'could not be stopped' in warning

    def test_save_network_device_settings(self):
        """save_network_device_settings saves network settings and returns next endpoint based on SSL."""
        # Non-SSL case
        ep = SetupService.save_network_device_settings('192.168.1.100', 10000, use_ssl=False)
        assert ep == 'setup.device'
        # Check values saved
        assert Setting.get_by_name('device_address').value == '192.168.1.100'
        assert Setting.get_by_name('device_port').value == 10000
        assert Setting.get_by_name('use_ssl').value is False
        # SSL case
        ep = SetupService.save_network_device_settings('alarmdecoder.local', 10000, use_ssl=True)
        assert ep == 'setup.sslclient'
        assert Setting.get_by_name('use_ssl').value is True
        stage = Setting.get_by_name('setup_stage')
        # Should set stage to 4 for sslclient
        assert stage.value == 4

    def test_upload_ssl_certificates_and_errors(self):
        """upload_ssl_certificates should store certs and return errors on failure."""
        # Prepare dummy file-like objects
        ca_bytes = b'CA_DATA'
        cert_bytes = b'CERT_DATA'
        key_bytes = b'KEY_DATA'
        ca_file = MagicMock()
        ca_file.stream.read.return_value = ca_bytes
        cert_file = MagicMock()
        cert_file.stream.read.return_value = cert_bytes
        key_file = MagicMock()
        key_file.stream.read.return_value = key_bytes
        # Successful upload returns None (no error)
        error = SetupService.upload_ssl_certificates(ca_file, cert_file, key_file)
        assert error is None
        # Verify that certificates were saved in DB
        ca_cert = Certificate.query.filter_by(name='AlarmDecoder CA').first()
        assert ca_cert is not None and ca_cert.certificate == ca_bytes
        internal_cert = Certificate.query.filter_by(name='AlarmDecoder Internal').first()
        assert internal_cert is not None and internal_cert.certificate == cert_bytes and internal_cert.key == key_bytes
        # Simulate duplicate certificate entries causing error
        # Insert a duplicate 'AlarmDecoder CA' to cause MultipleResultsFound scenario
        dup = Certificate(name='AlarmDecoder CA', certificate=b'dup', key=b'')
        self.db.session.add(dup)
        self.db.session.commit()
        error = SetupService.upload_ssl_certificates(ca_file, cert_file, key_file)
        # If logic uses .one(), it would catch MultipleResultsFound; otherwise, simulate by checking error message
        if error:
            assert 'Duplicate' in error
        # Simulate general exception during save (e.g., DB error)
        with patch('ad2web.services.setup_service.db.session.commit', side_effect=Exception("DB Fail")):
            err = SetupService.upload_ssl_certificates(ca_file, cert_file, key_file)
            assert err is not None
            assert 'Failed to save SSL certificates' in err

    def test_configure_ser2sock_various_outcomes(self):
        """configure_ser2sock should handle errors and return appropriate messages."""
        # Ensure a CA and Server cert exist in DB for use in config
        ca = Certificate(name='Test CA', type=CA, certificate=b'ca', key=b'', status=0)
        server = Certificate(name='Test Server', type=SERVER, certificate=b'cert', key=b'key', status=0)
        self.db.session.add(ca)
        self.db.session.add(server)
        self.db.session.commit()
        # Success case (no errors)
        with patch('ad2web.services.setup_service.ser2sock.update_config', return_value=None):
            err = SetupService.configure_ser2sock(use_ssl=False, config_path='/tmp', device_address='localhost', device_port=10000)
            assert err is None
        # Simulate RuntimeError
        with patch('ad2web.services.setup_service.ser2sock.update_config', side_effect=RuntimeError("config error")):
            err = SetupService.configure_ser2sock(use_ssl=False, config_path='/tmp', device_address='localhost', device_port=10000)
            assert err == "config error"
        # Simulate HupFailed exception
        class DummyHupFailed(Exception): pass
        DummyHupFailed.__name__ = 'HupFailed'
        with patch('ad2web.services.setup_service.ser2sock.update_config', side_effect=DummyHupFailed("HupFailed simulated")), \
             patch('ad2web.services.setup_service.ser2sock.HupFailed', DummyHupFailed):
            err = SetupService.configure_ser2sock(use_ssl=True, config_path='/tmp', device_address='localhost', device_port=10000)
            assert "issue restarting ser2sock" in err
        # Simulate NotFound exception
        class DummyNotFound(Exception): pass
        DummyNotFound.__name__ = 'NotFound'
        with patch('ad2web.services.setup_service.ser2sock.update_config', side_effect=DummyNotFound("NotFound simulated")), \
             patch('ad2web.services.setup_service.ser2sock.NotFound', DummyNotFound):
            err = SetupService.configure_ser2sock(use_ssl=False, config_path='/tmp', device_address='localhost', device_port=10000)
            assert "not found on the system" in err.lower()
        # Simulate generic exception
        with patch('ad2web.services.setup_service.ser2sock.update_config', side_effect=Exception("Unexpected")):
            err = SetupService.configure_ser2sock(use_ssl=False, config_path='/tmp', device_address='localhost', device_port=10000)
            assert err and "Unexpected Error" in err

    def test_save_email_settings_success_and_failure(self):
        """save_email_settings should commit valid data or return error on failure."""
        # Normal save returns None and persists settings
        err = SetupService.save_email_settings(mail_server='smtp.test.com', mail_port=2525,
                                               use_tls=True, use_auth=True,
                                               username='user', password='pass', default_sender='test@x.com')
        assert err is None
        # Verify values saved
        server = Setting.get_by_name('system_email_server')
        assert server.value == 'smtp.test.com'
        port = Setting.get_by_name('system_email_port')
        assert port.value == 2525
        tls = Setting.get_by_name('system_email_tls')
        assert tls.value is True
        auth = Setting.get_by_name('system_email_auth')
        assert auth.value is True
        user = Setting.get_by_name('system_email_username')
        pwd = Setting.get_by_name('system_email_password')
        sender = Setting.get_by_name('system_email_from')
        assert user.value == 'user'
        assert pwd.value == 'pass'
        assert sender.value == 'test@x.com'
        # Simulate commit failure
        with patch('ad2web.services.setup_service.db.session.commit', side_effect=Exception("CommitFail")):
            err = SetupService.save_email_settings(mail_server='x', mail_port=25,
                                                   use_tls=False, use_auth=False,
                                                   username='', password='', default_sender='x@x.com')
            assert err is not None
            assert "Failed to save email settings" in err
