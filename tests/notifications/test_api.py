import json
import pytest
from ad2web.services.notification_service import NotificationService
from ad2web.notifications.constants import EMAIL, EVENT_ZONE_FAULT

def test_api_list_empty(admin_client):
    res = admin_client.get("/api/notifications")
    assert res.status_code == 200
    assert res.get_json() == {"notifications": []}

def test_api_list_requires_auth(client):
    res = client.get("/api/notifications")
    assert res.status_code == 401

def test_api_create_and_get(admin_client, db_session, normal_user):
    payload = {
        "notif_type": EMAIL,
        "event": EVENT_ZONE_FAULT,
        "destination": "foo@bar.com",
        "message": "Msg"
    }
    res1 = admin_client.post("/api/notifications", json=payload)
    assert res1.status_code == 201
    data1 = res1.get_json()
    nid = data1["id"]

    res2 = admin_client.get(f"/api/notifications/{nid}")
    assert res2.status_code == 200
    data2 = res2.get_json()
    assert data2["destination"] == "foo@bar.com"

def test_api_create_missing_fields(admin_client):
    res = admin_client.post("/api/notifications", json={"notif_type": EMAIL})
    assert res.status_code == 422
    assert "Missing" in res.get_json()["message"]

def test_api_update_and_delete(admin_client, db_session, normal_user):
    notif = NotificationService.create_notification(
        user_id=normal_user.id,
        notif_type=EMAIL,
        event=EVENT_ZONE_FAULT,
        destination="a@b.com",
        message=""
    )
    # Update
    res1 = admin_client.put(f"/api/notifications/{notif.id}", json={"message": "New"})
    assert res1.status_code == 200
    assert res1.get_json()["message"] == "New"
    # Delete
    res2 = admin_client.delete(f"/api/notifications/{notif.id}")
    assert res2.status_code == 204
    # Now 404 when fetching
    res3 = admin_client.get(f"/api/notifications/{notif.id}")
    assert res3.status_code == 404
