from flask import current_app
from werkzeug.exceptions import NotFound, Forbidden
from ..extensions import db
from ..notifications.models import Notification, NotificationSetting, NotificationMessage
from ..notifications.constants import UPNPPUSH  # constant for UPNP Push type

class NotificationService:
    """Service layer for managing notifications and related objects."""

    @staticmethod
    def get_notifications(user):
        """
        Fetch notifications visible to the given user.
        Admin users get all notifications; regular users get only their own.
        """
        if user.is_admin():
            # Admin can see all notifications
            return Notification.query.all()
        else:
            # Regular user sees only their own notifications
            return Notification.query.filter_by(user_id=user.id).all()

    @staticmethod
    def get_notification(notification_id, user):
        """
        Retrieve a single notification by ID, ensuring the user has access.
        Raises NotFound if the notification doesn't exist, Forbidden if access is denied.
        """
        notif = Notification.query.filter_by(id=notification_id).first()
        if not notif:
            # No such notification in database
            raise NotFound(f"Notification id {notification_id} not found")
        # Permission check: user must own notification or be admin
        if notif.user_id != user.id and not user.is_admin():
            raise Forbidden("You do not have access to this notification")
        return notif

    @staticmethod
    def create_notification(user, type_id, description, settings_data):
        """
        Create a new notification for the given user with specified type, description, and settings.
        Returns the new Notification object.
        Non-admin users can only create notifications for themselves (enforced by using `user` parameter).
        """
        # Assemble Notification object
        new_notif = Notification(type=type_id, description=description, user_id=user.id, enabled=1)
        # Apply each setting as a NotificationSetting associated with this notification
        for name, value in settings_data.items():
            # Create a new NotificationSetting for each entry
            new_setting = NotificationSetting(name=name, value=value)
            new_notif.settings[name] = new_setting  # attribute_mapped_collection by name
        db.session.add(new_notif)
        db.session.commit()
        # Notify the system to refresh any in-memory notifier state for this new notification
        current_app.decoder.refresh_notifier(new_notif.id)
        return new_notif

    @staticmethod
    def update_notification(notification_id, user, description=None, settings_data=None):
        """
        Update an existing notification's description and/or settings.
        Only allowed if user owns the notification or is admin.
        `description` or `settings_data` can be None to leave unchanged.
        """
        notif = NotificationService.get_notification(notification_id, user)  # Ensures existence and permission
        # Update fields if provided
        if description is not None:
            notif.description = description
        if settings_data:
            for name, value in settings_data.items():
                if name in notif.settings:
                    # Update existing setting
                    notif.settings[name].value = value
                else:
                    # Create new setting if not present
                    new_setting = NotificationSetting(name=name, value=value)
                    notif.settings[name] = new_setting
        # Re-enable notification by default when updating (preserve original logic)
        # (If not desired, this line can be removed. Assuming parity: any edit re-enables the notification.)
        notif.enabled = 1
        db.session.commit()
        current_app.decoder.refresh_notifier(notification_id)
        return notif

    @staticmethod
    def delete_notification(notification_id, user):
        """
        Delete a notification by ID if the user has access. Raises if not found or forbidden.
        """
        notif = NotificationService.get_notification(notification_id, user)
        db.session.delete(notif)
        db.session.commit()
        # Refresh notifier state to remove this notification from active configuration
        current_app.decoder.refresh_notifier(notification_id)

    @staticmethod
    def copy_notification(notification_id, user):
        """
        Clone a notification (and its settings) to a new notification.
        Returns the new Notification object. Only allowed if user has access to the source notification.
        """
        notif = NotificationService.get_notification(notification_id, user)
        # Detach the original object from session to prepare for cloning
        db.session.expunge(notif)
        # Reset identity and fields for clone
        notif.id = None
        notif.description = notif.description + " Clone"
        notif.user_id = user.id  # ensure clone belongs to the requesting user (or admin creating for self)
        # Add clone to session
        db.session.add(notif)
        db.session.flush()  # flush to assign new ID (so we can use it for settings clone)
        new_id = notif.id
        # Clone settings
        original_settings = NotificationSetting.query.filter_by(notification_id=notification_id).all()
        for s in original_settings:
            db.session.expunge(s)
            s.id = None
            s.notification_id = new_id
            db.session.add(s)
        db.session.commit()
        current_app.decoder.refresh_notifier(new_id)
        return notif

    @staticmethod
    def toggle_notification(notification_id, user):
        """
        Toggle (enable/disable) a notification's active status.
        Returns the new enabled status (True/False).
        """
        notif = NotificationService.get_notification(notification_id, user)
        new_status = True
        if notif.enabled and notif.enabled != 0:
            notif.enabled = 0
            new_status = False
        else:
            notif.enabled = 1
            new_status = True
        db.session.commit()
        current_app.decoder.refresh_notifier(notification_id)
        return new_status

    @staticmethod
    def test_notification(notification_id, user):
        """
        Trigger a test send for the given notification. Returns an error message if any, otherwise None on success.
        """
        notif = NotificationService.get_notification(notification_id, user)
        # Use the decoder's test method to send a test notification
        error = current_app.decoder.test_notifier(notification_id)
        return error  # error is None if successful, or contains a message string on failure

    @staticmethod
    def get_notification_messages(user):
        """
        Retrieve all system notification message templates (admin only).
        """
        if not user.is_admin():
            raise Forbidden("Only administrators can view notification messages.")
        return NotificationMessage.query.all()

    @staticmethod
    def update_notification_message(message_id, new_text, user):
        """
        Update a notification message template's text by ID (admin only).
        """
        if not user.is_admin():
            raise Forbidden("Only administrators can modify notification messages.")
        msg = NotificationMessage.query.filter_by(id=message_id).first()
        if not msg:
            raise NotFound(f"Notification message id {message_id} not found")
        msg.text = new_text
        db.session.commit()
        return msg
