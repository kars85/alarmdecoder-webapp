# ad2web/__init__.py

from flask import Flask
from .views.user_views import users_bp    # Existing user blueprint
from .views.zone_views import zones_bp    # Existing zone blueprint
from ad2web.admin.views import admin_bp   # <-- Import your admin blueprint

def create_app():
    app = Flask(__name__)

    # Register blueprints
    app.register_blueprint(users_bp, url_prefix='/settings/users')
    app.register_blueprint(zones_bp, url_prefix='/settings/zones')
    app.register_blueprint(admin_bp, url_prefix='/settings/admin')  # <-- Add this line

    return app
