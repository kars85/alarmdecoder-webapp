from sqlalchemy import Column
from sqlalchemy.orm.collections import attribute_mapped_collection
from datetime import datetime, timedelta
from ..extensions import db

class Notification(db.Model):
    __tablename__ = 'notifications'

    id = Column(db.Integer, primary_key=True, autoincrement=True)
    description = Column(db.String(255), nullable=False)
    type = Column(db.Integer, nullable=False)
    user_id = Column(db.Integer, db.ForeignKey('users.id'))
    enabled = Column(db.Integer, default=1)

    settings = db.relationship("NotificationSetting",
                                backref="notification",
                                collection_class=attribute_mapped_collection('name'),
                                cascade="all, delete-orphan")

    def get_setting(self, name, default=None):
        if name in list(self.settings.keys()):
            return self.settings[name].value

        return default

class NotificationSetting(db.Model):
    __tablename__ = 'notification_settings'

    id = Column(db.Integer, primary_key=True, autoincrement=True)
    name = Column(db.String(32), nullable=False)

    notification_id = Column(db.Integer, db.ForeignKey("notifications.id"))

    int_value = Column(db.Integer)
    string_value = Column(db.String(255))

    @staticmethod
    def check_time_restriction(start_time, end_time):
        """Return True if the current time is within the [start_time, end_time] range."""
        # Parse times (expects format "HH:MM:SS")
        st = start_time.split(':')
        et = end_time.split(':')
        message_time = datetime.now()
        start_dt = message_time.replace(hour=int(st[0]), minute=int(st[1]),
                                        second=int(st[2]), microsecond=0)
        end_dt = message_time.replace(hour=int(et[0]), minute=int(et[1]),
                                      second=int(et[2]), microsecond=0)
        # If the interval spans midnight, adjust date accordingly
        if end_dt.hour < start_dt.hour:
            if message_time.hour < end_dt.hour:
                start_dt -= timedelta(days=1)  # past midnight: start time is yesterday
            else:
                end_dt += timedelta(days=1)  # before midnight: end time is next day
        # Check if current time falls in [start_dt, end_dt]
        return start_dt <= message_time <= end_dt
    
    @property
    def value(self):
        for k in ('int_value', 'string_value'):
            v = getattr(self, k)
            if v is not None:
                return v
        else:
            return None

    @value.setter
    def value(self, value):
        if isinstance(value, int):
            self.int_value = value
            self.string_value = None
        else:
            self.string_value = str(value)
            self.int_value = None

class NotificationMessage(db.Model):
    __tablename__ = 'notification_messages'

    id = Column(db.Integer, primary_key=True)
    text = Column(db.Text, nullable=False)
