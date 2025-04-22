from flask_wtf import FlaskForm as Form
from wtforms import (TextField, SubmitField, IntegerField)
from wtforms.validators import (Required, Length, NumberRange)


from ..widgets import ButtonField

class ZoneForm(Form):
    zone_id = IntegerField('Zone ID', [Required(), NumberRange(1, 65535)])
    name = TextField('Name', [Required(), Length(max=32)])
    description = TextField('Description', [Length(max=255)])

    submit = SubmitField('Save')
    cancel = ButtonField('Cancel', onclick="location.href='/settings/zones'")
