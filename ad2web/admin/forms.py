from flask_wtf import FlaskForm as Form
from wtforms import HiddenField, SubmitField, RadioField, TextField, PasswordField
from wtforms.validators import (Required, Length, EqualTo, AnyOf)

from ..user import USER_ROLE, USER_STATUS, USER, ACTIVE
from ..utils import PASSWORD_LEN_MIN, PASSWORD_LEN_MAX

from ..widgets import ButtonField

class UserForm(Form):
    next = HiddenField()
    name = TextField('Username', [Required()])
    email = TextField('Email', [Required()])
    password = PasswordField('Password', [Required(), Length(PASSWORD_LEN_MIN, PASSWORD_LEN_MAX)])
    password_again = PasswordField('Confirm Password', [Required(), Length(PASSWORD_LEN_MIN, PASSWORD_LEN_MAX), EqualTo('password')])
    role_code = RadioField("Role", [AnyOf([str(val) for val in USER_ROLE.keys()])],
            choices=[(str(val), label) for val, label in USER_ROLE.items()], default=USER)
    status_code = RadioField("Status", [AnyOf([str(val) for val in USER_STATUS.keys()])],
            choices=[(str(val), label) for val, label in USER_STATUS.items()], default=ACTIVE)

    submit = SubmitField('Save')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/users'")
