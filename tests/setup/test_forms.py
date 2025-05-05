import io
import os
import pytest
from wtforms.validators import ValidationError
from ad2web.setup import forms as setup_forms

# Test cases for forms in the AlarmDecoder setup wizard.

def test_device_type_form_defaults():
    """DeviceTypeForm should have default values and validate by default."""
    form = setup_forms.DeviceTypeForm()
    # Defaults are AD2USB and local
    assert form.device_type.data == 'AD2USB'
    assert form.device_location.data == 'local'
    assert form.validate()  # default values are valid

def test_network_device_form_validation():
    """NetworkDeviceForm requires address and port within range, and rejects reserved ports."""
    # Missing both fields -> expect validation failure on required fields
    form = setup_forms.NetworkDeviceForm(data={})
    assert not form.validate()
    assert 'device_address' in form.errors and 'device_port' in form.errors
    # Address provided, port out of range (too low)
    form = setup_forms.NetworkDeviceForm(data={'device_address': 'localhost', 'device_port': 1000})
    assert not form.validate()
    # NumberRange should trigger an error for port below 1024
    assert 'device_port' in form.errors
    # Port reserved value (e.g., 80) should trigger NoneOf validator message
    form = setup_forms.NetworkDeviceForm(data={'device_address': 'localhost', 'device_port': 80})
    assert not form.validate()
    assert 'device_port' in form.errors
    # The error message should mention reserved port (custom NoneOf message)
    assert any('reserved' in msg for msg in form.errors['device_port'])
    # Valid data
    form = setup_forms.NetworkDeviceForm(data={'device_address': '127.0.0.1', 'device_port': 10000})
    assert form.validate()

def test_local_device_form_path_exists(monkeypatch):
    """LocalDeviceForm requires device_path to exist and respects PathExists validator."""
    # Patch os.path.exists to simulate non-existing and existing paths
    monkeypatch.setattr(os.path, 'exists', lambda p: False)
    form = setup_forms.LocalDeviceForm(data={'device_path': '/dev/test', 'baudrate': 115200, 'confirm_management': True})
    assert not form.validate()
    # Expect a "Path does not exist" error on device_path
    assert 'device_path' in form.errors
    assert any('does not exist' in msg for msg in form.errors['device_path'])
    # Now simulate an existing path
    monkeypatch.setattr(os.path, 'exists', lambda p: True)
    form = setup_forms.LocalDeviceForm(data={'device_path': '/dev/test', 'baudrate': 115200, 'confirm_management': False})
    assert form.validate()
    # confirm_management is optional (boolean), should default to False if not provided
    form = setup_forms.LocalDeviceForm(data={'device_path': '/dev/test', 'baudrate': 115200})
    assert form.validate()
    # For AD2USB variant (LocalDeviceFormUSB), ensure choices get set externally, but validation can proceed if path is valid
    form_usb = setup_forms.LocalDeviceFormUSB()
    # set choices and data
    form_usb.device_path.choices = [('/dev/ttyUSB0', '/dev/ttyUSB0')]
    form_usb = setup_forms.LocalDeviceFormUSB(data={'device_path': '/dev/ttyUSB0', 'baudrate': 115200, 'confirm_management': True})
    form_usb.device_path.choices = [('/dev/ttyUSB0', '/dev/ttyUSB0')]
    monkeypatch.setattr(os.path, 'exists', lambda p: True)
    assert form_usb.validate()

def test_device_form_validators():
    """DeviceForm fields enforce proper ranges and formats."""
    # Invalid keypad_address (out of range)
    form = setup_forms.DeviceForm(data={
        'panel_mode': setup_forms.ADEMCO,
        'keypad_address': 0,  # out of valid range (1-99)
        'address_mask': 'FFFFFFFF',
        'internal_address_mask': 'FFFFFFFF',
        'zone_expanders': [],
        'relay_expanders': [],
        'lrr_enabled': False,
        'deduplicate': False
    })
    assert not form.validate()
    assert 'keypad_address' in form.errors  # NumberRange should fail
    # Invalid address mask (non-hexadecimal characters)
    form = setup_forms.DeviceForm(data={
        'panel_mode': setup_forms.ADEMCO,
        'keypad_address': 18,
        'address_mask': 'ZZZZZZZZ',  # not hex
        'internal_address_mask': 'FFFFFFFF',
        'zone_expanders': [],
        'relay_expanders': []
    })
    assert not form.validate()
    assert 'address_mask' in form.errors
    assert any('hexadecimal' in msg for msg in form.errors['address_mask'])
    # Valid data (all required fields provided within bounds)
    form = setup_forms.DeviceForm(data={
        'panel_mode': setup_forms.ADEMCO,
        'keypad_address': 18,
        'address_mask': 'FFFFFFFF',
        'internal_address_mask': 'FFFFFFFF',
        'zone_expanders': ['1', '3'],  # select some expanders
        'relay_expanders': ['2'],
        'lrr_enabled': True,
        'deduplicate': False
    })
    assert form.validate()
    # zone_expanders and relay_expanders should accept list of strings as choices
    assert set(form.zone_expanders.data) == {'1', '3'}
    assert set(form.relay_expanders.data) == {'2'}

def test_email_setup_form_validation():
    """EmailSetupForm (part of SetupWizard) requires proper fields (if present in wizard)."""
    if hasattr(setup_forms, 'EmailSetupForm'):
        EmailForm = setup_forms.EmailSetupForm
        # Missing required fields
        form = EmailForm(data={})
        assert not form.validate()
        # Provide invalid email format in default_sender
        form = EmailForm(data={
            'mail_server': 'smtp.example.com',
            'mail_port': 587,
            'use_tls': True,
            'use_auth': True,
            'username': 'user',
            'password': 'pass',
            'default_sender': 'invalid-email'
        })
        assert not form.validate()
        assert 'default_sender' in form.errors
        # Valid input
        form = EmailForm(data={
            'mail_server': 'smtp.example.com',
            'mail_port': 587,
            'use_tls': True,
            'use_auth': True,
            'username': 'user',
            'password': 'pass',
            'default_sender': 'user@example.com'
        })
        assert form.validate()

def test_create_account_form_validation():
    """CreateAccountForm enforces required fields and matching passwords."""
    form = setup_forms.CreateAccountForm(data={})
    assert not form.validate()
    assert 'name' in form.errors and 'email' in form.errors and 'password' in form.errors
    # Password mismatch
    form = setup_forms.CreateAccountForm(data={
        'name': 'newadmin',
        'email': 'new@example.com',
        'password': 'abcdef12',
        'password_again': 'abcdef13'
    })
    assert not form.validate()
    assert 'password_again' in form.errors
    assert any('match' in msg or 'must be equal' in msg for msg in form.errors['password_again'])
    # Valid input
    form = setup_forms.CreateAccountForm(data={
        'name': 'newadmin',
        'email': 'new@example.com',
        'password': 'abcdef12',
        'password_again': 'abcdef12'
    })
    assert form.validate()
