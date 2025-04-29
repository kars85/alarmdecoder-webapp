from flask import Flask
#from .app import create_app, init_app
from .views.user_views import users_bp  # Import the user blueprint
from .views.zone_views import zones_bp  # Import the zone blueprint


def create_app():
    app = Flask(__name__)  # Create the Flask app instance
    #init_app(app)  # Call any app initialization if needed

    # Register blueprints
    app.register_blueprint(users_bp, url_prefix='/settings/users')  # Register user blueprint
    app.register_blueprint(zones_bp, url_prefix='/settings/zones')  # Register zone blueprint

    return app
