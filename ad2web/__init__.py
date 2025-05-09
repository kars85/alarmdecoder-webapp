import os
from flask import Flask

from .config import DefaultConfig, TestConfig
from .utils.path_utils import make_dir, INSTANCE_FOLDER_PATH
from ad2web.decoder import create_decoder_socket
from .services.decoder_service import DecoderService
from ad2web.discovery import init_discovery, start_discovery

# Import Blueprints from views
from ad2web.views.auth_views import auth
from ad2web.views.user_views import users_bp
from ad2web.views.zone_views import zones_bp
from ad2web.views.admin_views import admin
from ad2web.views.settings_views import settings
from ad2web.views.log_views import log
from ad2web.views.notification_views import notification_bp
from ad2web.views.certificate_views import certificate
from ad2web.views.api_views import api_bp
from ad2web.views.keypad_views import keypad
from ad2web.views.setup_views import setup
from ad2web.views.camera_views import cameras_bp
from ad2web.views.updater_views import updater_bp


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

    # 5) Register blueprint for /views/ modules
    app.register_blueprint(auth)
    app.register_blueprint(users_bp)
    app.register_blueprint(zones_bp)
    app.register_blueprint(admin)
    app.register_blueprint(settings)
    app.register_blueprint(log)
    app.register_blueprint(notification_bp)
    app.register_blueprint(certificate)
    app.register_blueprint(api_bp)
    app.register_blueprint(keypad)
    app.register_blueprint(setup)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(updater_bp)
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
