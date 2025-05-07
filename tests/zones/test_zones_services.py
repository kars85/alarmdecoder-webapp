import pytest
from sqlalchemy.exc import IntegrityError

from ad2web.services.zone_service import ZoneService
from ad2web.zones.models import Zone

def test_list_zones_empty(db_session):
    """ZoneService.list_zones returns an empty list when no zones exist."""
    zones = ZoneService.list_zones()
    assert zones == []  # no zones in DB

def test_create_zone_success(db_session):
    """ZoneService.create_zone should add a new zone and return the Zone object."""
    zone = ZoneService.create_zone(zone_id=100, name="Sensor 100", description="Test zone")
    # The returned object should have the given attributes
    assert isinstance(zone, Zone)
    assert zone.zone_id == 100
    assert zone.name == "Sensor 100"
    assert zone.description == "Test zone"
    # It should be persisted in the database
    fetched = Zone.query.filter_by(zone_id=100).first()
    assert fetched is not None and fetched.name == "Sensor 100"

def test_create_zone_duplicate(db_session):
    """ZoneService.create_zone should raise IntegrityError if zone_id already exists."""
    ZoneService.create_zone(zone_id=5, name="Zone5", description="Duplicate test")
    # Attempt to create another zone with the same zone_id=5
    with pytest.raises(IntegrityError):
        ZoneService.create_zone(zone_id=5, name="Zone5 Copy", description="Should conflict")

def test_get_zone_exists(db_session):
    """ZoneService.get_zone returns the zone when it exists, by zone_id."""
    ZoneService.create_zone(zone_id=10, name="Front Door", description="Entry sensor")
    zone = ZoneService.get_zone(10)
    assert zone is not None
    assert zone.name == "Front Door"
    assert ZoneService.get_zone(999) is None  # non-existent zone returns None

def test_update_zone_success(db_session):
    """ZoneService.update_zone updates name/description and returns the updated zone."""
    ZoneService.create_zone(zone_id=42, name="Old Name", description="Old Desc")
    updated = ZoneService.update_zone(zone_id=42, name="New Name", description="New Desc")
    assert updated is not None
    assert updated.name == "New Name" and updated.description == "New Desc"
    # Check persistence
    refreshed = ZoneService.get_zone(42)
    assert refreshed.name == "New Name"

def test_update_zone_not_found(db_session):
    """ZoneService.update_zone returns None if the zone does not exist."""
    result = ZoneService.update_zone(zone_id=77, name="Name", description="Desc")
    assert result is None

def test_update_zone_commit_error(monkeypatch, db_session):
    """ZoneService.update_zone rolls back and raises IntegrityError on DB failure."""
    zone = ZoneService.create_zone(zone_id=8, name="Zone8", description="Desc8")
    # Monkeypatch session.commit to force an IntegrityError
    called = {"commit": False}
    def fake_commit():
        called["commit"] = True
        raise IntegrityError("forced failure", orig=None, params=None)
    monkeypatch.setattr(zone.__class__.query.session, "commit", fake_commit)
    with pytest.raises(IntegrityError):
        ZoneService.update_zone(zone_id=8, name="NewName", description="NewDesc")
    # After failure, the session should have been rolled back (no lingering changes)
    zone_after = ZoneService.get_zone(8)
    assert zone_after.name == "Zone8"  # name unchanged due to rollback

def test_delete_zone_success(db_session):
    """ZoneService.delete_zone deletes an existing zone and returns True."""
    ZoneService.create_zone(zone_id=50, name="Temp", description="")
    assert ZoneService.get_zone(50) is not None
    result = ZoneService.delete_zone(50)
    assert result is True
    # The zone should be removed from the DB
    assert ZoneService.get_zone(50) is None

def test_delete_zone_not_found(db_session):
    """ZoneService.delete_zone returns False if the zone is not found."""
    assert ZoneService.delete_zone(999) is False  # 999 not in DB

def test_import_zones_empty_list(db_session):
    """ZoneService.import_zones returns a failure message for empty input list and does not delete existing zones."""
    # Create a zone that should remain if import is skipped
    ZoneService.create_zone(zone_id=111, name="Existing", description="Should stay")
    result = ZoneService.import_zones([])  # empty data
    assert result == "Failure to enumerate zones, possibly unsupported"
    # Existing zone should still exist (no deletion happened)
    assert ZoneService.get_zone(111) is not None

def test_import_zones_delete_error(monkeypatch, db_session):
    """ZoneService.import_zones returns error message if deleting existing zones fails."""
    # Create a dummy zone to trigger delete
    ZoneService.create_zone(zone_id=200, name="TempZone", description="")
    # Monkeypatch the delete operation to throw an exception
    def fake_delete():
        raise Exception("DB error")
    monkeypatch.setattr(Zone.query, "delete", staticmethod(fake_delete))
    result = ZoneService.import_zones([{"address": 201, "zone_name": "Z201"}])
    assert result == "Error deleting existing zones."
    # Original zone should still remain since deletion failed
    assert ZoneService.get_zone(200) is not None

def test_import_zones_duplicate_ids(monkeypatch, db_session):
    """ZoneService.import_zones returns an error if final commit fails (e.g., duplicate IDs)."""
    data = [
        {"address": 301, "zone_name": "Z301"},
        {"address": 302, "zone_name": "Z302"}
    ]
    # Monkeypatch session.commit after adding zones to raise IntegrityError
    def fake_final_commit():
        raise IntegrityError("dup error", orig=None, params=None)
    monkeypatch.setattr(Zone.query.session, "commit", fake_final_commit)
    result = ZoneService.import_zones(data)
    assert result == "Error importing zones (duplicate IDs)."
    # Nothing should be committed; verify no zones were added
    assert ZoneService.get_zone(301) is None and ZoneService.get_zone(302) is None

def test_import_zones_success(db_session):
    """ZoneService.import_zones deletes existing and imports provided zones, skipping duplicates."""
    # Seed an existing zone that should be wiped by import
    ZoneService.create_zone(zone_id=400, name="WillDelete", description="")
    # Prepare input with duplicate addresses
    input_data = [
        {"address": 401, "zone_name": "Z401"},
        {"address": 401, "zone_name": "Z401_Duplicate"},  # duplicate address in list
        {"address": 402, "zone_name": ""}  # blank name should still be added with description fallback
    ]
    result = ZoneService.import_zones(input_data)
    # Should return a dict of imported zones (keys 401, 402)
    assert isinstance(result, dict) and 401 in result and 402 in result
    assert result[401]["name"] == "Z401"  # uses first occurrence
    # Zone 401 and 402 should now exist, and old zone 400 should be gone
    assert ZoneService.get_zone(400) is None
    zone401 = ZoneService.get_zone(401); zone402 = ZoneService.get_zone(402)
    assert zone401 is not None and zone402 is not None
    # Ensure duplicate input didn't create extra zone (only one zone for address 401)
    zones_all = ZoneService.list_zones()
    zone_ids = [z.zone_id for z in zones_all]
    assert zone_ids.count(401) == 1 and 401 in zone_ids and 402 in zone_ids
