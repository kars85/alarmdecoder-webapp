import os

from wtforms.validators import ValidationError

class PathExists:
    def __init__(self, message=None):
        if not message:
            message = 'Path does not exist.'

        self.message = message

    def __call__(self, form, field):
        f = field.data

        if not os.path.exists(f):
            raise ValidationError(self.message)

class Hex:
    def __init__(self, message=None):
        if not message:
            message = 'Number must be hexadecimal.'

        self.message = message

    def __call__(self, form, field):
        try:
            num = int(field.data, 16)
        except:
            raise ValidationError(self.message)