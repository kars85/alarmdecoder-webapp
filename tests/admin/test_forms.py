from werkzeug.datastructures import MultiDict
from ad2web.forms.admin_form import UserForm

def test_userform_valid_data():
    """UserForm should validate True for valid data input (creation scenario)."""
    form = UserForm(formdata=MultiDict({
        "name": "validuser",
        "email": "valid@example.com",
        "password": "validPass1",
        "password_again": "validPass1",
        "role_code": "1",
        "status_code": "2"
    }))
    assert form.validate() is True
    # No errors for completely valid input.


def test_userform_name_required():
    """Name is required in UserForm."""
    form = UserForm(formdata=MultiDict({
        "name": "",  # missing name
        "email": "user@example.com",
        "password": "abc123",
        "password_again": "abc123",
        "role_code": "1",
        "status_code": "2"
    }))
    assert form.validate() is False
    # Should have an error for the 'name' field (InputRequired).
    assert "name" in form.errors


def test_userform_email_required():
    """Email is required in UserForm."""
    form = UserForm(formdata=MultiDict({
        "name": "user",
        "email": "",  # missing email
        "password": "abc123",
        "password_again": "abc123",
        "role_code": "1",
        "status_code": "2"
    }))
    assert form.validate() is False
    assert "email" in form.errors  # email field should have an InputRequired error


def test_userform_password_length_and_confirmation():
    """Password must meet length requirements and confirm password must match."""
    # Password too short (5 chars) and mismatch with confirmation.
    form = UserForm(formdata=MultiDict({
        "name": "user",
        "email": "user@example.com",
        "password": "12345",       # 5 chars, below min length 6
        "password_again": "54321", # mismatch
        "role_code": "1",
        "status_code": "2"
    }))
    assert form.validate() is False
    # Expect length error on 'password' and mismatch error on 'password'.
    errors = form.errors.get("password", [])
    # Check that at least one error corresponds to length and one to mismatch.
    assert any("6 characters" in msg or "at least 6" in msg for msg in errors)  # length error
    assert any("match" in msg for msg in errors)  # "Passwords must match" error


def test_userform_password_confirmation_mismatch():
    """Mismatched password and password_again triggers validation error."""
    form = UserForm(formdata=MultiDict({
        "name": "user",
        "email": "user@example.com",
        "password": "mypassword",
        "password_again": "differentpass",
        "role_code": "1",
        "status_code": "2"
    }))
    assert form.validate() is False
    # The 'password' field should have an error about matching.
    assert "password" in form.errors
    assert any("match" in msg for msg in form.errors["password"])


def test_userform_edit_allows_blank_password():
    """When edit=True, UserForm should allow blank password fields (no requirement to change password)."""
    form = UserForm(edit=True, formdata=MultiDict({
        "name": "user",
        "email": "user@example.com",
        "password": "",           # blank, should be allowed in edit mode
        "password_again": "",     # blank confirm
        "role_code": "1",
        "status_code": "2"
    }))
    # In edit mode, InputRequired validators are removed&#8203;:contentReference[oaicite:14]{index=14}, so blank passwords should be acceptable.
    assert form.validate() is True
    assert form.errors == {}


def test_userform_invalid_role_value():
    """Role code outside defined choices should fail validation."""
    form = UserForm(formdata=MultiDict({
        "name": "user",
        "email": "user@example.com",
        "password": "password",
        "password_again": "password",
        "role_code": "5",  # invalid value, not in USER_ROLE keys {0,1}&#8203;:contentReference[oaicite:15]{index=15}
        "status_code": "2"
    }))
    assert form.validate() is False
    assert "role_code" in form.errors  # AnyOf validator should catch invalid role


def test_userform_invalid_status_value():
    """Status code outside defined choices should fail validation."""
    form = UserForm(formdata=MultiDict({
        "name": "user",
        "email": "user@example.com",
        "password": "password",
        "password_again": "password",
        "role_code": "1",
        "status_code": "99"  # invalid, not in USER_STATUS keys {0,1,2}&#8203;:contentReference[oaicite:16]{index=16}
    }))
    assert form.validate() is False
    assert "status_code" in form.errors
