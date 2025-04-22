# -*- coding: utf-8 -*-

from __future__ import absolute_import
from flask_sqlalchemy import SQLAlchemy
db = SQLAlchemy()

from flask_mail import Mail
mail = Mail()

from flask_login import LoginManager
login_manager = LoginManager()

from flask_openid import OpenID
oid = OpenID()
