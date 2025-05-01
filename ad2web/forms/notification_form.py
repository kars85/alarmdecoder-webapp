import re, ast, json
from flask_wtf import FlaskForm as Form
from wtforms import ValidationError, StringField, HiddenField, PasswordField, SubmitField, TextAreaField, IntegerField, RadioField, BooleanField, SelectField, SelectMultipleField, FormField, FieldList
from wtforms.validators import DataRequired, InputRequired, Optional, Length, NumberRange
from ad2web.notifications.constants import (NOTIFICATIONS, SUBSCRIPTIONS, PUSHOVER_PRIORITIES, PROWL_PRIORITIES, GROWL_PRIORITIES, GROWL_TITLE,
                                            URLENCODE, JSON, XML, CUSTOM_METHOD_POST, CUSTOM_METHOD_GET_TYPE, LOWEST, LOW, NORMAL, HIGH, EMERGENCY)
from ad2web.notifications.models import NotificationSetting
from ad2web.widgets import ButtonField, MultiCheckboxField

class NotificationButtonForm(Form):
    """Form for combined Save/Cancel/Test buttons in review."""
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    submit = SubmitField('Save')
    test = SubmitField('Save & Test')

class CreateNotificationForm(Form):
    """Initial form to choose notification type."""
    # Choices: use NOTIFICATIONS dict (OrderedDict mapping type code to name) for types
    type = SelectField('Notification Type', choices=[(str(code), name) for code, name in NOTIFICATIONS.items()])  # use (code,name) pairs
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")

class EditNotificationMessageForm(Form):
    """Admin form to edit a notification message template."""
    id = StringField()  # HiddenField could be used, using StringField for simplicity
    text = TextAreaField('Message Text', [DataRequired(), Length(max=255)])
    submit = SubmitField('Save')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications/messages'")

class NotificationReviewForm(Form):
    """Form for the review step, contains Save and Test buttons."""
    buttons = FormField(NotificationButtonForm)

class TimeValidator:
    """Custom validator to ensure time fields are in HH:MM:SS format."""
    def __init__(self, message=None):
        self.message = message or 'Field must be in the 24 hour time format: 00:00:00'
    def __call__(self, form, field):
        match = re.match(r"^(\d\d):(\d\d):(\d\d)$", field.data)
        if match is None:
            raise ValidationError(self.message)
        h, m, s = map(int, match.groups())
        if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= s <= 59):
            raise ValidationError(self.message)

class TimeSettingsInternalForm(Form):
    """Internal sub-form for time restriction and delay settings."""
    starttime = StringField('Start Time', [InputRequired(), Length(max=8), TimeValidator()], default='00:00:00', description='Start time for this event notification (24hr format)')
    endtime   = StringField('End Time', [InputRequired(), Length(max=8), TimeValidator()], default='23:59:59', description='End time for this event notification (24hr format)')
    delaytime = IntegerField('Zone Tracker Notification Delay', [InputRequired(), NumberRange(min=0)], default=0, description='Time in minutes to delay sending Zone Tracker notification')
    suppress  = BooleanField('Suppress Zone Restore?', [Optional()], description='Suppress notification if zone restores before delay period')
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class EditNotificationForm(Form):
    """Base form for editing/creating notifications of specific types."""
    type = HiddenField()
    description = StringField('Description', [DataRequired(), Length(max=255)], description='Brief description of this notification')
    suppress_timestamp = BooleanField('Suppress Timestamp?', [Optional()], description='Remove timestamp from message body and subject')
    time_field = FormField(TimeSettingsInternalForm)
    subscriptions = MultiCheckboxField('Notification Events', choices=[(str(k), v) for k, v in SUBSCRIPTIONS.items()])
    def populate_setting(self, name, value, id=None):
        # Create or update a NotificationSetting for the given name/value
        if id is not None:
            setting = NotificationSetting.query.filter_by(notification_id=id, name=name).first()
        else:
            setting = NotificationSetting(name=name)
        setting.value = value
        return setting
    def populate_from_setting(self, id, name, default=None):
        ret = default
        setting = NotificationSetting.query.filter_by(notification_id=id, name=name).first()
        if setting is not None:
            ret = setting.value
        return ret
    def populate_settings(self, settings, id=None):
        # Common settings across all notification types
        settings['subscriptions'] = self.populate_setting('subscriptions', json.dumps({str(k): True for k in self.subscriptions.data}), id)
        settings['starttime'] = self.populate_setting('starttime', self.time_field.starttime.data or '00:00:00', id)
        settings['endtime']   = self.populate_setting('endtime', self.time_field.endtime.data or '23:59:59', id)
        settings['delay']     = self.populate_setting('delay', self.time_field.delaytime.data, id)
        settings['suppress']  = self.populate_setting('suppress', self.time_field.suppress.data, id)
        settings['suppress_timestamp'] = self.populate_setting('suppress_timestamp', self.suppress_timestamp.data, id)
    def populate_from_settings(self, id):
        # Load common settings from NotificationSetting entries
        subscriptions_json = self.populate_from_setting(id, 'subscriptions')
        if subscriptions_json:
            try:
                sub_dict = json.loads(subscriptions_json)
                # Convert dict of {"event_type": true} to list of event_type keys
                self.subscriptions.data = [k for k, v in sub_dict.items() if v]
            except Exception:
                self.subscriptions.data = []
        self.time_field.starttime.data = self.populate_from_setting(id, 'starttime', default='00:00:00')
        self.time_field.endtime.data   = self.populate_from_setting(id, 'endtime',   default='23:59:59')
        self.time_field.delaytime.data = self.populate_from_setting(id, 'delay',     default=0)
        # Ensure delaytime is numeric
        if self.time_field.delaytime.data in (None, ''):
            self.time_field.delaytime.data = 0
        self.time_field.suppress.data   = self.populate_from_setting(id, 'suppress', default=False)
        self.suppress_timestamp.data    = self.populate_from_setting(id, 'suppress_timestamp', default=False)

class CustomValueForm(Form):
    """Internal form for dynamic key-value fields (used in custom notifications)."""
    custom_key   = StringField(label=None)
    custom_value = StringField(label=None)
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

# Notification-type specific forms, grouped by type:

# 1. Email Notification
class EmailNotificationInternalForm(Form):
    source               = StringField('Source Address (From)', [DataRequired(), Length(max=255)], default='youremail@example.com', description='Emails will originate from this address')
    destination          = StringField('Destination Address (To)', [DataRequired(), Length(max=255)], description='Emails will be sent to this address')
    subject              = StringField('Email Subject', [DataRequired(), Length(max=255)], default='AlarmDecoder: Alarm Event', description='Subject line for the email notification')
    server               = StringField('SMTP Server', [DataRequired(), Length(max=255)], default='localhost', description='SMTP server address')
    port                 = IntegerField('Server Port', [DataRequired(), NumberRange(min=1, max=65535)], default=25, description='SMTP server port')
    tls                  = BooleanField('Use TLS?', default=False)
    ssl                  = BooleanField('Use SSL?', default=False)
    authentication_DataRequired = BooleanField('Require Authentication?', default=False)
    username             = StringField('Username', [Optional(), Length(max=255)])
    password             = PasswordField('Password', [Optional(), Length(max=255)])
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class EmailNotificationForm(EditNotificationForm):
    legend = ("<div style=\"font-size: 16px;\"></div>")
    form_field = FormField(EmailNotificationInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_settings(self, settings, id=None):
        super().populate_settings(settings, id)
        settings['source'] = self.populate_setting('source', self.form_field.source.data, id)
        settings['destination'] = self.populate_setting('destination', self.form_field.destination.data, id)
        settings['subject'] = self.populate_setting('subject', self.form_field.subject.data, id)
        settings['server'] = self.populate_setting('server', self.form_field.server.data, id)
        settings['port'] = self.populate_setting('port', self.form_field.port.data, id)
        settings['tls'] = self.populate_setting('tls', self.form_field.tls.data, id)
        settings['ssl'] = self.populate_setting('ssl', self.form_field.ssl.data, id)
        settings['authentication_DataRequired'] = self.populate_setting('authentication_DataRequired', self.form_field.authentication_DataRequired.data, id)
        settings['username'] = self.populate_setting('username', self.form_field.username.data, id)
        settings['password'] = self.populate_setting('password', self.form_field.password.data, id)
    def populate_from_settings(self, id):
        super().populate_from_settings(id)
        self.form_field.source.data = self.populate_from_setting(id, 'source')
        self.form_field.destination.data = self.populate_from_setting(id, 'destination')
        self.form_field.subject.data = self.populate_from_setting(id, 'subject')
        self.form_field.server.data = self.populate_from_setting(id, 'server')
        self.form_field.port.data = self.populate_from_setting(id, 'port')
        self.form_field.tls.data = self.populate_from_setting(id, 'tls', default=False)
        self.form_field.ssl.data = self.populate_from_setting(id, 'ssl', default=False)
        self.form_field.authentication_DataRequired.data = self.populate_from_setting(id, 'authentication_DataRequired', default=False)
        # Password: do not mask the value on form repopulation
        self.form_field.password.widget.hide_value = False
        self.form_field.password.data = self.populate_from_setting(id, 'password')

# 2. Pushover Notification
class PushoverNotificationInternalForm(Form):
    token    = StringField('API Token', [DataRequired(), Length(max=30)], description='Your Pushover Application API Token')
    user_key = StringField('User/Group Key', [DataRequired(), Length(max=30)], description='Your Pushover user or group key')
    priority = SelectField('Message Priority', choices=[PUSHOVER_PRIORITIES[LOWEST], PUSHOVER_PRIORITIES[LOW], PUSHOVER_PRIORITIES[NORMAL], PUSHOVER_PRIORITIES[HIGH], PUSHOVER_PRIORITIES[EMERGENCY]], default=PUSHOVER_PRIORITIES[LOW], description='Pushover message priority', coerce=int)
    title    = StringField('Message Title', [Length(max=255)], description='Title for Pushover notifications')
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class PushoverNotificationForm(EditNotificationForm):
    legend = ("<div style=\"font-size: 16px;\"></div>")
    form_field = FormField(PushoverNotificationInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_settings(self, settings, id=None):
        super().populate_settings(settings, id)
        settings['token'] = self.populate_setting('token', self.form_field.token.data, id)
        settings['user_key'] = self.populate_setting('user_key', self.form_field.user_key.data, id)
        settings['priority'] = self.populate_setting('priority', self.form_field.priority.data, id)
        settings['title'] = self.populate_setting('title', self.form_field.title.data, id)
    def populate_from_settings(self, id):
        super().populate_from_settings(id)
        self.form_field.token.data = self.populate_from_setting(id, 'token')
        self.form_field.user_key.data = self.populate_from_setting(id, 'user_key')
        self.form_field.priority.data = self.populate_from_setting(id, 'priority')
        self.form_field.title.data = self.populate_from_setting(id, 'title')

# 3. Twilio SMS/Call Notification
class TwilioNotificationInternalForm(Form):
    account_sid = StringField('Account SID', [DataRequired(), Length(max=50)], description='Your Twilio Account SID')
    auth_token  = StringField('Auth Token', [DataRequired(), Length(max=50)], description='Your Twilio Auth Token')
    number_to   = StringField('To Number', [DataRequired(), Length(max=15)], description='Number to send SMS/Call to')
    number_from = StringField('From Number', [DataRequired(), Length(max=15)], description='Your Twilio phone number (From)')
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class TwilioNotificationForm(EditNotificationForm):
    legend = ("<div style=\"font-size: 16px;\"></div>")
    form_field = FormField(TwilioNotificationInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_settings(self, settings, id=None):
        super().populate_settings(settings, id)
        settings['account_sid'] = self.populate_setting('account_sid', self.form_field.account_sid.data, id)
        settings['auth_token'] = self.populate_setting('auth_token', self.form_field.auth_token.data, id)
        settings['number_to'] = self.populate_setting('number_to', self.form_field.number_to.data, id)
        settings['number_from'] = self.populate_setting('number_from', self.form_field.number_from.data, id)
    def populate_from_settings(self, id):
        super().populate_from_settings(id)
        self.form_field.account_sid.data = self.populate_from_setting(id, 'account_sid')
        self.form_field.auth_token.data  = self.populate_from_setting(id, 'auth_token')
        self.form_field.number_to.data   = self.populate_from_setting(id, 'number_to')
        self.form_field.number_from.data = self.populate_from_setting(id, 'number_from')

# 4. TwiML (Twilio with Twimlet URL) Notification
class TwiMLNotificationInternalForm(Form):
    account_sid = StringField('Account SID', [DataRequired(), Length(max=50)], description='Your Twilio Account SID')
    auth_token  = StringField('Auth Token', [DataRequired(), Length(max=50)], description='Your Twilio Auth Token')
    number_to   = StringField('To Number', [DataRequired(), Length(max=15)], description='Number to send call to')
    number_from = StringField('From Number', [DataRequired(), Length(max=15)], description='Your Twilio phone number')
    twimlet_url = StringField('Twimlet URL', [DataRequired(), Length(max=255)], default="http://twimlets.com/message", description='URL of the Twimlet to use (e.g., http://twimlets.com/message)')
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class TwiMLNotificationForm(EditNotificationForm):
    legend = ("<div style=\"font-size: 16px;\"></div>")
    form_field = FormField(TwiMLNotificationInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_settings(self, settings, id=None):
        super().populate_settings(settings, id)
        settings['account_sid'] = self.populate_setting('account_sid', self.form_field.account_sid.data, id)
        settings['auth_token']  = self.populate_setting('auth_token', self.form_field.auth_token.data, id)
        settings['number_to']   = self.populate_setting('number_to', self.form_field.number_to.data, id)
        settings['number_from'] = self.populate_setting('number_from', self.form_field.number_from.data, id)
        settings['twimlet_url'] = self.populate_setting('twimlet_url', self.form_field.twimlet_url.data, id)
    def populate_from_settings(self, id):
        super().populate_from_settings(id)
        self.form_field.account_sid.data = self.populate_from_setting(id, 'account_sid')
        self.form_field.auth_token.data  = self.populate_from_setting(id, 'auth_token')
        self.form_field.number_to.data   = self.populate_from_setting(id, 'number_to')
        self.form_field.number_from.data = self.populate_from_setting(id, 'number_from')
        self.form_field.twimlet_url.data = self.populate_from_setting(id, 'twimlet_url')

# 5. Prowl Notification
class ProwlNotificationInternalForm(Form):
    prowl_api_key  = StringField('API Key', [DataRequired(), Length(max=50)], description='Your Prowl API Key')
    prowl_app_name = StringField('Application Name', [DataRequired(), Length(max=256)], default='AlarmDecoder', description='Application name to report in notifications')
    prowl_priority = SelectField('Message Priority', choices=[PROWL_PRIORITIES[LOWEST], PROWL_PRIORITIES[LOW], PROWL_PRIORITIES[NORMAL], PROWL_PRIORITIES[HIGH], PROWL_PRIORITIES[EMERGENCY]], default=PROWL_PRIORITIES[LOW], description='Prowl message priority', coerce=int)
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class ProwlNotificationForm(EditNotificationForm):
    legend = ("<div style=\"font-size: 16px;\"></div>")
    form_field = FormField(ProwlNotificationInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_settings(self, settings, id=None):
        super().populate_settings(settings, id)
        settings['prowl_api_key']  = self.populate_setting('prowl_api_key', self.form_field.prowl_api_key.data, id)
        settings['prowl_app_name'] = self.populate_setting('prowl_app_name', self.form_field.prowl_app_name.data, id)
        settings['prowl_priority'] = self.populate_setting('prowl_priority', self.form_field.prowl_priority.data, id)
    def populate_from_settings(self, id):
        super().populate_from_settings(id)
        self.form_field.prowl_api_key.data  = self.populate_from_setting(id, 'prowl_api_key')
        self.form_field.prowl_app_name.data = self.populate_from_setting(id, 'prowl_app_name')
        self.form_field.prowl_priority.data = self.populate_from_setting(id, 'prowl_priority')

# 6. Growl Notification
class GrowlNotificationInternalForm(Form):
    growl_hostname = StringField('Hostname', [DataRequired(), Length(max=255)], description='Growl server hostname/IP')
    growl_port     = StringField('Port', [DataRequired(), Length(max=10)], default='23053', description='Growl server port')
    growl_password = PasswordField('Password', description='Password for the Growl server (if any)')
    growl_title    = StringField('Title', [DataRequired(), Length(max=255)], default=GROWL_TITLE, description='Title for Growl notifications')
    growl_priority = SelectField('Message Priority', choices=[GROWL_PRIORITIES[LOWEST], GROWL_PRIORITIES[LOW], GROWL_PRIORITIES[NORMAL], GROWL_PRIORITIES[HIGH], GROWL_PRIORITIES[EMERGENCY]], default=GROWL_PRIORITIES[LOW], description='Growl message priority', coerce=int)
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class GrowlNotificationForm(EditNotificationForm):
    legend = ("<div style=\"font-size: 16px;\"></div>")
    form_field = FormField(GrowlNotificationInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_settings(self, settings, id=None):
        super().populate_settings(settings, id)
        settings['growl_hostname'] = self.populate_setting('growl_hostname', self.form_field.growl_hostname.data, id)
        settings['growl_port']     = self.populate_setting('growl_port', self.form_field.growl_port.data, id)
        settings['growl_password'] = self.populate_setting('growl_password', self.form_field.growl_password.data, id)
        settings['growl_title']    = self.populate_setting('growl_title', self.form_field.growl_title.data, id)
        settings['growl_priority'] = self.populate_setting('growl_priority', self.form_field.growl_priority.data, id)
    def populate_from_settings(self, id):
        super().populate_from_settings(id)
        self.form_field.growl_hostname.data = self.populate_from_setting(id, 'growl_hostname')
        self.form_field.growl_port.data     = self.populate_from_setting(id, 'growl_port')
        self.form_field.growl_password.data = self.populate_from_setting(id, 'growl_password')
        self.form_field.growl_title.data    = self.populate_from_setting(id, 'growl_title')
        self.form_field.growl_priority.data = self.populate_from_setting(id, 'growl_priority')

# 7. Custom HTTP/POST Notification
class CustomPostInternalForm(Form):
    custom_url    = StringField('Base URL', [DataRequired(), Length(max=255)], description='Base URL of the endpoint (e.g., https://api.example.com)')
    custom_path   = StringField('Path', [Optional(), Length(max=255)], description='URL path to post to (optional)')
    is_ssl        = BooleanField('Use SSL?', default=False)
    method        = RadioField('HTTP Method', choices=[(CUSTOM_METHOD_POST, 'POST'), (CUSTOM_METHOD_GET_TYPE, 'GET')], default=CUSTOM_METHOD_POST, coerce=int)
    post_type     = RadioField('Content Type', choices=[(URLENCODE, 'Form URL Encoded'), (JSON, 'JSON'), (XML, 'XML')], default=URLENCODE, coerce=int)
    require_auth  = BooleanField('Use HTTP Basic Auth?', default=False)
    auth_username = StringField('Auth Username', [Optional(), Length(max=255)])
    auth_password = PasswordField('Auth Password', [Optional(), Length(max=255)])
    custom_values = FieldList(FormField(CustomValueForm), validators=[Optional()], label=None)
    add_field     = ButtonField('Add Field', onclick='addField();')
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class CustomPostForm(EditNotificationForm):
    legend = ("<div style=\"font-size: 16px;\"></div>")
    form_field = FormField(CustomPostInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_settings(self, settings, id=None):
        super().populate_settings(settings, id)
        settings['custom_url']   = self.populate_setting('custom_url', self.form_field.custom_url.data, id)
        settings['custom_path']  = self.populate_setting('custom_path', self.form_field.custom_path.data, id)
        settings['is_ssl']       = self.populate_setting('is_ssl', self.form_field.is_ssl.data, id)
        settings['method']       = self.populate_setting('method', self.form_field.method.data, id)
        settings['post_type']    = self.populate_setting('post_type', self.form_field.post_type.data, id)
        settings['require_auth'] = self.populate_setting('require_auth', self.form_field.require_auth.data, id)
        settings['auth_username'] = self.populate_setting('auth_username', self.form_field.auth_username.data, id)
        settings['auth_password'] = self.populate_setting('auth_password', self.form_field.auth_password.data, id)
        # Store custom key/value pairs as a list of dicts
        custom_list = []
        for entry in self.form_field.custom_values.data:
            # Each entry is a CustomValueForm with attributes custom_key and custom_value
            if entry and entry.get('custom_key'):
                custom_list.append({'custom_key': entry.get('custom_key'), 'custom_value': entry.get('custom_value')})
        settings['custom_values'] = self.populate_setting('custom_values', str(custom_list), id)
    def populate_from_settings(self, id):
        super().populate_from_settings(id)
        self.form_field.custom_url.data    = self.populate_from_setting(id, 'custom_url')
        self.form_field.custom_path.data   = self.populate_from_setting(id, 'custom_path')
        self.form_field.is_ssl.data        = self.populate_from_setting(id, 'is_ssl', default=False)
        self.form_field.method.data        = self.populate_from_setting(id, 'method', default=CUSTOM_METHOD_POST)
        self.form_field.post_type.data     = self.populate_from_setting(id, 'post_type', default=URLENCODE)
        self.form_field.require_auth.data  = self.populate_from_setting(id, 'require_auth', default=False)
        self.form_field.auth_username.data = self.populate_from_setting(id, 'auth_username')
        self.form_field.auth_password.data = self.populate_from_setting(id, 'auth_password')
        # Populate custom key/value pairs back into FieldList
        custom_str = self.populate_from_setting(id, 'custom_values')
        if custom_str:
            try:
                custom_list = ast.literal_eval(custom_str)
            except Exception:
                custom_list = []
            for pair in custom_list:
                CVForm = CustomValueForm()
                CVForm.custom_key.data = pair.get('custom_key')
                CVForm.custom_value.data = pair.get('custom_value')
                # Append to the FieldList
                self.form_field.custom_values.append_entry({'custom_key': CVForm.custom_key.data, 'custom_value': CVForm.custom_value.data})

# 8. UPNP Push Notification (special case: only one allowed, simplified form)
class UPNPPushNotificationInternalForm(Form):
    token = StringField('Token', [Length(max=255)], description='(Not used; leave blank)')
    def __init__(self, *args, **kwargs):
        kwargs['csrf_enabled'] = False
        super().__init__(*args, **kwargs)

class UPNPPushNotificationForm(Form):
    # This form does not extend EditNotificationForm, it defines its own fields (UPNP push is a special case).
    legend = ("<div style=\"font-size: 16px;\">"
              "UPNP Push subscriptions.<br/>"
              "Enable UPNP push notifications for local network clients.<br/>"
              "<strong><font color=\"red\">Warning:</font></strong> Enable only one UPNP notification at a time."
              "</div>")
    type = HiddenField()
    subscriptions = HiddenField()  # will carry the event subscription (probably fixed for UPNP)
    description = StringField('Description', [DataRequired(), Length(max=255)], description='Brief description of this notification')
    form_field = FormField(UPNPPushNotificationInternalForm)
    submit = SubmitField('Next')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/notifications'")
    def populate_setting(self, name, value, id=None):
        # Local helper (similar to EditNotificationForm)
        if id is not None:
            setting = NotificationSetting.query.filter_by(notification_id=id, name=name).first()
        else:
            setting = NotificationSetting(name=name)
        setting.value = value
        return setting
    def populate_from_setting(self, id, name, default=None):
        ret = default
        setting = NotificationSetting.query.filter_by(notification_id=id, name=name).first()
        if setting is not None:
            ret = setting.value
        return ret
    def populate_settings(self, settings, id=None):
        # Only one custom setting 'token' for UPNP (other common settings not used for UPNP push)
        settings['token'] = self.populate_setting('token', self.form_field.token.data, id)
    def populate_from_settings(self, id):
        self.form_field.token.data = self.populate_from_setting(id, 'token')
