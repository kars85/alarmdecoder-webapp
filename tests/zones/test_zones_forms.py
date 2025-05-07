import pytest
from wtforms import ValidationError
from werkzeug.datastructures import MultiDict

from ad2web.forms.zones_form import ZoneForm

def validate_form(form: ZoneForm):
    """Helper to validate form and return errors dict."""
    valid = form.validate()
    return valid, form.errors

def test_zone_form_valid_data(db_session):
    """ZoneForm accepts valid data (boundary values included)."""
    form = ZoneForm(data={"zone_id": 65535, "name": "Garage Sensor", "description": "Garage Door"})
    is_valid, errors = validate_form(form)
    assert is_valid is True
    assert errors == {}  # no errors for valid data

def test_zone_form_missing_name(db_session):
    """ZoneForm requires 'name'; missing name should produce a validation error."""
    form = ZoneForm(data={"zone_id": 1, "name": "", "description": "Desc"})
    is_valid, errors = validate_form(form)
    assert not is_valid
    assert "name" in errors
    # The default error message for InputRequired (or DataRequired) should appear
    assert any("This field is required" in msg for msg in errors["name"])

def test_zone_form_missing_zone_id(db_session):
    """ZoneForm requires 'zone_id'; missing zone_id yields error."""
    form = ZoneForm(data={"zone_id": None, "name": "No ID", "description": ""})
    is_valid, errors = validate_form(form)
    assert not is_valid
    assert "zone_id" in errors
    assert any("This field is required" in msg or "Invalid input" in msg for msg in errors["zone_id"])

def test_zone_form_invalid_zone_id_type(db_session):
    """ZoneForm.zone_id must be an integer; non-numeric input should fail validation."""
    form = ZoneForm(data={"zone_id": "abc", "name": "Name", "description": ""})
    is_valid, errors = validate_form(form)
    assert not is_valid
    assert "zone_id" in errors
    # WTForms should report a conversion error like "Not a valid integer"
    assert any("valid integer" in msg for msg in errors["zone_id"])

def test_zone_form_zone_id_out_of_range(db_session):
    """ZoneForm.zone_id has a range validator (1 to 65535). Values outside should error."""
    form_low = ZoneForm(data={"zone_id": 0, "name": "Low", "description": ""})
    form_high = ZoneForm(data={"zone_id": 70000, "name": "High", "description": ""})
    valid_low, errs_low = validate_form(form_low)
    valid_high, errs_high = validate_form(form_high)
    assert not valid_low and "zone_id" in errs_low
    assert not valid_high and "zone_id" in errs_high
    # Expect error messages indicating the allowed range
    msg_low = errs_low["zone_id"][0]
    msg_high = errs_high["zone_id"][0]
    assert "between 1 and 65535" in msg_low
    assert "between 1 and 65535" in msg_high

def test_zone_form_name_too_long(db_session):
    """ZoneForm.name max length 32 characters; longer input should fail."""
    long_name = "X" * 33
    form = ZoneForm(data={"zone_id": 2, "name": long_name, "description": ""})
    valid, errors = validate_form(form)
    assert not valid and "name" in errors
    assert any("Field cannot be longer than 32 characters" in msg or "max length 32" in msg
               for msg in errors["name"])

def test_zone_form_description_too_long(db_session):
    """ZoneForm.description max length 255; input exceeding length should fail."""
    long_desc = "D" * 256
    form = ZoneForm(data={"zone_id": 3, "name": "Name", "description": long_desc})
    valid, errors = validate_form(form)
    assert not valid and "description" in errors
    assert any("Field cannot be longer than 255 characters" in msg or "max length 255" in msg
               for msg in errors["description"])
