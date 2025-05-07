# ad2web/forms/notification_form.py (after definitions of specific form classes)
from ad2web.notifications import constants  # import to access type constants

class NotificationForm:
    """Unified form wrapper that dispatches to a specific notification form based on type."""
    def __init__(self, *args, **kwargs):
        data = kwargs.get('data') or {}
        notif_type = data.get('notif_type')
        # Allow numeric or string type identifiers
        if isinstance(notif_type, str):
            if notif_type.isdigit():
                notif_type = int(notif_type)
            else:
                # Convert type name to code if given (e.g. "email" -> 0)
                name = notif_type.lower()
                name_to_code = {v.lower(): k for k, v in constants.NOTIFICATION_TYPES.items()}
                notif_type = name_to_code.get(name, None)
        # Map notification type code to the corresponding Form class
        type_map = {
            constants.EMAIL:    EmailNotificationForm,
            constants.PUSHOVER: PushoverNotificationForm,
            constants.TWILIO:   TwilioNotificationForm,
            constants.PROWL:    ProwlNotificationForm,
            constants.GROWL:    GrowlNotificationForm,
            constants.CUSTOM:   CustomPostForm,
            constants.TWIML:    TwiMLNotificationForm,
            constants.UPNPPUSH: UPNPPushNotificationForm,
            # constants.MATRIX:   MatrixNotificationForm  (if Matrix form is implemented later)
        }
        FormClass = type_map.get(notif_type)
        if FormClass:
            self._form = FormClass(*args, **kwargs)  # instantiate the specific form
            self.errors = {}  # initialize errors container
        else:
            self._form = None
            # Prepare an errors dict to simulate a form field error for unsupported type
            self.errors = {"notif_type": ["Unsupported notification type"]}
    def validate(self):
        """Validate the form by delegating to the specific notification form."""
        if not self._form:
            return False
        return self._form.validate()
    def __getattr__(self, name):
        """Delegate attribute access to the underlying form (so fields/errors are accessible)."""
        return getattr(self._form, name)
