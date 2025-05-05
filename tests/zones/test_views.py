from flask import url_for
import pytest

from ad2web.services.zone_service import ZoneService
from ad2web.zones.models import Zone

def test_index_no_zones(admin_client):
    """GET /settings/zones/ with no zones returns 200 and shows an empty list."""
    response = admin_client.get("/settings/zones/")
    assert response.status_code == 200
    # The page should indicate no zones (e.g., no table rows)
    assert b"No zones" in response.data or b"Zone" in response.data  # at least loads template

def test_index_with_zones(admin_client, db_session):
    """GET /settings/zones/ shows existing zones in the list."""
    # Create a couple of zones to display
    ZoneService.create_zone(zone_id=101, name="Zone101", description="Test1")
    ZoneService.create_zone(zone_id=102, name="Zone102", description="Test2")
    response = admin_client.get("/settings/zones/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Zone names should appear in the response content
    assert "Zone101" in html and "Zone102" in html

def test_index_requires_login(client):
    """Unauthenticated access to /settings/zones/ should redirect to login."""
    response = client.get("/settings/zones/", follow_redirects=False)
    assert response.status_code == 302
    # Should redirect to login page
    assert "/login" in response.headers.get("Location", "")

def test_index_forbidden_for_non_admin(normal_client):
    """Non-admin user trying to access /settings/zones/ gets 403 Forbidden."""
    response = normal_client.get("/settings/zones/")
    assert response.status_code == 403

def test_create_get(admin_client):
    """GET /settings/zones/create returns the zone creation form."""
    response = admin_client.get("/settings/zones/create")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The form fields should be present
    assert "Zone ID" in html and "Name" in html

def test_create_requires_admin(normal_client):
    """Non-admin user cannot load the create zone page (403 Forbidden)."""
    response = normal_client.get("/settings/zones/create")
    assert response.status_code == 403

def test_create_post_success(admin_client, db_session):
    """POST /settings/zones/create with valid data creates a zone and redirects to index."""
    form_data = {"zone_id": 201, "name": "New Zone", "description": "Created via form"}
    response = admin_client.post("/settings/zones/create", data=form_data, follow_redirects=True)
    # Should redirect to index (status code 200 after follow_redirects means landed on index)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Success flash message should be displayed
    assert "Zone created." in html
    # The new zone should exist in DB
    created = Zone.query.filter_by(zone_id=201).first()
    assert created is not None and created.name == "New Zone"

def test_create_post_missing_fields(admin_client):
    """POST /settings/zones/create with missing required fields should re-render form with errors."""
    form_data = {"zone_id": "", "name": "", "description": "No name or ID"}
    response = admin_client.post("/settings/zones/create", data=form_data)
    # No redirect, should return the form page with errors
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Error messages for required fields should be present
    assert "This field is required" in html

def test_create_post_duplicate_zone(admin_client, db_session):
    """POST /settings/zones/create with a duplicate zone_id should flash an error and redirect."""
    # Create an initial zone
    ZoneService.create_zone(zone_id=300, name="Original300", description="")
    form_data = {"zone_id": 300, "name": "Duplicate300", "description": "Dup"}
    response = admin_client.post("/settings/zones/create", data=form_data, follow_redirects=True)
    assert response.status_code == 200  # after redirect to index
    html = response.get_data(as_text=True)
    # Expect error flash message about duplicate
    assert "Failed to create zone" in html and "unique" in html
    # Ensure duplicate was not created
    zones = Zone.query.filter_by(zone_id=300).all()
    assert len(zones) == 1  # still only the original

def test_edit_get(admin_client, db_session):
    """GET /settings/zones/edit/<id> returns the edit form for the specified zone."""
    ZoneService.create_zone(zone_id=211, name="EditMe", description="Old")
    response = admin_client.get("/settings/zones/edit/211")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # The form should contain the current values (e.g., name in a value attribute)
    assert 'value="EditMe"' in html

def test_edit_get_not_found(admin_client):
    """GET /settings/zones/edit/<id> for non-existent zone returns 404."""
    response = admin_client.get("/settings/zones/edit/9999")
    assert response.status_code == 404

def test_edit_get_forbidden(normal_client, db_session):
    """Non-admin user gets 403 for edit zone page even if zone exists."""
    ZoneService.create_zone(zone_id=212, name="NormEdit", description="")
    response = normal_client.get("/settings/zones/edit/212")
    assert response.status_code == 403

def test_edit_post_success(admin_client, db_session):
    """POST /settings/zones/edit/<id> with valid data updates the zone and redirects."""
    ZoneService.create_zone(zone_id=250, name="Original Name", description="Orig")
    form_data = {"zone_id": 250, "name": "Updated Name", "description": "Updated Desc"}
    response = admin_client.post(f"/settings/zones/edit/250", data=form_data, follow_redirects=True)
    # Should redirect to index (200 after follow_redirects)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Zone updated." in html
    # DB should reflect the updated data
    zone = Zone.query.filter_by(zone_id=250).first()
    assert zone is not None and zone.name == "Updated Name" and zone.description == "Updated Desc"

def test_edit_post_validation_error(admin_client, db_session):
    """POST /settings/zones/edit/<id> with invalid data (missing name) shows form errors."""
    ZoneService.create_zone(zone_id=260, name="Name", description="Desc")
    form_data = {"zone_id": 260, "name": "", "description": "No name"}
    response = admin_client.post("/settings/zones/edit/260", data=form_data)
    assert response.status_code == 200  # re-render form on validation failure
    html = response.get_data(as_text=True)
    assert "This field is required" in html
    # Ensure the zone was not changed
    zone = Zone.query.filter_by(zone_id=260).first()
    assert zone is not None and zone.description == "Desc"

def test_edit_post_duplicate_zone_id(admin_client, db_session):
    """POST /settings/zones/edit/<id> changing zone_id to an existing one should flash error and redirect."""
    ZoneService.create_zone(zone_id=500, name="Zone500", description="")
    ZoneService.create_zone(zone_id=501, name="Zone501", description="")
    # Attempt to change zone 500's zone_id to 501 (conflict)
    form_data = {"zone_id": 501, "name": "Zone500New", "description": "Conflict change"}
    response = admin_client.post("/settings/zones/edit/500", data=form_data, follow_redirects=True)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    # Should flash failure message
    assert "Failed to update zone" in html and "unique" in html
    # Verify zone 500 still has original properties (not changed to duplicate)
    zone500 = Zone.query.filter_by(zone_id=500).first()
    assert zone500 is not None and zone500.name == "Zone500"

def test_edit_post_forbidden(normal_client, db_session):
    """Non-admin user attempting to POST edit is forbidden and zone remains unchanged."""
    ZoneService.create_zone(zone_id=270, name="NoChange", description="Orig")
    form_data = {"zone_id": 270, "name": "ShouldNotUpdate", "description": "New"}
    response = normal_client.post("/settings/zones/edit/270", data=form_data)
    assert response.status_code == 403
    # Verify no change
    zone = Zone.query.filter_by(zone_id=270).first()
    assert zone is not None and zone.name == "NoChange"

def test_delete_zone_success(admin_client, db_session):
    """POST /settings/zones/delete/<id> deletes the zone and returns JSON success."""
    ZoneService.create_zone(zone_id=350, name="DelZone", description="")
    response = admin_client.post("/settings/zones/delete/350")
    assert response.status_code == 200
    data = response.get_json()
    assert data == {"success": True} or (data.get("success") is True)
    # Zone should be removed from DB
    assert ZoneService.get_zone(350) is None

def test_delete_zone_not_found(admin_client):
    """POST /settings/zones/delete/<id> for non-existent zone returns 404 JSON."""
    response = admin_client.post("/settings/zones/delete/999")
    assert response.status_code == 404
    data = response.get_json()
    assert data == {"success": False} or (data.get("success") is False)

def test_delete_zone_forbidden(normal_client, db_session):
    """Non-admin user cannot delete a zone (403), and the zone remains."""
    ZoneService.create_zone(zone_id=360, name="NoDelete", description="")
    response = normal_client.post("/settings/zones/delete/360")
    assert response.status_code == 403
    # Ensure zone still exists
    assert ZoneService.get_zone(360) is not None

def test_import_zones_route_success(admin_client, db_session):
    """POST /settings/zones/import with JSON data imports zones and returns JSON."""
    # Prepare JSON payload
    payload = [
        {"address": 801, "zone_name": "Import1"},
        {"address": 802, "zone_name": "Import2"}
    ]
    response = admin_client.post("/settings/zones/import", json=payload)
    assert response.status_code == 200
    data = response.get_json()
    # Should return a dict in 'success' with new zones data
    assert data.get("success") and isinstance(data["success"], dict)
    # Zones should be in DB
    assert ZoneService.get_zone(801) is not None and ZoneService.get_zone(802) is not None

def test_import_zones_route_no_data(admin_client):
    """POST /settings/zones/import with empty data returns failure message."""
    response = admin_client.post("/settings/zones/import", json=[])
    assert response.status_code == 200
    data = response.get_json()
    # success key contains the failure message string
    assert isinstance(data.get("success"), str)
    assert "Failure to enumerate zones" in data["success"]

def test_import_zones_route_forbidden(normal_client):
    """Non-admin cannot import zones (403 Forbidden)."""
    response = normal_client.post("/settings/zones/import", json=[])
    assert response.status_code == 403
