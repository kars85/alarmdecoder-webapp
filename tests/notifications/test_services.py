import pytest
from sqlalchemy.exc import IntegrityError

from ad2web.services.notification_service import (
    NotificationService, NotificationTypeError, NotificationNotFoundError
)
from ad2web.notifications.models import Notification
from ad2web.notifications.constants import (
    EMAIL, SMS, EVENT_ZONE_FAULT, DEFAULT_EVENT_MESSAGES
)

def test_list_notifications_empty(db_session):
    """Returns empty list when no notifications exist."""
    assert NotificationService.list_notifications() == []

def test_create_email_notification_success(db_session, normal_user):
    """Creating an email notification returns the Notification instance."""
    notif = NotificationService.create_notification(
        user_id=normal_user.id,
        notif_type=EMAIL,
        event=EVENT_ZONE_FAULT,
        destination="user@example.com",
        message="Custom message"
    )
    assert isinstance(notif, Notification)
    # persisted
    found = Notification.query.get(notif.id)
    assert found and found.destination == "user@example.com"

def test_create_sms_notification_missing_destination(db_session, normal_user):
    """Missing required destination for SMS should raise NotificationTypeError."""
    with pytest.raises(NotificationTypeError):
        NotificationService.create_notification(
            user_id=normal_user.id,
            notif_type=SMS,
            event=EVENT_ZONE_FAULT,
            destination="",          # SMS needs a phone number
            message=""
        )

def test_create_notification_bad_type(db_session, normal_user):
    """An unsupported notification type should raise NotificationTypeError."""
    with pytest.raises(NotificationTypeError):
        NotificationService.create_notification(
            user_id=normal_user.id,
            notif_type="PUSH",  # not implemented
            event=EVENT_ZONE_FAULT,
            destination="x",
            message=""
        )

def test_get_notification_not_found(db_session):
    """Looking up a nonexistent ID raises NotificationNotFoundError."""
    with pytest.raises(NotificationNotFoundError):
        NotificationService.get_notification(9999)

def test_get_notification_success(db_session, normal_user):
    """Retrieving an existing notification returns it."""
    notif = NotificationService.create_notification(
        user_id=normal_user.id,
        notif_type=EMAIL,
        event=EVENT_ZONE_FAULT,
        destination="foo@bar.com",
        message=""
    )
    fetched = NotificationService.get_notification(notif.id)
    assert fetched.id == notif.id

def test_update_notification_success(db_session, normal_user):
    """Updating destination and message persists changes."""
    notif = NotificationService.create_notification(
        user_id=normal_user.id,
        notif_type=EMAIL,
        event=EVENT_ZONE_FAULT,
        destination="old@x.com",
        message="Old"
    )
    updated = NotificationService.update_notification(
        notif.id,
        destination="new@x.com",
        message="New message"
    )
    assert updated.destination == "new@x.com"
    assert updated.message == "New message"

def test_update_notification_not_found(db_session):
    """Updating a nonexistent notification raises NotificationNotFoundError."""
    with pytest.raises(NotificationNotFoundError):
        NotificationService.update_notification(9999, destination="x", message="y")

def test_delete_notification_success(db_session, normal_user):
    """Deleting an existing notification returns True and removes it."""
    notif = NotificationService.create_notification(
        user_id=normal_user.id,
        notif_type=EMAIL,
        event=EVENT_ZONE_FAULT,
        destination="del@x.com",
        message=""
    )
    assert NotificationService.delete_notification(notif.id) is True
    with pytest.raises(NotificationNotFoundError):
        NotificationService.get_notification(notif.id)

def test_delete_notification_not_found(db_session):
    """Deleting nonexistent notification returns False."""
    assert NotificationService.delete_notification(9999) is False
