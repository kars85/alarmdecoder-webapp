import pytest
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import NotFound

from ad2web.services.notification_service import NotificationService
from ad2web.notifications.models import Notification
from ad2web.notifications import constants

# If event constants are needed:
ZONE_FAULT = constants.ZONE_FAULT  # replaces EVENT_ZONE_FAULT, if used in tests

def test_list_notifications_empty(db_session):
    """Returns empty list when no notifications exist."""
    notifications = NotificationService.get_notifications(normal_user=None)  # No notifications in DB
    assert notifications == []

def test_create_email_notification_success(db_session, normal_user):
    """Creating an email notification returns the Notification instance."""
    # Create an email notification (type name 'EMAIL')
    notif = NotificationService.create_notification(normal_user, "EMAIL", "Test email notification")
    # persisted
    found = Notification.query.get(notif.id)
    assert found is not None
    assert found.description == "Test email notification"
    # No destination was set during creation, it should default to no setting
    assert found.get_setting('destination') is None

def test_create_sms_notification_missing_destination(db_session, normal_user):
    """Creating an SMS notification with no phone number yields an empty destination setting."""
    # 'TWILIO' represents SMS notification type in constants
    notif = NotificationService.create_notification(normal_user, "TWILIO", "Test SMS notification", settings_data={"destination": ""})
    found = Notification.query.get(notif.id)
    assert found is not None
    # Destination setting should exist but be blank
    assert found.get_setting('destination') == ""

def test_create_notification_bad_type(db_session, normal_user):
    """An unsupported notification type should result in a database integrity error."""
    # Passing an invalid type name 'PUSH' (not in NOTIFICATION_TYPES)
    with pytest.raises(IntegrityError):
        NotificationService.create_notification(normal_user, "PUSH", "Bad type notification")
        # The commit inside create_notification will raise IntegrityError due to type not set:contentReference[oaicite:8]{index=8}

def test_get_notification_not_found(db_session, normal_user):
    """Looking up a nonexistent ID raises NotFound."""
    with pytest.raises(NotFound):
        NotificationService.get_notification(99999, normal_user)  # ID does not exist

def test_get_notification_success(db_session, normal_user):
    """Retrieving an existing notification returns it."""
    # First, create a notification to fetch
    notif = NotificationService.create_notification(normal_user, "EMAIL", "Fetchable notification")
    fetched = NotificationService.get_notification(notif.id, normal_user)
    assert fetched is not None
    assert fetched.id == notif.id
    assert fetched.description == "Fetchable notification"

def test_update_notification_success(db_session, normal_user):
    """Updating destination and message persists changes."""
    # Seed a notification to update
    notif = NotificationService.create_notification(normal_user, "EMAIL", "Updatable notification", settings_data={"destination": "old@example.com", "message": "Old message"})
    # Update its destination and message
    updated = NotificationService.update_notification(notif.id, normal_user, settings_data={"destination": "new@example.com", "message": "Updated message"})
    # Fetch from DB to verify changes
    found = Notification.query.get(notif.id)
    assert found.get_setting('destination') == "new@example.com"
    assert found.get_setting('message') == "Updated message"

def test_update_notification_not_found(db_session, normal_user):
    """Updating a nonexistent notification raises NotFound."""
    with pytest.raises(NotFound):
        NotificationService.update_notification(123456, normal_user, settings_data={"destination": "x", "message": "y"})

def test_delete_notification_success(db_session, normal_user):
    """Deleting an existing notification removes it from the database."""
    notif = NotificationService.create_notification(normal_user, "EMAIL", "Will be deleted")
    NotificationService.delete_notification(notif.id, normal_user)
    # Confirm it’s removed
    assert Notification.query.get(notif.id) is None

def test_delete_notification_not_found(db_session, normal_user):
    """Deleting a nonexistent notification raises NotFound."""
    with pytest.raises(NotFound):
        NotificationService.delete_notification(424242, normal_user)
