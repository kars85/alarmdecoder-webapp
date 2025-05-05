import json
import pytest

from ad2web.services import zone_service, api_service
from ad2web.zones.models import Zone

# Dummy device to intercept zone fault/restore commands
class DummyDevice:
    def __init__(self):
        self.commands = []
    def send(self, cmd):
        self.commands.append(cmd)
        return None

# Ensure the AlarmDecoder device is initialized with a dummy for tests
@pytest.fixture(autouse=True)
def dummy_device(admin_client):
    # Attach dummy device to the application's decoder
    app = admin_client.application
    app.decoder.device = DummyDevice()
    yield
    # (Optional cleanup) remove dummy device after tests
    app.decoder.device = None

def test_list_zones_empty(admin_client):
    """GET /api/zones returns empty list when no zones configured."""
    response = admin_client.get("/api/zones")
    assert response.status_code == 200
    data = response.get_json()
    assert "zones" in data and data["zones"] == []

def test_list_zones_with_data(admin_client, db_session):
    """GET /api/zones returns all zones in JSON format."""
    # Create zones
    zone_service.ZoneService.create_zone(zone_id=10, name="Zone10", description="Test10")
    zone_service.ZoneService.create_zone(zone_id=11, name="Zone11", description="Test11")
    response = admin_client.get("/api/zones")
    assert response.status_code == 200
    data = response.get_json()
    zones_list = data.get("zones")
    # Should have both zones with correct fields
    ids = {z["zone_id"] for z in zones_list}
    assert {10, 11}.issubset(ids)
    for z in zones_list:
        if z["zone_id"] == 10:
            assert z["name"] == "Zone10" and z["description"] == "Test10"

def test_list_zones_unauthorized_no_key(client):
    """GET /api/zones without API key returns 401 Not Authorized."""
    response = client.get("/api/zones")
    assert response.status_code == 401
    data = response.get_json()
    assert data.get("message") == "Not authorized."

def test_get_zone_success(admin_client, db_session):
    """GET /api/zones/<id> returns details for the specified zone."""
    zone_service.ZoneService.create_zone(zone_id=20, name="APIZone20", description="API test")
    response = admin_client.get("/api/zones/20")
    assert response.status_code == 200
    data = response.get_json()
    assert data["zone_id"] == 20 and data["name"] == "APIZone20"

def test_get_zone_not_found(admin_client):
    """GET /api/zones/<id> for non-existent zone returns 404 with error message."""
    response = admin_client.get("/api/zones/9999")
    assert response.status_code == 404
    data = response.get_json()
    # Should contain an error message about zone not existing
    assert "Zone does not exist" in data.get("message", "")

def test_get_zone_unauthorized(normal_client):
    """GET /api/zones/<id> with no valid API key yields 401 Not Authorized."""
    # Here normal_client is authorized (with API key) but let's simulate missing key by using a new client
    unauth_client = normal_client.application.test_client()  # no API key header
    response = unauth_client.get("/api/zones/1")
    assert response.status_code == 401
    data = response.get_json()
    assert data.get("message") == "Not authorized."

def test_create_zone_success(admin_client, db_session):
    """POST /api/zones creates a new zone when called by admin (returns 201 and JSON)."""
    payload = {"zone_id": 30, "name": "API Created", "description": "Created via API"}
    response = admin_client.post("/api/zones", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 201
    data = response.get_json()
    assert data["zone_id"] == 30 and data["name"] == "API Created"
    # New zone should exist in DB
    assert zone_service.ZoneService.get_zone(30) is not None

def test_create_zone_missing_fields(admin_client):
    """POST /api/zones with missing required fields returns 422."""
    # Missing 'name'
    payload = {"zone_id": 40}
    response = admin_client.post("/api/zones", json=payload)
    assert response.status_code == 422
    data = response.get_json()
    assert "Missing 'name' entry" in data.get("message", "")
    # Missing 'zone_id'
    payload = {"name": "NoID"}
    response = admin_client.post("/api/zones", json=payload)
    assert response.status_code == 422
    data = response.get_json()
    assert "Missing 'zone_id' entry" in data.get("message", "")

def test_create_zone_conflict(admin_client, db_session):
    """POST /api/zones for an existing zone_id returns 409 Conflict."""
    zone_service.ZoneService.create_zone(zone_id=50, name="ConflictZone", description="")
    payload = {"zone_id": 50, "name": "ConflictZone2", "description": ""}
    response = admin_client.post("/api/zones", json=payload)
    assert response.status_code == 409
    data = response.get_json()
    assert "already exists" in data.get("message", "")
    # No new zone created
    zones = Zone.query.filter_by(zone_id=50).all()
    assert len(zones) == 1

def test_create_zone_insufficient_privileges(normal_client, db_session):
    """POST /api/zones by non-admin returns 401 Unauthorized (insufficient privileges)."""
    payload = {"zone_id": 60, "name": "NormalUserZone", "description": ""}
    response = normal_client.post("/api/zones", json=payload)
    assert response.status_code == 401
    data = response.get_json()
    assert "Insufficient privileges" in data.get("message", "")
    # Zone should not have been created
    assert zone_service.ZoneService.get_zone(60) is None

def test_create_zone_no_auth(client):
    """POST /api/zones with no API key returns 401 Not authorized."""
    payload = {"zone_id": 61, "name": "NoAuthZone", "description": ""}
    response = client.post("/api/zones", json=payload)
    assert response.status_code == 401
    data = response.get_json()
    assert data.get("message") == "Not authorized."
    assert zone_service.ZoneService.get_zone(61) is None

def test_update_zone_success(admin_client, db_session):
    """PUT /api/zones/<id> updates zone fields for admin and returns 200 with new data."""
    zone_service.ZoneService.create_zone(zone_id=70, name="UpdName", description="UpdDesc")
    payload = {"name": "UpdatedName", "description": "UpdatedDesc"}
    response = admin_client.put("/api/zones/70", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    assert data["name"] == "UpdatedName" and data["description"] == "UpdatedDesc"
    # DB should reflect changes
    zone = zone_service.ZoneService.get_zone(70)
    assert zone is not None and zone.name == "UpdatedName"

def test_update_zone_change_id_conflict(admin_client, db_session):
    """PUT /api/zones/<id> attempting to change zone_id to one that exists returns 409."""
    zone_service.ZoneService.create_zone(zone_id=80, name="Zone80", description="")
    zone_service.ZoneService.create_zone(zone_id=81, name="Zone81", description="")
    payload = {"zone_id": 81, "name": "ShouldConflict"}
    response = admin_client.put("/api/zones/80", json=payload)
    assert response.status_code == 409
    data = response.get_json()
    assert "already exists with the associated zone_id" in data.get("message", "")
    # Zone 80 should remain unchanged
    zone80 = zone_service.ZoneService.get_zone(80)
    assert zone80 is not None and zone80.zone_id == 80 and zone80.name == "Zone80"

def test_update_zone_not_found(admin_client):
    """PUT /api/zones/<id> for non-existent zone returns 404."""
    payload = {"name": "NoZone"}
    response = admin_client.put("/api/zones/999", json=payload)
    assert response.status_code == 404
    data = response.get_json()
    assert "Zone does not exist" in data.get("message", "")

def test_update_zone_missing_body(normal_client):
    """PUT /api/zones/<id> with no JSON body returns 422 missing body error."""
    # Use normal_client to simulate a request missing JSON (it will be caught before admin check)
    response = normal_client.put("/api/zones/1")  # no data, no content_type
    assert response.status_code == 422
    data = response.get_json()
    assert "Missing request body" in data.get("message", "")

def test_update_zone_insufficient_privileges(normal_client, db_session):
    """PUT /api/zones/<id> by non-admin returns 401 and does not update the zone."""
    zone_service.ZoneService.create_zone(zone_id=90, name="NoPriv", description="Orig")
    payload = {"name": "NewName"}
    response = normal_client.put("/api/zones/90", json=payload)
    assert response.status_code == 401
    data = response.get_json()
    assert "Insufficient privileges" in data.get("message", "")
    # Ensure no update
    zone = zone_service.ZoneService.get_zone(90)
    assert zone is not None and zone.name == "NoPriv"

def test_update_zone_no_auth(client, db_session):
    """PUT /api/zones/<id> with no API key returns 401 Not authorized."""
    zone_service.ZoneService.create_zone(zone_id=91, name="Zone91", description="")
    payload = {"description": "ShouldNotUpdate"}
    response = client.put("/api/zones/91", data=json.dumps(payload), content_type="application/json")
    assert response.status_code == 401
    data = response.get_json()
    assert data.get("message") == "Not authorized."
    # Ensure not updated
    zone = zone_service.ZoneService.get_zone(91)
    assert zone is not None and zone.description == ""

def test_delete_zone_success(admin_client, db_session):
    """DELETE /api/zones/<id> removes the zone and returns 204 No Content."""
    zone_service.ZoneService.create_zone(zone_id=250, name="DelAPI", description="")
    response = admin_client.delete("/api/zones/250")
    assert response.status_code == 204  # No Content
    # The zone should be deleted
    assert zone_service.ZoneService.get_zone(250) is None

def test_delete_zone_not_found(admin_client):
    """DELETE /api/zones/<id> for non-existent zone returns 404."""
    response = admin_client.delete("/api/zones/9999")
    assert response.status_code == 404
    data = response.get_json()
    assert "Zone does not exist" in data.get("message", "")

def test_delete_zone_insufficient_privileges(normal_client, db_session):
    """DELETE /api/zones/<id> by non-admin returns 401 and does not delete."""
    zone_service.ZoneService.create_zone(zone_id=260, name="NoDeleteAPI", description="")
    response = normal_client.delete("/api/zones/260")
    assert response.status_code == 401
    data = response.get_json()
    assert "Insufficient privileges" in data.get("message", "")
    # Zone still exists
    assert zone_service.ZoneService.get_zone(260) is not None

def test_delete_zone_no_auth(client, db_session):
    """DELETE /api/zones/<id> without API key returns 401 Not authorized."""
    zone_service.ZoneService.create_zone(zone_id=261, name="Zone261", description="")
    response = client.delete("/api/zones/261")
    assert response.status_code == 401
    data = response.get_json()
    assert data.get("message") == "Not authorized."
    assert zone_service.ZoneService.get_zone(261) is not None

def test_fault_zone_success(admin_client, db_session):
    """POST /api/zones/<id>/fault simulates zone fault (admin only, returns 204)."""
    zone_service.ZoneService.create_zone(zone_id=701, name="FaultZone", description="")
    dummy = admin_client.application.decoder.device  # our DummyDevice
    response = admin_client.post("/api/zones/701/fault")
    assert response.status_code == 204
    # The dummy device should have received the correct command
    assert f"L7011\r" in dummy.commands

def test_fault_zone_not_found(admin_client):
    """POST /api/zones/<id>/fault for missing zone returns 404 error."""
    response = admin_client.post("/api/zones/999/fault")
    assert response.status_code == 404
    data = response.get_json()
    assert "Zone does not exist" in data.get("message", "")

def test_fault_zone_insufficient_privileges(normal_client, db_session):
    """POST /api/zones/<id>/fault by non-admin returns 401 and does not send device command."""
    zone_service.ZoneService.create_zone(zone_id=702, name="FaultNoPriv", description="")
    dummy = normal_client.application.decoder.device
    response = normal_client.post("/api/zones/702/fault")
    assert response.status_code == 401
    data = response.get_json()
    assert "Insufficient privileges" in data.get("message", "")
    # No command sent to device
    assert dummy.commands == []

def test_fault_zone_no_auth(client, db_session):
    """POST /api/zones/<id>/fault with no API key returns 401."""
    zone_service.ZoneService.create_zone(zone_id=703, name="FaultNoAuth", description="")
    response = client.post("/api/zones/703/fault")
    assert response.status_code == 401
    data = response.get_json()
    assert data.get("message") == "Not authorized."

def test_restore_zone_success(admin_client, db_session):
    """POST /api/zones/<id>/restore simulates zone restore (admin only)."""
    zone_service.ZoneService.create_zone(zone_id=801, name="RestoreZone", description="")
    dummy = admin_client.application.decoder.device
    response = admin_client.post("/api/zones/801/restore")
    assert response.status_code == 204
    # Dummy device should have 'L{zone_id}0' command
    assert f"L8010\r" in dummy.commands

def test_restore_zone_not_found(admin_client):
    """POST /api/zones/<id>/restore for missing zone returns 404."""
    response = admin_client.post("/api/zones/999/restore")
    assert response.status_code == 404
    data = response.get_json()
    assert "Zone does not exist" in data.get("message", "")

def test_restore_zone_insufficient_privileges(normal_client, db_session):
    """POST /api/zones/<id>/restore by non-admin returns 401 and no device command."""
    zone_service.ZoneService.create_zone(zone_id=802, name="RestoreNoPriv", description="")
    dummy = normal_client.application.decoder.device
    response = normal_client.post("/api/zones/802/restore")
    assert response.status_code == 401
    data = response.get_json()
    assert "Insufficient privileges" in data.get("message", "")
    assert dummy.commands == []  # no command sent

def test_restore_zone_no_auth(client, db_session):
    """POST /api/zones/<id>/restore without API key returns 401."""
    zone_service.ZoneService.create_zone(zone_id=803, name="RestoreNoAuth", description="")
    response = client.post("/api/zones/803/restore")
    assert response.status_code == 401
    data = response.get_json()
    assert data.get("message") == "Not authorized."
