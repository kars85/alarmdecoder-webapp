from flask_wtf import FlaskForm as Form
from wtforms import HiddenField
from wtforms.validators import InputRequired

class APIKeyForm(Form):
    """Form for API key management actions (generate or disable)."""
    user_id = HiddenField('User ID', validators=[InputRequired()])
