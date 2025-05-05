from flask_wtf import FlaskForm # Renamed import alias for clarity
# Import the specific validators you need
from wtforms import StringField, SubmitField, IntegerField
from wtforms.validators import DataRequired, Length, NumberRange # Import DataRequired

from ..widgets import ButtonField

class ZoneForm(FlaskForm): # Use FlaskForm alias
    # Use DataRequired for zone_id as 0 is invalid anyway due to NumberRange
    zone_id = IntegerField('Zone ID', [DataRequired(), NumberRange(min=1, max=65535)]) # Use min/max keywords
    # Use DataRequired for name to prevent empty strings
    name = StringField('Name', [DataRequired(), Length(max=32)])
    # Description is optional, so no required validator here
    description = StringField('Description', [Length(max=255)])

    submit = SubmitField('Save')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/zones'")