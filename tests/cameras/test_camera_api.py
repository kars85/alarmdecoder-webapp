import json
import pytest
from ad2web.cameras.models import Camera

@pytest.mark.parametrize("client_fixture", ["client", "normal_client"])
def test_api_list_unauthenticated(request, client_fixture):
    """Unauthenticated or non-admin cannot list cameras via API."""
    client = request.getfixturevalue(client_fixture)
    res = client.get("/api/cameras")
    # Expect 401 for no API key or insufficient privileges
    assert res.status_code in (401, 403)

def test_api_list_success(admin_client, db_session):
    """Admin can list cameras via API; returns JSON array."""
    # Seed a camera
    cam = Camera(name="APICam", protocol="http", host="192.0.2.1", port=8080, path="/img", username="", password="")
    db_session.session.add(cam); db_session.session.commit()

    res = admin_client.get("/api/cameras")
    assert res.status_code == 200
    data = res.get_json()
    assert "cameras" in data
    assert any(c["name"] == "APICam" for c in data["cameras"])

def test_api_get_camera_success(admin_client, db_session):
    """GET /api/cameras/<id> returns camera details."""
    cam = Camera(name="SingleCam", protocol="http", host="8.8.8.8", port=80, path="/jpg", username="", password="")
    db_session.session.add(cam); db_session.session.commit()

    res = admin_client.get(f"/api/cameras/{cam.id}")
    assert res.status_code == 200
    json = res.get_json()
    assert json["id"] == cam.id and json["name"] == "SingleCam"

def test_api_get_camera_not_found(admin_client):
    """GET non-existent camera yields 404."""
    res = admin_client.get("/api/cameras/9999")
    assert res.status_code == 404
    assert "does not exist" in res.get_json().get("message", "").lower()

def test_api_create_and_delete_camera(admin_client, db_session):
    """POST to /api/cameras creates a camera, DELETE removes it."""
    payload = {
        "name": "CreateCam",
        "protocol": "rtsp",
        "host": "10.10.10.10",
        "port": 554,
        "path": "/live",
        "username": "u",
        "password": "p"
    }
    # Create
    res1 = admin_client.post("/api/cameras", data=json.dumps(payload), content_type="application/json")
    assert res1.status_code == 201
    cdata = res1.get_json()
    cid = cdata["id"]
    # Exists in DB
    assert Camera.query.get(cid) is not None

    # Delete
    res2 = admin_client.delete(f"/api/cameras/{cid}")
    assert res2.status_code == 204
    assert Camera.query.get(cid) is None

def test_api_create_camera_missing_fields(admin_client):
    """Omitting required fields returns 422."""
    # Missing 'host'
    res = admin_client.post("/api/cameras", json={"name":"X"})
    assert res.status_code == 422
    msg = res.get_json().get("message","").lower()
    assert "host" in msg or "missing" in msg
