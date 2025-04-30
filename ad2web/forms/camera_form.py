from flask_wtf import FlaskForm as Form
from wtforms import TextField, SubmitField
from wtforms.validators import Required, Length
from ad2web.widgets import ButtonField

class CameraForm(Form):
    name = TextField('Name', [Required(), Length(max=32)])
    get_jpg_url = TextField('Snapshot URL', [Length(max=255)])
    username = TextField('Auth Username', [Length(max=32)])
    password = TextField('Auth Password', [Length(max=255)])
    submit = SubmitField('Save')
    cancel = ButtonField('Cancel', onclick="location.href='/cameras/camera_list'")
