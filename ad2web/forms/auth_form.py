# ad2web/forms/auth_form.py
from flask_wtf import FlaskForm
from markupsafe import Markup
from wtforms import StringField, PasswordField, BooleanField, HiddenField, SubmitField
from wtforms.validators import InputRequired, Length, EqualTo, Email, ValidationError

from ..user.models import User
from flask_login import current_user
from ..utils.constants import USERNAME_LEN_MIN, USERNAME_LEN_MAX, PASSWORD_LEN_MIN, PASSWORD_LEN_MAX

class LoginForm(FlaskForm):
    next = HiddenField()
    login = StringField('Username or Email', validators=[InputRequired()])
    password = PasswordField('Password', validators=[InputRequired(), Length(min=PASSWORD_LEN_MIN, max=PASSWORD_LEN_MAX)])
    remember = BooleanField('Remember me')
    submit = SubmitField('Sign in')

class SignupForm(FlaskForm):
    next = HiddenField()
    name = StringField('Username', validators=[InputRequired(), Length(min=USERNAME_LEN_MIN, max=USERNAME_LEN_MAX)],
                       description="Choose a unique username.")
    email = StringField('Email', validators=[InputRequired(), Email()],
                        description="What's your email address?")
    password = PasswordField('Password', validators=[InputRequired(), Length(min=PASSWORD_LEN_MIN, max=PASSWORD_LEN_MAX)],
                             description=f"{PASSWORD_LEN_MIN} characters or more! Be tricky.")
    agree = BooleanField(Markup('I agree to the <a target="_blank" rel="noopener noreferrer" href="/terms">Terms of Service</a>'),
                         validators=[InputRequired()])
    submit = SubmitField('Sign up')

    def validate_name(self, field):
        if User.query.filter_by(name=field.data).first():
            raise ValidationError('This username is taken.')

    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('This email is already registered.')

class ForgotPasswordForm(FlaskForm):
    email = StringField('Your Email', validators=[InputRequired(), Email()])
    submit = SubmitField('Send instructions')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[InputRequired(), Length(min=PASSWORD_LEN_MIN, max=PASSWORD_LEN_MAX)])
    confirm_password = PasswordField('Confirm Password', validators=[InputRequired(), EqualTo('password', message="Passwords must match.")])
    submit = SubmitField('Reset Password')

class ReauthForm(FlaskForm):
    next = HiddenField()
    password = PasswordField('Password', validators=[InputRequired(), Length(min=PASSWORD_LEN_MIN, max=PASSWORD_LEN_MAX)])
    submit = SubmitField('Reauthenticate')

class ProfileEditForm(FlaskForm):
    next = HiddenField()
    name = StringField('Username', validators=[InputRequired(), Length(min=USERNAME_LEN_MIN, max=USERNAME_LEN_MAX)])
    email = StringField('Email', validators=[InputRequired(), Email()])
    submit = SubmitField('Save Changes')

    def validate_name(self, field):
        # Only check if changing to a new username
        if current_user.is_authenticated and field.data != current_user.name:
            if User.query.filter_by(name=field.data).first():
                raise ValidationError('This username is already taken.')

    def validate_email(self, field):
        if current_user.is_authenticated and field.data != current_user.email:
            if User.query.filter_by(email=field.data).first():
                raise ValidationError('This email is already in use.')

class ChangePasswordForm(FlaskForm):
    next = HiddenField()
    current_password = PasswordField('Current Password', validators=[InputRequired()])
    new_password = PasswordField('New Password', validators=[InputRequired(), Length(min=PASSWORD_LEN_MIN, max=PASSWORD_LEN_MAX)])
    confirm_password = PasswordField('Confirm New Password', validators=[InputRequired(), EqualTo('new_password', message="Passwords must match.")])
    submit = SubmitField('Change Password')

    def validate_current_password(self, field):
        if not current_user.is_authenticated or not current_user.check_password(field.data):
            raise ValidationError('Current password is incorrect.')
