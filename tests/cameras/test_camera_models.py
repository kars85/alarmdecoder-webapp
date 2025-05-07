import pytest
from ad2web.cameras.models import Camera

def test_camera_model_persistence(db_session):
    """You can create and retrieve a Camera via the ORM."""
    cam = Camera(
        name="Front Door",
        protocol="http",
        host="192.168.1.50",
        port=8080,
        path="/stream",
        username="user",
        password="pass"
    )
    db_session.session.add(cam)
    db_session.session.commit()

    fetched = Camera.query.filter_by(name="Front Door").first()
    assert fetched is not None
    assert fetched.host == "192.168.1.50"
    assert fetched.port == 8080
    assert fetched.path == "/stream"

def test_get_jpg_url_method(db_session):
    """Camera.get_jpg_url() builds the correct URL string."""
    cam = Camera(
        name="TestCam",
        protocol="http",
        host="10.0.0.10",
        port=8000,
        path="/jpg",
        username="u",
        password="p"
    )
    # No auth embed
    url = cam.get_jpg_url()
    assert url.startswith("http://10.0.0.10:8000/jpg")
    # If credentials are present, they should appear in the URL
    cam.username = "alice"; cam.password = "secret"
    auth_url = cam.get_jpg_url()
    assert "alice:secret@" in auth_url
    assert auth_url.endswith("/jpg")

def test_camera_str_representation(db_session):
    """__repr__ or __str__ for Camera includes its name and ID."""
    cam = Camera(name="LobbyCam", protocol="rtsp", host="x", port=554, path="", username="", password="")
    db_session.session.add(cam)
    db_session.session.commit()
    text = repr(cam)
    assert "LobbyCam" in text and str(cam.id) in text
