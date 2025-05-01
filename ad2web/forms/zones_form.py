from flask_wtf import FlaskForm as Form
from wtforms import IntegerField, StringField
from wtforms.validators import InputRequired, Length, NumberRange

class ZoneForm(Form):
    zone_id = IntegerField('Zone ID', [InputRequired(), NumberRange(1, 65535)])
    name = StringField('Name', [InputRequired(), Length(max=32)])
    description = StringField('Description', [Length(max=255)])
