from flask_wtf import FlaskForm
from wtforms import TextField, PasswordField, RadioField, HiddenField
from wtforms.validators import DataRequired, Length, EqualTo, AnyOf

from .constants import USER_ROLE, USER_STATUS, USER, ACTIVE

class UserForm(FlaskForm):
    """Form for creating and editing users (admin use)."""
    next = HiddenField()
    name = TextField('Username', validators=[DataRequired()])
    email = TextField('Email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=6, max=64),  # using PASSWORD_LEN_MIN=6, PASSWORD_LEN_MAX=64 as per utils
        EqualTo('password_again', message='Passwords must match')
    ])
    password_again = PasswordField('Confirm Password', validators=[DataRequired()])
    # Use integer values for role and status (coerce=int for proper type conversion)
    role_code = RadioField(
        'Role', choices=[(val, label) for val, label in USER_ROLE.items()],
        default=USER, coerce=int,
        validators=[DataRequired(), AnyOf([val for val in USER_ROLE.keys()])]
    )
    status_code = RadioField(
        'Status', choices=[(val, label) for val, label in USER_STATUS.items()],
        default=ACTIVE, coerce=int,
        validators=[DataRequired(), AnyOf([val for val in USER_STATUS.keys()])]
    )
