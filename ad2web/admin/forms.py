# ad2web/admin/forms.py

from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, PasswordField, RadioField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, AnyOf

from ..user.constants import USER_ROLE, USER_STATUS, USER
from ..user.constants import ACTIVE
from ..user.constants import NEW  # if you allow setting 'new' status
from ..validators import PASSWORD_LEN_MIN, PASSWORD_LEN_MAX

class UserForm(FlaskForm):
    """Form for creating or editing a user (admin)."""
    user_id = HiddenField()  # used for edit vs create
    name = StringField(
        'Username',
        validators=[DataRequired(), Length(min=3, max=50)]
    )
    email = StringField(
        'Email',
        validators=[DataRequired(), Email(), Length(max=120)]
    )
    password = PasswordField(
        'Password',
        validators=[Length(min=PASSWORD_LEN_MIN, max=PASSWORD_LEN_MAX)]
    )
    password_again = PasswordField(
        'Confirm Password',
        validators=[EqualTo('password', message='Passwords must match')]
    )
    role_code = RadioField(
        'Role',
        validators=[DataRequired(), AnyOf([str(val) for val in USER_ROLE.keys()])],
        choices=[(str(val), label) for val, label in USER_ROLE.items()],
        default=str(USER)
    )
    status_code = RadioField(
        'Status',
        validators=[DataRequired(), AnyOf([str(val) for val in USER_STATUS.keys()])],
        choices=[(str(val), label) for val, label in USER_STATUS.items()],
        default=str(ACTIVE)
    )
    submit = SubmitField('Save')
