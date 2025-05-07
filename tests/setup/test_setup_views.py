from tests import TestCase
from ad2web.settings.models import Setting
from ad2web.user.models import User
from ad2web.certificate.models import Certificate
from flask import url_for
from io import BytesIO
from unittest.mock import patch

class TestSetupViews(TestCase):
    def _ensure_first_run(self):
        """Make sure the setup is marked as not complete."""
        sc = Setting.get_by_name('setup_complete', default=False)
        sc.value = False
        self.db.session.add(sc)
        self.db.session.commit()

    def test_setup_index_access(self):
        """Initial GET on /setup/ should return the index page for first-run."""
        self._ensure_first_run()
        response = self.client.get('/setup/')
        self.assert_200(response)
        assert b'AlarmDecoder Device Setup' in response.data

    def test_device_detection_usb_no_device_flash(self):
        """Local step (AD2USB) should flash error if no USB devices found."""
        self._ensure_first_run()
        # Set device_type to AD2USB and device_location to local via /setup/type
        resp = self.client.post('/setup/type', data={'device_type': 'AD2USB', 'device_location': 'local'})
        # Now GET /setup/local, patching _iterate_usb to simulate no devices
        with patch('ad2web.setup.views._iterate_usb', return_value={}):
            resp = self.client.get('/setup/local')
            self.assert_200(resp)
            # Should contain flash error about no devices found
            assert b'No devices found' in resp.data
            # The form should still render (with empty choices)
            assert b'Local Device Settings' in resp.data

    def test_device_detection_usb_device_found(self):
        """Local step (AD2USB) populates device choices if USB device is found."""
        self._ensure_first_run()
        self.client.post('/setup/type', data={'device_type': 'AD2USB', 'device_location': 'local'})
        fake_devices = {'/dev/ttyUSB0': '/dev/ttyUSB0'}
        with patch('ad2web.setup.views._iterate_usb', return_value=fake_devices):
            resp = self.client.get('/setup/local')
            self.assert_200(resp)
            # The form select should include the found device path
            assert b'/dev/ttyUSB0' in resp.data

    def test_setup_type_and_redirect(self):
        """Posting to /setup/type should redirect to the appropriate next step."""
        self._ensure_first_run()
        # Local device selection
        resp = self.client.post('/setup/type', data={'device_type': 'AD2SERIAL', 'device_location': 'local'})
        self.assert_redirects(resp, '/setup/local')
        # Network device selection
        self._ensure_first_run()
        resp = self.client.post('/setup/type', data={'device_type': 'AD2PI', 'device_location': 'network'})
        self.assert_redirects(resp, '/setup/network')

    def test_setup_local_validation_error(self):
        """Posting invalid data to /setup/local should not redirect and should show errors."""
        self._ensure_first_run()
        # Prepare state by posting /setup/type to select a local device (non-USB for simplicity)
        self.client.post('/setup/type', data={'device_type': 'AD2SERIAL', 'device_location': 'local'})
        # Patch os.path.exists to always return False to simulate invalid path
        with patch('ad2web.setup.views.os.path.exists', return_value=False):
            resp = self.client.post('/setup/local', data={
                'device_path': '/invalid/path', 'baudrate': 115200
            })
            # No redirect on validation failure
            assert resp.status_code == 200
            # Should show "Path does not exist" error message
            assert b'Path does not exist' in resp.data
            # Ensure we remain on the local setup page
            assert b'Local Device Settings' in resp.data

    def test_setup_local_ser2sock_stop_warning(self):
        """If ser2sock stop fails, /setup/local should flash a warning."""
        self._ensure_first_run()
        self.client.post('/setup/type', data={'device_type': 'AD2SERIAL', 'device_location': 'local'})
        # Ensure ser2sock is "present"
        with patch('ad2web.setup.views.ser2sock.exists', return_value=True), \
             patch('ad2web.setup.views.ser2sock.stop', side_effect=OSError), \
             patch('ad2web.setup.views.ser2sock.update_config', return_value=None):
            resp = self.client.post('/setup/local', data={
                'device_path': '/dev/ttyS0', 'baudrate': 19200, 'confirm_management': False
            }, follow_redirects=True)
            # We expect a warning flash and to be on same page (since follow_redirects True, we'll get the rendered local page again)
            assert b'failed to stop it' in resp.data or b'communication issues' in resp.data
            # Should not proceed to next step due to warning
            assert b'Local Device Settings' in resp.data

    def test_setup_network_validation_error(self):
        """Posting invalid data to /setup/network should show errors and not redirect."""
        self._ensure_first_run()
        self.client.post('/setup/type', data={'device_type': 'AD2PI', 'device_location': 'network'})
        resp = self.client.post('/setup/network', data={
            'device_address': '', 'device_port': 100
        })
        # Required and out-of-range errors
        assert resp.status_code == 200
        assert b'This field is required' in resp.data or b'required' in resp.data
        assert b'1024' in resp.data  # port range hint
        # reserved port example
        resp = self.client.post('/setup/network', data={
            'device_address': '127.0.0.1', 'device_port': 80
        })
        assert resp.status_code == 200
        assert b'reserved by other services' in resp.data

    def test_setup_sslclient_file_upload_and_validation(self):
        """Posting to /setup/sslclient with missing files should show errors, and with files should redirect."""
        self._ensure_first_run()
        # Simulate having gone through network with SSL to reach sslclient step
        self.client.post('/setup/type', data={'device_type': 'AD2PI', 'device_location': 'network'})
        self.client.post('/setup/network', data={'device_address': 'test', 'device_port': 10000, 'ssl': True})
        # Missing files scenario
        resp = self.client.post('/setup/sslclient', data={}, follow_redirects=False)
        # Should not redirect because form invalid (WTForms will treat missing files as validation errors)
        assert resp.status_code == 200
        assert b'This field is required' in resp.data
        # Now provide dummy files
        data = {
            'ca_cert': (BytesIO(b'CACERT'), 'ca.crt'),
            'cert': (BytesIO(b'CLIENTCERT'), 'client.crt'),
            'key': (BytesIO(b'CLIENTKEY'), 'client.key')
        }
        resp = self.client.post('/setup/sslclient', content_type='multipart/form-data', data=data, follow_redirects=False)
        # After successful upload, should redirect to /setup/device
        self.assert_redirects(resp, '/setup/device')
        # Certificates should be saved to DB
        ca = Certificate.query.filter_by(name='AlarmDecoder CA').first()
        internal = Certificate.query.filter_by(name='AlarmDecoder Internal').first()
        assert ca is not None and internal is not None

    def test_setup_sslserver_encryption_option(self):
        """Posting to /setup/sslserver should start ser2sock config and handle errors."""
        self._ensure_first_run()
        # Simulate going through local with managed_ser2sock = True to reach sslserver
        self.client.post('/setup/type', data={'device_type': 'AD2SERIAL', 'device_location': 'local'})
        self.client.post('/setup/local', data={'device_path': '/dev/ttyS0', 'baudrate': 115200, 'confirm_management': True})
        # Case: choose not to encrypt ser2sock (ssl unchecked)
        with patch('ad2web.setup.views.ser2sock.update_config', return_value=None):
            resp = self.client.post('/setup/sslserver', data={
                'config_path': '/tmp', 'device_address': '0.0.0.0', 'device_port': 10000
            }, follow_redirects=False)
            # Should redirect to device step on success
            self.assert_redirects(resp, '/setup/device')
        # Case: ser2sock not found error
        with patch('ad2web.setup.views.ser2sock.update_config', side_effect=Exception("NotFound simulated")), \
             patch('ad2web.setup.views.ser2sock.NotFound', Exception):
            resp = self.client.post('/setup/sslserver', data={
                'config_path': '/tmp', 'device_address': '0.0.0.0', 'device_port': 10000
            })
            # Should not redirect, should flash error about not found
            assert resp.status_code == 200
            assert b'not able to find ser2sock' in resp.data or b'not found' in resp.data
        # Case: HupFailed error
        class DummyHupFail(Exception): pass
        DummyHupFail.__name__ = 'HupFailed'
        with patch('ad2web.setup.views.ser2sock.update_config', side_effect=DummyHupFail("HUP fail")), \
             patch('ad2web.setup.views.ser2sock.HupFailed', DummyHupFail):
            resp = self.client.post('/setup/sslserver', data={
                'config_path': '/tmp', 'device_address': '0.0.0.0', 'device_port': 10000, 'ssl': True
            })
            assert resp.status_code == 200
            assert b'issue restarting ser2sock' in resp.data

    def test_setup_device_step_and_post(self):
        """Full device configuration step populates and saves settings."""
        self._ensure_first_run()
        # Prepare previous steps: choose local device (no ser2sock management to go directly to device)
        self.client.post('/setup/type', data={'device_type': 'AD2SERIAL', 'device_location': 'local'})
        self.client.post('/setup/local', data={'device_path': '/dev/ttyS0', 'baudrate': 19200, 'confirm_management': False})
        # GET device should load form
        resp = self.client.get('/setup/device')
        self.assert_200(resp)
        assert b'Panel Type' in resp.data
        # POST with valid data
        resp = self.client.post('/setup/device', data={
            'panel_mode': 0,  # ADEMCO
            'keypad_address': 19,
            'address_mask': 'FFFFFFFF',
            'internal_address_mask': 'FFFFFFFF',
            'zone_expanders': ['1', '2'],
            'relay_expanders': ['1'],
            'lrr_enabled': True,
            'deduplicate': True
        }, follow_redirects=False)
        # Should redirect to /setup/test after successful save
        self.assert_redirects(resp, '/setup/test')
        # Verify settings saved
        assert Setting.get_by_name('keypad_address').value == 19
        assert Setting.get_by_name('lrr_enabled').value is True
        # Stage should now be testing stage (7)
        stage = Setting.get_by_name('setup_stage')
        assert stage.value == 7

    def test_setup_test_step_behavior(self):
        """The test step should redirect to account or skip if already complete."""
        self._ensure_first_run()
        # Simulate reaching test step with setup not complete -> should go to account
        resp = self.client.get('/setup/test')
        self.assert_200(resp)
        # Simulate user clicking Continue (POST to test next)
        resp = self.client.post('/setup/test')
        self.assert_redirects(resp, '/setup/account')
        # Now simulate setup already complete scenario
        sc = Setting.get_by_name('setup_complete', default=False)
        sc.value = True
        self.db.session.add(sc); self.db.session.commit()
        resp = self.client.post('/setup/test', follow_redirects=False)
        self.assert_redirects(resp, '/')
        # Should also flash 'Setup complete!' message
        final = self.client.get('/')
        assert b'Setup complete!' in final.data or b'Configuration updated' in final.data

    def test_setup_account_creation_and_validation(self):
        """Account creation step should create a new user or handle validation errors."""
        self._ensure_first_run()
        # Missing fields -> validation error
        resp = self.client.post('/setup/account', data={}, follow_redirects=False)
        assert resp.status_code == 200
        assert b'This field is required' in resp.data
        # Valid submission -> should create user and redirect to main index
        resp = self.client.post('/setup/account', data={
            'name': 'installer',
            'email': 'installer@example.com',
            'password': 'adwpwd123',
            'password_again': 'adwpwd123'
        }, follow_redirects=False)
        self.assert_redirects(resp, '/')
        # User should exist in DB now
        new_user = User.query.filter_by(name='installer').first()
        assert new_user is not None
        # setup_complete should be True
        assert Setting.get_by_name('setup_complete').value is True
