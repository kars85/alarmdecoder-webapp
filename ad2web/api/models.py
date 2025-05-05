# ad2web/api/models.py
import os, base64  # added imports for key generation
from sqlalchemy import Column
from ad2web.extensions import db


class APIKey(db.Model):
    __tablename__ = 'apikeys'
    id = Column(db.Integer, primary_key=True)
    user_id = Column(db.Integer, db.ForeignKey('users.id'))
    key = Column(db.String(64))
    user = db.relationship("User", backref="apikey")

    @staticmethod
    def generate_api_key():
        """Generate a new random API key token."""
        return base64.b32encode(os.urandom(7)).rstrip('==')
