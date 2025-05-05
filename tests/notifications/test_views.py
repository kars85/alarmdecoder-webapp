from ad2web.notifications.models import Notification
from ad2web.notifications.constants import EMAIL, EVENT_ZONE_FAULT

def test_index_requires_login(client):
    """Unauthenticated users are redirected from /settings/notifications."""
    res = client.get("/settings/notifications/")
    assert res.status_code == 302 and "/login" in res.headers["Location"]

def test_index_for_admin(admin_client):
    """Admin can load the notifications index page."""
    res = admin_client.get("/settings/notifications/")
    assert res.status_code == 200
    assert b"Notifications" in res.data

def test_create_get(admin_client):
    res = admin_client.get("/settings/notifications/create")
    assert res.status_code == 200
    assert b"Create Notification" in res.data

def test_create_post_success(admin_client, db_session, normal_user):
    form = {
        "notif_type": EMAIL,
        "event": EVENT_ZONE_FAULT,
        "destination": "test@example.com",
        "message": ""
    }
    res = admin_client.post("/settings/notifications/create", data=form, follow_redirects=True)
    assert res.status_code == 200
    assert b"Notification created" in res.data
    # Confirm DB
    notif = Notification.query.filter_by(destination="test@example.com").first()
    assert notif and notif.notif_type == EMAIL

def test_create_post_validation_error(admin_client):
    form = {
        "notif_type": EMAIL,
        "event": EVENT_ZONE_FAULT,
        "destination": "",  # missing
        "message": ""
    }
    res = admin_client.post("/settings/notifications/create", data=form)
    assert res.status_code == 200
    assert b"This field is required" in res.data

def test_edit_get_and_post(admin_client, db_session, normal_user):
    # seed one
    notif = Notification(
        user_id=normal_user.id,
        notif_type=EMAIL,
        event=EVENT_ZONE_FAULT,
        destination="e@x.com",
        message=""
    )
    db_session.session.add(notif)
    db_session.session.commit()

    # GET edit page
    res1 = admin_client.get(f"/settings/notifications/edit/{notif.id}")
    assert res1.status_code == 200
    assert b"Edit Notification" in res1.data

    # POST update
    form = {"destination": "new@x.com", "message": "Updated"}
    res2 = admin_client.post(f"/settings/notifications/edit/{notif.id}", data=form, follow_redirects=True)
    assert res2.status_code == 200
    assert b"Notification updated" in res2.data
    updated = Notification.query.get(notif.id)
    assert updated.destination == "new@x.com" and updated.message == "Updated"

def test_delete_notification(admin_client, db_session, normal_user):
    notif = NotificationService.create_notification(
        user_id=normal_user.id,
        notif_type=EMAIL,
        event=EVENT_ZONE_FAULT,
        destination="del@example.com",
        message=""
    )
    res = admin_client.post(f"/settings/notifications/delete/{notif.id}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    with pytest.raises(Exception):
        # now gone
        NotificationService.get_notification(notif.id)
