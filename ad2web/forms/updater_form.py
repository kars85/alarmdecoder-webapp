from flask_wtf import FlaskForm as Form
from wtforms import SubmitField, FileField, SelectField
from wtforms.validators import DataRequired
from ad2web.widgets import ButtonField

class UpdateFirmwareForm(Form):
    firmware_file = FileField('Firmware File', validators=[DataRequired()])
    submit = SubmitField('Upload')
    cancel = ButtonField('Cancel', onclick="location.href='/settings'")

class UpdateFirmwareJSONForm(Form):
    firmware_file_json = SelectField('Firmware File', coerce=str)
    json_submit = SubmitField('Upload')
    cancel = ButtonField('Cancel', onclick="location.href='/settings'")
