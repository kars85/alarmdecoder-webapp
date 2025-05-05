import pytest
from ad2web.forms.notification_form import NotificationForm
from wtforms import ValidationError

def submit_form(data):
    form = NotificationForm(data=data)
    valid = form.validate()
    return valid, form.errors

def test_email_form_valid():
    data = {
        "notif_type": "EMAIL",
        "event": "zone_fault",
        "destination": "foo@example.com",
        "message": "Alert!"
    }
    valid, errs = submit_form(data)
    assert valid and errs == {}

def test_email_form_missing_destination():
    data = {
        "notif_type": "EMAIL",
        "event": "zone_fault",
        "destination": "",
        "message": ""
    }
    valid, errs = submit_form(data)
    assert not valid
    assert "destination" in errs

def test_sms_form_valid():
    data = {
        "notif_type": "SMS",
        "event": "zone_fault",
        "destination": "+15551234567",
        "message": ""
    }
    valid, errs = submit_form(data)
    assert valid

def test_sms_form_invalid_number():
    data = {
        "notif_type": "SMS",
        "event": "zone_fault",
        "destination": "notanumber",
        "message": ""
    }
    valid, errs = submit_form(data)
    assert not valid
    assert "destination" in errs

def test_form_bad_type():
    data = {
        "notif_type": "PUSH",
        "event": "zone_fault",
        "destination": "x",
        "message": ""
    }
    with pytest.raises(ValidationError):
        NotificationForm(data=data).validate()
