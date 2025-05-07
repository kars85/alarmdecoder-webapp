# ad2web/app.py  (or ad2web/__init__.py if that’s where create_app lives)

from flask import Flask
# … your other imports …
from ad2web.views.user_views import users_bp
from ad2web.views.zone_views import zones_bp
from ad2web.admin.views import admin_bp
# Import the SocketIO “decoder” server factory (unchanged)
from ad2web.decoder import create_decoder_socket
# Import your new service
from .services.decoder_service import DecoderService
from ad2web.discovery import init_discovery, start_discovery
from .config import DefaultConfig, TestConfig  # etc.
from .utils.path_utils import make_dir, INSTANCE_FOLDER_PATH
from flask import Flask
from .config import DefaultConfig
from .utils.path_utils import make_dir, INSTANCE_FOLDER_PATH
import os
# Import your blueprints, services, etc.
from ad2web.views.user_views import users_bp
from ad2web.views.zone_views import zones_bp
from ad2web.admin.views import admin_bp
from ad2web.decoder import create_decoder_socket
from .services.decoder_service import DecoderService
from ad2web.discovery import init_discovery, start_discovery

def create_app(config_object=None):
    # 1) Create the Flask app, pointing instance_relative_config at INSTANCE_FOLDER_PATH
    app = Flask(__name__,
                instance_path = os.path.abspath(INSTANCE_FOLDER_PATH),
                instance_relative_config = True)

    # 2) Load configuration (testing or default)
    if config_object:
        app.config.from_object(config_object)
    else:
        app.config.from_object(DefaultConfig)

    # 3) Ensure all instance‐type folders exist
    make_dir(app.instance_path)                  # Flask’s instance folder
    make_dir(INSTANCE_FOLDER_PATH)               # your global instance folder
    make_dir(app.config['LOG_FOLDER'])           # logs subfolder
    make_dir(app.config['UPLOAD_FOLDER'])        # uploads subfolder
    if app.config.get('OPENID_FS_STORE_PATH'):
        make_dir(app.config['OPENID_FS_STORE_PATH'])  # OpenID store

    # 4) Initialize extensions, login manager, etc. (if you have them)
    #    e.g. db.init_app(app), mail.init_app(app), etc.

    # 5) Register blueprints
    app.register_blueprint(users_bp, url_prefix='/settings/users')
    app.register_blueprint(zones_bp, url_prefix='/settings/zones')
    app.register_blueprint(admin_bp, url_prefix='/settings/admin')
    # …and any others…

    # 6) Set up your DecoderService
    socketio_server = create_decoder_socket(app)
    decoder = DecoderService(app, socketio_server)
    app.decoder = decoder
    decoder.init()
    decoder.start()

    # 7) Start SSDP discovery (if you need it)
    init_discovery(app)
    start_discovery()

    return app



def init_app():
    return None