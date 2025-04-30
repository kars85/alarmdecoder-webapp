from sqlalchemy import Column
from ..extensions import db

class Setting(db.Model):
    __tablename__ = 'settings'
    id = Column(db.Integer, primary_key=True, autoincrement=True)
    name = Column(db.String(32), unique=True, nullable=False)
    int_value = Column(db.Integer)
    string_value = Column(db.String(255))
    @classmethod
    def get_by_name(cls, name, default=None):
        setting = cls.query.filter_by(name=name).first()
        if not setting:
            setting = Setting(name=name)
            if default is not None:
                setting.value = default
        return setting
    @property
    def value(self):
        if self.int_value is not None:
            return self.int_value
        if self.string_value is not None:
            return self.string_value
        return None
    @value.setter
    def value(self, value):
        if isinstance(value, int):
            self.int_value = value
            self.string_value = None
        else:
            self.string_value = str(value) if value is not None else None
            self.int_value = None
    @classmethod
    def get_value(cls, name, default=None, type_func=None):
        setting = cls.get_by_name(name, default=default)
        val = setting.value
        if type_func:
            try:
                return type_func(val)
            except Exception:
                return default
        return val
    @classmethod
    def set_value(cls, name, value):
        setting = cls.query.filter_by(name=name).first()
        if not setting:
            setting = Setting(name=name)
        setting.value = value
        db.session.add(setting)
        return setting
    def __eq__(self, other):
        other_val = other.value if isinstance(other, Setting) else other
        return self.value == other_val
    def __ne__(self, other):
        return not self.__eq__(other)
