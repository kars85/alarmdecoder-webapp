import pytest
from ad2web.forms.auth_form import LoginForm, SignupForm, ResetPasswordForm
from ad2web.user.models import User

@pytest.mark.parametrize("field, value, error_field", [
    ("name", "", "name"),          # missing username
    ("email", "", "email"),        # missing email
    ("password", "", "password"),  # missing password
    ("agree", False, "agree")      # terms not agreed
])
def test_signup_form_missing_fields(db_session, field, value, error_field):
    """SignupForm should require username, email, password, and agreement."""
    data = {
        "name": "newuser",
        "email": "new@example.com",
        "password": "validpass",
        "agree": True
    }
    data[field] = value
    form = SignupForm(data=data)
    assert not form.validate()
    # Expect an error on the specific missing field
    assert error_field in form.errors
    # Required field error message should be present (e.g., "This field is required.")
    assert any("required" in msg.lower() for msg in form.errors[error_field])

def test_signup_form_duplicate_username_email(db_session):
    """SignupForm should reject duplicate username or email."""
    # Create an existing user in the database
    existing = User(name="existinguser", email="exists@example.com", password="Passw0rd")
    db_session.add(existing); db_session.commit()
    # Duplicate username
    form1 = SignupForm(data={
        "name": "existinguser",
        "email": "new@example.com",
        "password": "somepass",
        "agree": True
    })
    assert not form1.validate()
    assert "name" in form1.errors and any("taken" in msg for msg in form1.errors["name"])
    # Duplicate email
    form2 = SignupForm(data={
        "name": "newuser",
        "email": "exists@example.com",
        "password": "somepass",
        "agree": True
    })
    assert not form2.validate()
    assert "email" in form2.errors and any("already" in msg or "taken" in msg for msg in form2.errors["email"])

def test_signup_form_password_length(db_session):
    """SignupForm enforces password length constraints (min length)."""
    too_short = "123"  # shorter than PASSWORD_LEN_MIN (e.g. 6)
    form = SignupForm(data={
        "name": "user2",
        "email": "user2@example.com",
        "password": too_short,
        "agree": True
    })
    assert not form.validate()
    assert "password" in form.errors
    # Expect an error about length
    assert any("characters" in msg.lower() or "short" in msg.lower() for msg in form.errors["password"])

def test_signup_form_valid_data(db_session):
    """SignupForm with all valid inputs should pass validation."""
    form = SignupForm(data={
        "name": "uniqueuser",
        "email": "unique@example.com",
        "password": "GoodPass123",
        "agree": True
    })
    assert form.validate()  # no validation errors
    # Form does not create user by itself, but data is acceptable for user creation

def test_login_form_missing_and_length(db_session):
    """LoginForm requires login and password fields and enforces password length."""
    # Both fields missing
    form1 = LoginForm(data={"login": "", "password": ""})
    assert not form1.validate()
    assert "login" in form1.errors and "password" in form1.errors
    # Password too short
    form2 = LoginForm(data={"login": "user", "password": "123"})
    assert not form2.validate()
    assert "password" in form2.errors and any("min" in msg.lower() or "characters" in msg.lower() for msg in form2.errors["password"])

def test_login_form_valid_fields(db_session):
    """LoginForm with required fields present passes basic validation (does not verify credentials here)."""
    form = LoginForm(data={"login": "user@example.com", "password": "ValidPass"})
    assert form.validate()  # form fields are valid (actual authentication handled elsewhere)

def test_reset_password_form_missing_and_mismatch(db_session):
    """ResetPasswordForm requires both password fields and they must match."""
    # Missing confirmation password
    form1 = ResetPasswordForm(data={"password": "NewPass123", "confirm_password": ""})
    assert not form1.validate()
    assert "confirm_password" in form1.errors
    # Mismatch passwords
    form2 = ResetPasswordForm(data={"password": "NewPass123", "confirm_password": "DifferentPass"})
    assert not form2.validate()
    assert "confirm_password" in form2.errors and any("match" in msg.lower() for msg in form2.errors["confirm_password"])
    # Matching passwords
    form3 = ResetPasswordForm(data={"password": "NewPass123", "confirm_password": "NewPass123"})
    assert form3.validate()

def test_create_profile_form_duplicate_fields(db_session):
    """CreateProfileForm (OpenID profile creation) should enforce unique username/email similar to SignupForm."""
    existing = User(name="openiduser", email="openid@example.com", password="Pass123")
    db_session.add(existing); db_session.commit()
    form = CreateProfileForm(data={
        "openid": "some-openid-url",
        "name": "openiduser",               # duplicate username
        "email": "openid@example.com",      # duplicate email
        "password": "AnotherPass123"
    })
    assert not form.validate()
    # Should have errors on both name and email for duplicates
    assert "name" in form.errors and any("taken" in msg for msg in form.errors["name"])
    assert "email" in form.errors and any("taken" in msg for msg in form.errors["email"])
    # Also test that a valid new profile passes
    form2 = CreateProfileForm(data={
        "openid": "other-openid",
        "name": "newopeniduser",
        "email": "newopenid@example.com",
        "password": "SomePass123"
    })
    assert form2.validate()
