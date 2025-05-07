import pytest
from ad2web.cameras.forms import CameraForm
from wtforms import ValidationError

@pytest.fixture
def valid_data():
    return {
        "name": "SideCam",
        "protocol": "http",
        "host": "192.168.0.20",
        "port": 8081,
        "path": "/video",
        "username": "",
        "password": ""
    }

def test_camera_form_valid(valid_data):
    """A CameraForm with all required fields valid passes."""
    form = CameraForm(data=valid_data)
    assert form.validate() is True

@pytest.mark.parametrize("missing", ["name", "protocol", "host", "port"])
def test_camera_form_required_fields(missing, valid_data):
    """Missing any required field causes form.validate() to be False."""
    data = dict(valid_data)
    data[missing] = ""
    form = CameraForm(data=data)
    assert form.validate() is False
    assert missing in form.errors

def test_camera_form_invalid_port(valid_data):
    """Port must be between 1 and 65535."""
    for bad in [0, 70000, -1]:
        data = dict(valid_data); data["port"] = bad
        form = CameraForm(data=data)
        assert form.validate() is False
        assert "port" in form.errors

def test_camera_form_path_length(valid_data):
    """Path field has a max length (e.g. 128); overly long path fails."""
    data = dict(valid_data)
    data["path"] = "/" + "a" * 200
    form = CameraForm(data=data)
    assert form.validate() is False
    assert "path" in form.errors

def test_camera_form_optional_credentials(valid_data):
    """Username/password may be blank but are length-limited if provided."""
    data = dict(valid_data)
    data["username"] = "u" * 65
    data["password"] = "p" * 65
    form = CameraForm(data=data)
    assert form.validate() is False
    assert "username" in form.errors and "password" in form.errors
