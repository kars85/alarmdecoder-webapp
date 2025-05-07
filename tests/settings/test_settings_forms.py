import pytest
from types import SimpleNamespace
from ad2web.settings import forms
from ad2web.user.models import User  # assuming user model import path

def validate_form(form, **fields):
    """Utility to fill form fields and trigger validation."""
    for name, value in fields.items():
        setattr(form, name, SimpleNamespace(data=value))
    return form.validate()

@pytest.mark.parametrize("hostname, expected_error", [
    ("", "Hostname must be"),               # empty (InputRequired fails)
    ("toolong" + "a"*60, "must be 1-63 characters"),  # 67 chars total, beyond max
    ("-starts", "must not"),               # starts with invalid punctuation
    ("ends-", "must not"),                 # ends with invalid punctuation
    ("has space", "Invalid characters found"),  # contains space
    ("name_with_underscore", "Invalid characters found"),  # contains invalid char underscore
    ("Valid-Name123", None)                # valid hostname
])
def test_hostsettingsform_validate_hostname(hostname, expected_error):
    form = forms.HostSettingsForm()
    form.hostname.data = hostname
    is_valid = form.validate()
    if expected_error:
        assert not is_valid
        # Collect errors
        errors = sum((field_errors for field_errors in form.errors.values()), [])
        # Check the expected error substring is in the combined error messages
        assert any(expected_error in err for err in errors)
    else:
        assert is_valid

@pytest.mark.parametrize("user_exists, correct_password, expect_valid", [
    (False, False, False),   # user not found
    (True, False, False),    # user found, password wrong
    (True, True, True)       # user found, password correct
])
def test_passwordform_validate_password(monkeypatch, user_exists, correct_password, expect_valid):
    # Prepare dummy current_user and User.get_by_id
    dummy_current_user = SimpleNamespace(id=1)
    monkeypatch.setattr(forms, "current_user", dummy_current_user)
    # Monkeypatch User.get_by_id to return dummy user or None
    if user_exists:
        dummy_user = SimpleNamespace()
        dummy_user.check_password = lambda pwd: correct_password
        monkeypatch.setattr(User, "get_by_id", classmethod(lambda cls, id: dummy_user))
    else:
        monkeypatch.setattr(User, "get_by_id", classmethod(lambda cls, id: None))
    form = forms.PasswordForm()
    form.password.data = "irrelevant"  # input value (actual value checked via dummy_user.check_password)
    form.new_password.data = "NewPass123"
    form.password_again.data = "NewPass123"
    valid = form.validate()
    assert valid is expect_valid
    if not expect_valid:
        assert "Password is wrong." in form.password.errors

def test_passwordform_confirm_mismatch(monkeypatch):
    """Ensure PasswordForm catches mismatched new_password and password_again."""
    # Set up a dummy current_user and user to bypass current password check
    dummy_current_user = SimpleNamespace(id=1)
    dummy_user = SimpleNamespace(check_password=lambda pwd: True)
    monkeypatch.setattr(forms, "current_user", dummy_current_user)
    monkeypatch.setattr(User, "get_by_id", classmethod(lambda cls, id: dummy_user))
    form = forms.PasswordForm()
    form.password.data = "correctcurrent"
    form.new_password.data = "NewPassword1"
    form.password_again.data = "DifferentPassword1"
    valid = form.validate()
    # Should not validate because of EqualTo failure on password_again
    assert not valid
    # WTForms EqualTo default error message
    assert any("must be equal to new_password" in err for err in form.password_again.errors) \
           or any("Passwords must match" in err for err in form.password_again.errors)

@pytest.mark.parametrize("server, port, default_sender, expect_errors", [
    (None, 25, "user@example.com", True),        # missing server (InputRequired fails)
    ("smtp.example.com", None, "user@example.com", True),  # missing port
    ("smtp.example.com", 70000, "user@example.com", True), # invalid port (out of range)
    ("smtp.example.com", 25, "", True),          # missing default sender
    ("smtp.example.com", 587, "user@example.com", False)   # all required fields valid
])
def test_emailconfigureform_validation(server, port, default_sender, expect_errors):
    form = forms.EmailConfigureForm()
    form.mail_server.data = server if server is not None else ""
    form.port.data = port if port is not None else None
    form.tls.data = False
    form.auth_required.data = False
    form.username.data = "user"  # optional
    form.password.data = "pass"  # optional
    form.default_sender.data = default_sender if default_sender is not None else ""
    valid = form.validate()
    if expect_errors:
        assert not valid
        # At least one field should have an error
        assert form.errors
    else:
        assert valid

def test_emailconfigureform_invalid_port_range():
    """Port outside 1-65535 should produce a validation error."""
    form = forms.EmailConfigureForm()
    form.mail_server.data = "smtp.test.com"
    form.port.data = 0  # invalid (too low)
    form.tls.data = False
    form.auth_required.data = False
    form.username.data = ""
    form.password.data = ""
    form.default_sender.data = "test@example.com"
    assert not form.validate()
    assert "Number must be at least 1" in form.port.errors[0] or "greater than or equal to 1" in form.port.errors[0]
    form.port.data = 99999  # too high
    assert not form.validate()
    # Error could mention upper bound
    assert "Number must be between 1 and 65535" in form.port.errors[0] or "between 1 and 65535" in form.port.errors[0]

def test_exportconfigureform_days_to_keep_validation():
    """Days to keep must be between 1 and 255 (Optional allows blank, but if provided outside range -> error)."""
    form = forms.ExportConfigureForm()
    # If no days provided (None), form should still validate (Optional allows blank default usage)
    form.frequency.data = 1  # valid frequency choice (e.g., Daily)
    form.email.data = False
    form.email_address.data = ""
    form.local_file.data = True
    form.local_file_path.data = "/tmp"
    form.days_to_keep.data = None  # simulate field left empty (should default later, but Optional means no error now)
    assert form.validate()
    # Now test out of range values
    form.days_to_keep.data = 0
    assert not form.validate()
    assert any("Number must be at least 1" in err or "greater than or equal to 1" in err
               for err in form.days_to_keep.errors)
    form.days_to_keep.data = 300
    assert not form.validate()
    assert any("Number must be at most 255" in err or "between 1 and 255" in err
               for err in form.days_to_keep.errors)

def test_versioncheckerform_timeout_validation():
    """Timeout must be >= 600 seconds as enforced by NumberRange."""
    form = forms.VersionCheckerForm()
    form.version_checker_timeout.data = 599  # below minimum
    form.version_checker_disable.data = False
    assert not form.validate()
    # Error should indicate the minimum (600)
    assert any("greater than or equal to 600" in err or "at least 600" in err for err in form.version_checker_timeout.errors)
    form.version_checker_timeout.data = 600
    assert form.validate()
