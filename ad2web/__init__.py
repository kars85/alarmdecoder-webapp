# ad2web/app.py  (or ad2web/__init__.py if that’s where create_app lives)

from flask import Flask
# … your other imports …
from ad2web.views.user_views import users_bp
from ad2web.views.zone_views import zones_bp
from ad2web.admin.views import admin_bp
# Import the SocketIO “decoder” server factory (unchanged)
from ad2web.decoder import create_decoder_socket
# Import your new service
from services.decoder_service import DecoderService
from ad2web.discovery import init_discovery, start_discovery

def create_app():
    app = Flask(__name__)
    # … load config, init extensions, login manager, etc. …

    # Register your blueprints
    app.register_blueprint(users_bp, url_prefix='/settings/users')
    app.register_blueprint(zones_bp, url_prefix='/settings/zones')
    app.register_blueprint(admin_bp, url_prefix='/settings/admin')
    # … any others …

    # ———  NEW: DecoderService initialization ———
    # Create the SocketIO server that will carry panel events to clients
    socketio_server = create_decoder_socket(app)
    # Instantiate and attach the service
    decoder = DecoderService(app, socketio_server)
    app.decoder = decoder
    # Perform one-time setup (load settings, init sub-services)
    decoder.init()
    # Start all background threads (device monitor, notifications, backups, UPNP, discovery, etc.)
    decoder.start()
    # ————————————————————————————————
    # Initialize and start SSDP discovery
    init_discovery(app)
    start_discovery()

    return app
