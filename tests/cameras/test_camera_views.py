import pytest
from ad2web.cameras.models import Camera

@pytest.fixture
def sample_camera(db_session):
    cam = Camera(
        name="TestViewCam",
        protocol="http",
        host="127.0.0.1",
        port=8000,
        path="/jpg",
        username="",
        password=""
    )
    db_session.session.add(cam)
    db_session.session.commit()
    return cam

def test_list_page_requires_login(client):
    """GET /settings/cameras/ redirects to login if unauthenticated."""
    res = client.get("/settings/cameras/")
    assert res.status_code == 302
    assert "/login" in res.headers["Location"]

def test_list_page_forbidden_for_non_admin(normal_client):
    """Non-admin users cannot view cameras settings (403)."""
    res = normal_client.get("/settings/cameras/")
    assert res.status_code == 403

def test_list_page_shows_entries(admin_client, sample_camera):
    """Admin sees cameras listed on the settings page."""
    res = admin_client.get("/settings/cameras/")
    assert res.status_code == 200
    html = res.data.decode()
    assert "TestViewCam" in html

def test_create_get_and_post(admin_client, db_session):
    """GET form and POST valid data creates a camera and redirects."""
    # GET create form
    res1 = admin_client.get("/settings/cameras/create")
    assert res1.status_code == 200 and "Create Camera" in res1.data.decode()
    # POST creation
    data = {
        "name": "NewCam",
        "protocol": "http",
        "host": "10.0.0.5",
        "port": 8082,
        "path": "/mjpeg",
        "username": "",
        "password": ""
    }
    res2 = admin_client.post("/settings/cameras/create", data=data, follow_redirects=True)
    assert res2.status_code == 200
    assert "Camera created" in res2.data.decode()
    # Confirm in DB
    cam = Camera.query.filter_by(name="NewCam").first()
    assert cam is not None

def test_create_invalid(admin_client):
    """POST invalid form data re-renders form with errors."""
    data = {"name": "", "protocol": "", "host": "", "port": 0, "path": "", "username": "", "password": ""}
    res = admin_client.post("/settings/cameras/create", data=data)
    assert res.status_code == 200
    html = res.data.decode()
    assert "This field is required" in html or "Invalid input" in html

def test_edit_get_and_post(admin_client, sample_camera):
    """GET edit form and POST changes successfully update the camera."""
    edit_url = f"/settings/cameras/edit/{sample_camera.id}"
    # GET
    r1 = admin_client.get(edit_url)
    assert r1.status_code == 200 and "Edit Camera" in r1.data.decode()
    # POST update
    upd = {"name": "EditedCam", "protocol": "http", "host": "1.2.3.4", "port": 8001, "path": "/", "username": "", "password": ""}
    r2 = admin_client.post(edit_url, data=upd, follow_redirects=True)
    assert r2.status_code == 200
    assert "Camera updated" in r2.data.decode()
    # DB reflect
    from ad2web.cameras.models import Camera as CamModel
    cam = CamModel.query.get(sample_camera.id)
    assert cam.name == "EditedCam" and cam.host == "1.2.3.4"

def test_delete_camera(admin_client, db_session):
    """POST delete returns JSON success and removes camera."""
    from ad2web.cameras.models import Camera as Cam
    cam = Cam(name="DelCam", protocol="http", host="x", port=80, path="", username="", password="")
    db_session.session.add(cam); db_session.session.commit()
    res = admin_client.post(f"/settings/cameras/delete/{cam.id}")
    assert res.status_code == 200
    assert res.get_json()["success"] is True
    assert Cam.query.get(cam.id) is None
