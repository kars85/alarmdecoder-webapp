from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, IntegerField, RadioField, FileField, SelectField, BooleanField, FormField, HiddenField
from wtforms.validators import InputRequired, Length, EqualTo, Email, NumberRange, Optional, NoneOf, ValidationError
from ..validators import PathExists, Hex
from ..widgets import ButtonField, MultiCheckboxField
from alarmdecoder.panels import ADEMCO, DSC
import re

class StrongPassword:
    """Validator to enforce strong password requirements."""
    def __init__(self, min_length=8):
        self.min_length = min_length
    def __call__(self, form, field):
        pwd = str(field.data or "")
        if len(pwd) < self.min_length:
            raise ValidationError(f"Password must be at least {self.min_length} characters long.")
        if not re.search(r"[A-Z]", pwd) or not re.search(r"[a-z]", pwd) or not re.search(r"\d", pwd):
            raise ValidationError("Password must include at least one uppercase letter, one lowercase letter, and one number.")

class SetupButtonForm(FlaskForm):
    previous = ButtonField('Previous', onclick='history.go(-1);')
    next = SubmitField('Next')

class DeviceTypeForm(FlaskForm):
    device_type = SelectField('Device Type', choices=[('AD2USB', 'AD2USB'), ('AD2PI', 'AD2PI'), ('AD2SERIAL', 'AD2SERIAL')], default='AD2USB')
    device_location = SelectField('Device Location', choices=[('local', 'Local Device'), ('network', 'Network Device')], default='local')
    buttons = FormField(SetupButtonForm)

class NetworkDeviceForm(FlaskForm):
    device_address = StringField('Address', validators=[InputRequired(), Length(max=255)], description='Hostname or IP address', default='localhost')
    device_port = IntegerField('Port', validators=[InputRequired(), NumberRange(min=1024, max=65535)], default=10000)
    ssl = BooleanField('Connect to encrypted ser2sock? (Experimental)')
    buttons = FormField(SetupButtonForm)

class SSLForm(FlaskForm):
    ca_cert = FileField('CA Certificate', validators=[InputRequired()], description='CA certificate for AlarmDecoder.')
    cert = FileField('Client Certificate', validators=[InputRequired()], description='Client certificate for this webapp.')
    key = FileField('Client Key', validators=[InputRequired()], description='Private key for the client certificate.')
    buttons = FormField(SetupButtonForm)

class SSLHostForm(FlaskForm):
    config_path = StringField('SER2SOCK Config Path', validators=[InputRequired(), PathExists()], default='/etc/ser2sock')
    device_address = StringField('Address', validators=[InputRequired(), Length(max=255)], description='Hostname or IP address', default='localhost')
    device_port = IntegerField('Port', validators=[InputRequired(), NumberRange(min=1024, max=65535), NoneOf([80, 443, 5000], message="Port is reserved by other services.")], default=10000)
    ssl = BooleanField('Encrypt ser2sock?')
    buttons = FormField(SetupButtonForm)

class LocalDeviceForm(FlaskForm):
    device_path = StringField('Device Path', validators=[InputRequired(), Length(max=255), PathExists()], description='Path to the AlarmDecoder device.', default='/dev/serial0')
    baudrate = SelectField('Baudrate', choices=[(115200, '115200'), (19200, '19200')], default=115200, coerce=int)
    confirm_management = BooleanField('Share AlarmDecoder on network?', description='Serve AlarmDecoder on your network (via ser2sock) for other software to use.', default=True)
    buttons = FormField(SetupButtonForm)

class LocalDeviceFormUSB(FlaskForm):
    device_path = SelectField('Device Path', choices=[('/dev/ttyUSB0', '/dev/ttyUSB0')], default='/dev/ttyUSB0', coerce=str)
    baudrate = SelectField('Baudrate', choices=[(115200, '115200'), (19200, '19200')], default=115200, coerce=int)
    confirm_management = BooleanField('Share AlarmDecoder on network?', description='Serve AlarmDecoder on your network (via ser2sock) for other software to use.', default=True)
    buttons = FormField(SetupButtonForm)

class TestDeviceForm(FlaskForm):
    previous = ButtonField('Previous', onclick='history.go(-1);')
    next = SubmitField('Next')

class DeviceForm(FlaskForm):
    panel_mode = RadioField('Panel Type', choices=[(ADEMCO, 'Honeywell/Ademco'), (DSC, 'DSC')], default=ADEMCO, coerce=int)
    keypad_address = IntegerField('Keypad Address', validators=[InputRequired(), NumberRange(min=1, max=99)], default=18)
    address_mask = StringField('AlarmDecoder Address Mask', validators=[InputRequired(), Length(max=8), Hex()], default='FFFFFFFF')
    internal_address_mask = StringField('Webapp Address Mask', validators=[InputRequired(), Length(max=8), Hex()], default='FFFFFFFF')
    zone_expanders = MultiCheckboxField('Zone Expanders', choices=[('1','Emulate zone expander #1'),('2','Emulate zone expander #2'),('3','Emulate zone expander #3'),('4','Emulate zone expander #4'),('5','Emulate zone expander #5')])
    relay_expanders = MultiCheckboxField('Relay Expanders', choices=[('1','Emulate relay expander #1'),('2','Emulate relay expander #2'),('3','Emulate relay expander #3'),('4','Emulate relay expander #4')])
    lrr_enabled = BooleanField('Emulate Long Range Radio?')
    deduplicate = BooleanField('Deduplicate messages?')
    buttons = FormField(SetupButtonForm)

class CreateAccountForm(FlaskForm):
    name = StringField('Username', validators=[InputRequired(), Length(max=64)], default='admin')
    email = StringField('Email Address', validators=[InputRequired(), Length(max=64), Email()], default='admin@example.com')
    password = PasswordField('Password', validators=[InputRequired(), Length(min=8, max=64), StrongPassword()])
    password_again = PasswordField('Confirm Password', validators=[InputRequired(), EqualTo('password', message="Passwords must match.")])
    submit = SubmitField('Create Admin Account')

class EmailSetupForm(FlaskForm):
    mail_server = StringField('SMTP Server', validators=[InputRequired(), Length(max=255)], default='localhost')
    mail_port = IntegerField('SMTP Port', validators=[InputRequired(), NumberRange(min=1, max=65535)], default=25)
    use_tls = BooleanField('Use TLS?', default=False)
    use_auth = BooleanField('Requires Authentication?', default=False)
    username = StringField('SMTP Username', validators=[Optional(), Length(max=255)])
    password = PasswordField('SMTP Password', validators=[Optional(), Length(max=255)])
    default_sender = StringField('Default Sender Address', validators=[InputRequired(), Length(max=255)], default='alarmdecoder@example.com', description='From address for outgoing emails')
    buttons = FormField(SetupButtonForm)
