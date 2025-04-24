import flask.helpers
# Compatibility fix for Flask/Werkzeug versions regarding cached_property.
# Ensures locked_cached_property is available regardless of the specific Flask version.
try:
    from flask.helpers import locked_cached_property
except ImportError:
    from werkzeug.utils import cached_property as locked_cached_property
    flask.helpers.locked_cached_property = locked_cached_property
# Apply gevent monkey patching for asynchronous operations.
# This must happen very early, before most other modules are imported.
from gevent import monkey
monkey.patch_all()
# Import necessary Flask components and standard libraries.
import os
import signal

from flask import Flask, request, render_template, g, redirect, url_for
from flask_babel import Babel


# Import application-specific modules, blueprints, models, and utilities.
from .config import DefaultConfig
from .decoder import decodersocket, Decoder, create_decoder_socket
from .updater.views import updater
from .user import User, user
from .settings import settings
from .frontend import frontend
from .api import api, api_settings
from .admin import admin
from .certificate import certificate
from .log import log
from .keypad import keypad
from .notifications import notifications
from .zones import zones
from .settings.models import Setting
from .setup.constants import SETUP_COMPLETE, SETUP_STAGE_ENDPOINT, SETUP_ENDPOINT_STAGE
from .setup import setup
from .extensions import db, mail, login_manager, oid
from .utils import INSTANCE_FOLDER_PATH
from .cameras import cameras

# Define which symbols are exported when using 'from ad2web.app import *'.
__all__ = ['create_app']
# Define the default blueprints to be registered with the application.
DEFAULT_BLUEPRINTS = (
    frontend,
    user,
    settings,
    api,
    api_settings,
    admin,
    certificate,
    log,
    keypad,
    decodersocket,
    notifications,
    zones,
    setup,
    updater,
    cameras,
)

class ReverseProxied:
    """
    Middleware to handle requests coming from a reverse proxy (e.g., nginx).
    It corrects URL scheme, remote address, host, and script name based on
    standard 'X-Forwarded-*' headers, allowing the Flask app to generate correct
    URLs and identify the client IP even when behind a proxy.
    """
    def __init__(self, app, num_proxies=1):
        """
        Initializes the middleware.

        Args:
            app: The WSGI application to wrap.
            num_proxies: The number of proxies the request is expected to pass through.
                         Used to determine the real client IP from X-Forwarded-For.
        """
        self.app = app
        self.num_proxies = num_proxies

    def get_remote_addr(self, forwarded_for):
        """
        Selects the client's remote address from the X-Forwarded-For header list,
        considering the configured number of trusted proxies.
        """
        if len(forwarded_for) >= self.num_proxies:
            return forwarded_for[-1 * self.num_proxies]

    def __call__(self, environ, start_response):
        """
        Processes the incoming WSGI request environment, updating relevant keys
        based on X-Forwarded-* headers before passing the request to the wrapped app.
        """
        getter = environ.get
        forwarded_proto = getter('HTTP_X_FORWARDED_PROTO', '')
        forwarded_for = getter('HTTP_X_FORWARDED_FOR', '').split(',')
        forwarded_host = getter('HTTP_X_FORWARDED_HOST', '')

        environ.update({
            'werkzeug.proxy_fix.orig_wsgi_url_scheme': getter('wsgi.url_scheme'),
            'werkzeug.proxy_fix.orig_remote_addr': getter('REMOTE_ADDR'),
            'werkzeug.proxy_fix.orig_http_host': getter('HTTP_HOST')
        })

        forwarded_for = [x for x in [x.strip() for x in forwarded_for] if x]
        remote_addr = self.get_remote_addr(forwarded_for)

        if remote_addr is not None:
            environ['REMOTE_ADDR'] = remote_addr

        if forwarded_host:
            environ['HTTP_HOST'] = forwarded_host

        script_name = environ.get('HTTP_X_SCRIPT_NAME', '')
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ['PATH_INFO']
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = environ.get('HTTP_X_SCHEME', '')
        if scheme:
            environ['wsgi.url_scheme'] = scheme

        if forwarded_proto:
            environ['wsgi.url_scheme'] = forwarded_proto

        server = environ.get('HTTP_X_FORWARDED_SERVER', '')
        if server:
            environ['HTTP_HOST'] = server

        return self.app(environ, start_response)

def create_app(config=None, app_name=None, blueprints=None):
    """
    Application factory function. Creates and configures the main Flask application instance.

    Args:
        config: Optional configuration object to override defaults.
        app_name: Optional name for the Flask application. Defaults to project name.
        blueprints: Optional tuple of blueprints to register. Defaults to DEFAULT_BLUEPRINTS.

    Returns:
        A tuple containing the configured Flask app instance and the Socket.IO server instance.
    """
    if app_name is None:
        app_name = DefaultConfig.PROJECT
    if blueprints is None:
        blueprints = DEFAULT_BLUEPRINTS

    app = Flask(app_name, instance_path=INSTANCE_FOLDER_PATH, instance_relative_config=True)
    # Wrap the app with the ReverseProxied middleware.
    app.wsgi_app = ReverseProxied(app.wsgi_app)
    # Apply various configuration steps.
    configure_app(app, config)
    configure_hook(app)
    configure_blueprints(app, blueprints)
    configure_extensions(app)
    configure_logging(app)
    configure_template_filters(app)
    configure_error_handlers(app)

    # Create the Socket.IO server and the main Decoder instance
    appsocket = create_decoder_socket(app)
    decoder = Decoder(app, appsocket)
    # Store the decoder instance on the app object for global access.
    app.decoder = decoder

    return app, appsocket

def init_app(app, appsocket):
    """
    Performs initialization tasks after the Flask app and Socket.IO server are created.
    Sets up signal handling for graceful shutdown, checks database readiness,
    and starts the main AlarmDecoder service logic.

    Args:
        app: The Flask application instance.
        appsocket: The SocketIOServer instance.
    """
    def signal_handler(signal, frame):
        """Handles SIGINT (Ctrl+C) for graceful shutdown."""
        appsocket.stop()
        app.decoder.stop()
        os._exit(0)

    try:
        # Register the signal handler for graceful shutdown.
        signal.signal(signal.SIGINT, signal_handler)

        # Ensure the application context is active for database operations..
        with app.app_context():
            # Check if the essential 'settings' table exists before starting the decoder.
            if db.metadata.tables['settings'].exists(db.engine):
                app.decoder.init()
                app.decoder.start()
            else:
                # Log critical error and exit if the database isn't initialized.
                app.logger.error("Could not find 'settings' table in the database.  You may need to run 'python manage.py initdb'.")
                os._exit(0)

    except Exception:
        # Log any exceptions during the initialization process.
        app.logger.error("Error", exc_info=True)

def configure_app(app, config=None):
    """
    Loads application configuration from various sources.
    Priority: DefaultConfig -> production.cfg (instance folder) -> config object -> Environment variable (commented out).

    Args:
        app: The Flask application instance.
        config: An optional configuration object.
    """
    # http://flask.pocoo.org/docs/api/#configuration
    app.config.from_object(DefaultConfig)

    # http://flask.pocoo.org/docs/config/#instance-folders
    app.config.from_pyfile('production.cfg', silent=True)

    if config:
        app.config.from_object(config)

    # Use instance folder instead of env variables to make deployment easier.
    #app.config.from_envvar('%s_APP_CONFIG' % DefaultConfig.PROJECT.upper(), silent=True)


def configure_extensions(app):
    """
    Initializes Flask extensions with the application instance.

    Args:
        app: The Flask application instance.
    """
    # flask-sqlalchemy: Initialize database handling.
    db.init_app(app)

    # flask-mail: Initialize email sending capabilities
    mail.init_app(app)

    # flask-babel: Initialize internationalization and localization.
    babel = Babel(app)

    @babel.localeselector
    def get_locale():
        """Selects the best language based on request headers and configured languages."""
        accept_languages = app.config.get('ACCEPT_LANGUAGES')
        return request.accept_languages.best_match(accept_languages)

    # flask-login: Initialize user session management.
    login_manager.login_view = 'frontend.login'
    login_manager.refresh_view = 'frontend.reauth'

    @login_manager.user_loader
    def load_user(id):
        """Loads a user object from the database based on the user ID stored in the session."""
        return User.query.get(id)
    login_manager.setup_app(app)

    # flask-openid: Initialize OpenID authentication support.
    oid.init_app(app)


def configure_blueprints(app, blueprints):
    """
    Registers blueprints with the Flask application.

    Args:
        app: The Flask application instance.
        blueprints: An iterable of blueprint objects to register.
    """
    for blueprint in blueprints:
        app.register_blueprint(blueprint)


def configure_template_filters(app):
    """
    Registers custom Jinja2 template filters.

    Args:
        app: The Flask application instance.
    """

    # Note: The original pretty_date filter seems to call itself recursively.
    # This should likely call an actual date formatting utility.
    @app.template_filter()
    def pretty_date(value):
        # return pretty_date(value) # Original - likely incorrect
        # Example: return value.strftime("%b %d, %Y %I:%M %p") # Replace with desired format
        return pretty_date(value)

    @app.template_filter()
    def format_date(value, format='%Y-%m-%d'):
        """Formats a date object using the specified format string."""
        return value.strftime(format)


def configure_logging(app):
    """
    Configures application logging, including file rotation for info logs.
    Sets log level based on app.config['DEBUG'].

    Args:
        app: The Flask application instance.
    """
    import logging

    # Set info level on logger, which might be overwritten by handers.
    # Suppress DEBUG messages.
    if app.config['DEBUG']:
        app.logger.setLevel(logging.DEBUG)
    else:
        app.logger.setLevel(logging.INFO)
    # Configure a rotating file handler for general info logs
    info_log = os.path.join(app.config['LOG_FOLDER'], 'info.log')
    info_file_handler = logging.handlers.RotatingFileHandler(info_log, maxBytes=100000, backupCount=10)
    info_file_handler.setLevel(logging.INFO)
    info_file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s '
        '[in %(pathname)s:%(lineno)d]')
    )
    # Ensure Socket.IO logs also go to the main info log file.
    socketio_logger = logging.getLogger('socketio.virtsocket')
    socketio_logger.addHandler(info_file_handler)
    # Add the handler to the main Flask app logger.
    app.logger.addHandler(info_file_handler)


def configure_hook(app):
    """
    Configures request hooks (before_request and after_request).

    Args:
        app: The Flask application instance.
    """
    # List of blueprints that are accessible even if setup is not complete.
    safe_blueprints = ['setup', 'sock', None]   # None = static content.

    @app.before_request
    def before_request():
        """
        Executed before each request.
        - Adjusts session cookie security based on X-Forwarded-Proto (HTTPS).
        - Enforces setup process completion by redirecting users if setup is not finished.
        - Makes the global AlarmDecoder instance available via `g.alarmdecoder`.
        """
        # Dynamically set cookie security based on detected scheme (HTTPS).
        x_forwarded_proto = request.headers.get('X-Forwarded-Proto', None)
        if x_forwarded_proto is not None and x_forwarded_proto == 'https':
            app.config['SESSION_COOKIE_SECURE'] = True
            app.config['REMEMBER_COOKIE_SECURE'] = True
        else:
            app.config['SESSION_COOKIE_SECURE'] = False
            app.config['REMEMBER_COOKIE_SECURE'] = False
        # Enforce setup completion for non-safe blueprints.
        if request.blueprint == 'setup':
            # Handle navigation within the setup process itself.
            setup_stage = Setting.get_by_name('setup_stage').value
            # If setup hasn't been started, redirect to the index
            if setup_stage is None:
                if request.endpoint != 'setup.index' and request.endpoint != 'setup.type':
                    return redirect(url_for('setup.index'))

            # Redirect users accessing other parts of the app if setup isn't complete.
            elif SETUP_ENDPOINT_STAGE[request.endpoint] > setup_stage:
                return redirect(url_for(SETUP_STAGE_ENDPOINT[setup_stage]))

        elif request.blueprint not in safe_blueprints:
            setup_stage = Setting.get_by_name('setup_stage').value
            # If setup hasn't been started, force them to the index.
            if setup_stage is None:
                return redirect(url_for('setup.index'))

            # And if it has, place them at the last-known stage.
            elif setup_stage != SETUP_COMPLETE:
                stage_page = SETUP_STAGE_ENDPOINT[setup_stage]

                return redirect(url_for(stage_page))
        # Make the AlarmDecoder instance accessible in the request context.
        g.alarmdecoder = app.decoder

    @app.after_request
    def apply_response_headers(response):
        """
        Executed after each request before sending the response.
        - Adds security-related headers (X-Frame-Options, Cache-Control, etc.).
        """
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

def configure_error_handlers(app):
    """
    Configures custom error handlers for common HTTP status codes.

    Args:
        app: The Flask application instance.
    """
    @app.errorhandler(403)
    def forbidden_page(error):
        """Renders a custom page for 403 Forbidden errors."""
        return render_template("errors/forbidden_page.html"), 403

    @app.errorhandler(404)
    def page_not_found(error):
        """Renders a custom page for 404 Not Found errors."""
        return render_template("errors/page_not_found.html"), 404

    @app.errorhandler(500)
    def server_error_page(error):
        """Renders a custom page for 500 Internal Server errors."""
        return render_template("errors/server_error.html"), 500
