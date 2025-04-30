from flask_wtf import FlaskForm as Form
from wtforms import TextField, HiddenField, SubmitField
from wtforms.validators import Required, Length

class GenerateCertificateForm(Form):
    next = HiddenField()
    name = TextField('Name', [Required(), Length(max=32)])
    description = TextField('Description', [Length(max=255)])
    submit = SubmitField('Generate')
