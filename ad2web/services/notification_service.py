from ad2web.extensions import db
from ad2web.notifications.models import Notification, NotificationSetting, NotificationMessage
from ad2web import zone_service, user_service  # assume zone_service and user_service are available


class NotificationService:
    """Service layer for notification business logic."""

    def get_notifications(self, user):
        """Fetch notifications accessible by the given user."""
        if user.is_admin():
            notifications = Notification.query.all()
        else:
            notifications = Notification.query.filter_by(user_id=user.id).all()
        return notifications

    def get_notification(self, notif_id):
        """Fetch a notification by ID, or None if not found."""
        return Notification.query.filter_by(id=notif_id).first()

    def create_notification(self, notif_type, description, user, settings_form=None):
        """
        Create a new notification of the given type for the user.
        `notif_type` is the integer code or name for the notification type.
        `settings_form` is an optional form object with fields to populate NotificationSettings.
        """
        notif = Notification()
        # If notif_type is given as name (string), convert to code if needed
        if isinstance(notif_type, str):
            # Assume NOTIFICATION_TYPES maps code->name, find matching code
            from ad2web.notifications.constants import NOTIFICATION_TYPES
            for code, name in NOTIFICATION_TYPES.items():
                if name.lower() == notif_type.lower():
                    notif.type = code
                    break
        else:
            notif.type = notif_type
        notif.description = description
        notif.user = user_service.get_user(user.id) if hasattr(user_service,
                                                               "get_user") else user  # use user_service if available
        db.session.add(notif)
        # Populate settings via form if provided
        if settings_form:
            # Use the form's populate_settings to create NotificationSetting objects
            settings_form.populate_settings(notif.settings)
        db.session.commit()  # commit will save notification and associated settings
        # Refresh internal notifier threads to pick up the new notification
        try:
            from flask import current_app
            current_app.decoder.refresh_notifier(notif.id)
        except Exception:
            pass
        return notif

    def update_notification(self, notif, settings_form=None, new_description=None):
        """
        Update an existing notification's settings and description.
        `notif` is a Notification object.
        """
        if new_description is not None:
            notif.description = new_description
        if settings_form:
            settings_form.populate_settings(notif.settings, id=notif.id)
        # Ensure notification is enabled after edits (preserves original behavior)
        notif.enabled = 1
        db.session.add(notif)
        db.session.commit()
        try:
            from flask import current_app
            current_app.decoder.refresh_notifier(notif.id)
        except Exception:
            pass
        return notif

    def delete_notification(self, notif):
        """Delete a notification and its settings."""
        db.session.delete(notif)
        db.session.commit()
        try:
            from flask import current_app
            current_app.decoder.refresh_notifier(notif.id)
        except Exception:
            pass

    def toggle_notification(self, notif):
        """Toggle a notification's enabled status and return the new status string."""
        if notif.enabled == 0:
            notif.enabled = 1
            status = "Enabled"
        else:
            notif.enabled = 0
            status = "Disabled"
        db.session.add(notif)
        db.session.commit()
        try:
            from flask import current_app
            current_app.decoder.refresh_notifier(notif.id)
        except Exception:
            pass
        return status

    def copy_notification(self, notif):
        """
        Clone a notification (and its settings). Returns the new cloned Notification.
        """
        # Make a transient copy of the Notification
        from sqlalchemy.orm.session import make_transient
        db.session.expunge(notif)
        make_transient(notif)
        original_id = notif.id  # store original ID for settings copy
        notif.id = None
        notif.description = (notif.description or "") + " Clone"
        db.session.add(notif)
        db.session.flush()  # flush to assign new ID
        new_id = notif.id
        # Copy settings
        old_settings = NotificationSetting.query.filter_by(notification_id=original_id).all()
        for s in old_settings:
            db.session.expunge(s)
            make_transient(s)
            s.id = None
            s.notification_id = new_id
            db.session.add(s)
        db.session.commit()
        try:
            from flask import current_app
            current_app.decoder.refresh_notifier(new_id)
        except Exception:
            pass
        return notif

    def update_zone_filter(self, notif, zone_id_list):
        """
        Update the zone_filter setting for a notification.
        `zone_id_list` is a list of zone IDs (as strings or ints) to filter on.
        """
        # Store the selected zones as JSON in the 'zone_filter' setting
        setting_value = [] if zone_id_list is None else [int(z) for z in zone_id_list]
        # Find existing setting or create new
        setting = NotificationSetting.query.filter_by(notification_id=notif.id, name='zone_filter').first()
        if not setting:
            setting = NotificationSetting(name='zone_filter', notification=notif)
        import json
        setting.value = json.dumps(setting_value)
        db.session.add(setting)
        db.session.commit()
        return setting_value

    def get_zone_choices(self):
        """
        Retrieve zone choices (id and name) for use in zone filtering.
        Uses zone_service instead of direct model queries.
        """
        zones = zone_service.get_all_zones() if hasattr(zone_service, "get_all_zones") else []
        # Build a list of 1-99 zones with placeholder names, then replace with actual names where available
        zone_list = [(str(i), f"Zone {i:02d}") for i in range(1, 100)]
        # Replace placeholder with configured zone names
        for z in zones:
            zid = getattr(z, 'zone_id', None) or getattr(z, 'id', None) or z.zone_id
            name = getattr(z, 'name', None) or ""
            if 1 <= int(zid) <= 99:
                zone_list[int(zid) - 1] = (str(zid),
                                           f"Zone {int(zid):02d} - {name}" if name else f"Zone {int(zid):02d}")
        return zone_list


# Instantiate a singleton service for convenience
notification_service = NotificationService()
