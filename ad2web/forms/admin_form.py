from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, RadioField, HiddenField
from wtforms.validators import DataRequired, Length, EqualTo, AnyOf

from ad2web.user.constants import USER_ROLE, USER_STATUS, USER, ACTIVE

class UserForm(FlaskForm):
    """Form for creating and editing users (admin interface)."""
    next = HiddenField()  # for redirecting after form submission, if needed
    name = StringField('Username', validators=[DataRequired()])
    email = StringField('Email', validators=[DataRequired()])
    password = PasswordField('Password', validators=[
        DataRequired(),
        Length(min=6, max=64),
        EqualTo('password_again', message='Passwords must match')
    ])
    password_again = PasswordField('Confirm Password', validators=[DataRequired()])
    role_code = RadioField(
        'Role',
        choices=[(val, label) for val, label in USER_ROLE.items()],
        default=USER, coerce=int,
        validators=[DataRequired(), AnyOf([val for val in USER_ROLE.keys()])]
    )
    status_code = RadioField(
        'Status',
        choices=[(val, label) for val, label in USER_STATUS.items()],
        default=ACTIVE, coerce=int,
        validators=[DataRequired(), AnyOf([val for val in USER_STATUS.keys()])]
    )

    def __init__(self, edit=False, *args, **kwargs):
        """If edit=True, make password fields optional (not required when editing an existing user)."""
        super().__init__(*args, **kwargs)
        if edit:
            # Remove DataRequired from password fields to allow leaving them blank
            self.password.validators = [v for v in self.password.validators if not isinstance(v, DataRequired)]
            self.password_again.validators = [v for v in self.password_again.validators if not isinstance(v, DataRequired)]
