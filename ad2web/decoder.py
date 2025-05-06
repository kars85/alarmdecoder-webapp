# ad2web/decoder.py
#
# Core module responsible for managing the AlarmDecoder device connection,
# handling events, managing background threads (device polling, updates, cameras, exports),
# and facilitating communication with clients via Socket.IO.
import threading
import os
import sys
import time
import datetime
import binascii

try:
    import miniupnpc
    has_upnp = True
except ImportError:
    has_upnp = False

import socketio

from gevent import pywsgi

from sqlalchemy.orm.exc import NoResultFound
from flask import Blueprint, Response, request, g, current_app
import jsonpickle

from OpenSSL import SSL
from alarmdecoder import AlarmDecoder
from alarmdecoder.devices import SocketDevice, SerialDevice
from alarmdecoder.util import NoDeviceError, CommError

from .extensions import db, mail
from .notifications import NotificationSystem, NotificationThread
from .settings.models import Setting
from .certificate.models import Certificate
from .updater import Updater
from .updater.models import FirmwareUpdater

from .notifications.models import NotificationMessage
from .notifications.constants import (
    ARM, DISARM, POWER_CHANGED, ALARM, ALARM_RESTORED,
    FIRE, BYPASS, BOOT, LRR, CONFIG_RECEIVED, ZONE_FAULT,
    ZONE_RESTORE, LOW_BATTERY, PANIC,
    READY, CHIME, DEFAULT_EVENT_MESSAGES, EVMSG_VERSION,
    RFX, EXP, AUI
)

from .cameras import CameraSystem
from .services.discovery_service import DiscoveryService
from .upnp import UPNPThread
from .setup.constants import SETUP_COMPLETE
from .utils.path_utils import INSTANCE_FOLDER_PATH
from .utils.user_utils import user_is_authenticated
from .mailer import Mailer
from .exporter import Exporter

# Mapping of internal event type constants to the corresponding signal names
# used by the python-alarmdecoder library. This allows dynamic binding
# of event handlers.
EVENT_MAP = {
    ARM: 'on_arm',
    DISARM: 'on_disarm',
    POWER_CHANGED: 'on_power_changed',
    ALARM: 'on_alarm',
    ALARM_RESTORED: 'on_alarm_restored',
    FIRE: 'on_fire',
    BYPASS: 'on_bypass',
    BOOT: 'on_boot',
    LRR: 'on_lrr_message',
    READY: 'on_ready_changed',
    CHIME: 'on_chime_changed',
    CONFIG_RECEIVED: 'on_config_received',
    ZONE_FAULT: 'on_zone_fault',
    ZONE_RESTORE: 'on_zone_restore',
    LOW_BATTERY: 'on_low_battery',
    PANIC: 'on_panic',
    RFX: 'on_rfx_message',
    EXP: 'on_expander_message',
    AUI: 'on_aui_message'
}

# Flask Blueprint for handling Socket.IO routes under the /socket.io/ prefix.
decodersocket = Blueprint('sock', __name__, url_prefix='/socket.io')


# 1) Create the Socket.IO server and WSGI wrapper at module load
sio = socketio.Server(async_mode='gevent', cors_allowed_origins="*")
# NOTE: you can pass additional options here (e.g. ping_interval, logger, etc.)

def create_decoder_socket(app):
    """
    Create and return a Gevent WSGI server that speaks both HTTP (Flask)
    and Socket.IO on /socket.io/.
    """
    # Wrap the Flask app so socket.io routes are intercepted
    wsgi_app = socketio.WSGIApp(sio, app)
    host = app.config.get('HOST', '0.0.0.0')
    port = app.config.get('PORT', 5000)
    server = pywsgi.WSGIServer((host, port), wsgi_app)
    # 2) Register our custom namespace:
    sio.register_namespace(DecoderNamespace('/alarmdecoder'))
    return server


class Decoder:
    """
    Main application class managing the AlarmDecoder device, state, background threads,
    and communication. Acts as a central hub for the web application's backend logic
    related to the alarm system.
    """

    def __init__(self, app, websocket):
        """
        Initializes the Decoder instance.

        Args:
            app: The Flask application object.
            websocket: The SocketIOServer object for WebSocket communication.
        """
        with app.app_context():
            self.app = app
            self.websocket = websocket
            self.device = None
            self.updater = Updater()
            self.updates = {}
            self.version = ''
            self.firmware_file = None
            self.firmware_length = -1

            self.trigger_reopen_device = False
            self.trigger_restart = False

            self._last_message = None
            self._device_baudrate = 115200
            self._device_type = None
            self._device_location = None
            self._event_thread = DecoderThread(self)
            self._discovery_thread = None
            self._notification_thread = None
            self._notifier_system = None
            self._upnp_thread = None
            self._internal_address_mask = 0xFFFFFFFF
            self.last_message_received = None

    @property
    def internal_address_mask(self):
        return self._internal_address_mask

    @internal_address_mask.setter
    def internal_address_mask(self, mask):
        """Gets the internal address mask used by the AlarmDecoder."""
        return self._internal_address_mask

    @internal_address_mask.setter
    def internal_address_mask(self, mask):
        """
        Sets the internal address mask and applies it to the active device, if any.

        Args:
            mask (str): The address mask as a hexadecimal string (e.g., "FFFFFFFF").
        """
        self._internal_address_mask = int(mask, 16)
        if self.device is not None:
            self.device.internal_address_mask = int(mask, 16)

    def start(self):
        """
        Starts all background worker threads associated with the Decoder.
        (Event handling, version checking, camera polling, discovery, notifications, exports, UPNP).
        """
        self._event_thread.start()
        self._version_thread.start()
        self._camera_thread.start()
        self._discovery_thread.start()
        self._notification_thread.start()
        self._exporter_thread.start()
        if has_upnp:
            self._upnp_thread.start()

    def stop(self, restart=False):
        """
        Gracefully stops the Decoder service.

        Closes the device connection, stops all background threads, and shuts down
        the WebSocket server. Optionally triggers a full application restart.

        Args:
            restart (bool): If True, attempts to restart the application process
                            after shutting down. Defaults to False.
        """
        self.app.logger.info('Stopping service..')

        self.close()

        self._event_thread.stop()
        self._version_thread.stop()
        self._camera_thread.stop()
        self._discovery_thread.stop()
        self._notification_thread.stop()
        self._exporter_thread.stop()
        if has_upnp:
            self._upnp_thread.stop()

        if restart:
            try:
                self._event_thread.join(5)
                self._version_thread.join(5)
                self._camera_thread.join(5)
                self._discovery_thread.join(5)
                self._notification_thread.join(5)
                self._exporter_thread.join(5)
                if has_upnp:
                    self._upnp_thread.join(5)

            except RuntimeError:
                pass

        self.websocket.stop()

        if restart:
            self.app.logger.info('Restarting service..')
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def init(self):
        """
        Performs initial setup and configuration for the Decoder service.

        Loads settings, initializes notification and camera systems, starts
        background threads, checks database schema, sets up mailer, generates
        secret key if needed, and triggers an initial device open if configured.
        """
        with self.app.app_context():
            device_type = Setting.get_by_name('device_type').value

            # Add/Update any default event messages that may be missing or changed due to additions.
            # Compares the stored version message with the default to detect necessary updates.
            dbevmsgver = NotificationMessage.query.filter_by(id=EVMSG_VERSION).first()
            if dbevmsgver is None or dbevmsgver.text != DEFAULT_EVENT_MESSAGES[EVMSG_VERSION]:
                current_app.logger.info('New EVENT message formats detected. Your customization will be lost.')
                for event, message in DEFAULT_EVENT_MESSAGES.items():
                    old = NotificationMessage.query.filter_by(id=event).first()
                    if old:
                        db.session.delete(old)
                    db.session.add(NotificationMessage(id=event, text=message))
                db.session.commit()
            # Load mail server configuration from settings
            current_app.config['MAIL_SERVER'] = Setting.get_by_name('system_email_server',default='localhost').value
            current_app.config['MAIL_PORT'] = Setting.get_by_name('system_email_port',default=25).value
            current_app.config['MAIL_USE_TLS'] = Setting.get_by_name('system_email_tls',default=False).value
            current_app.config['MAIL_USERNAME'] = Setting.get_by_name('system_email_username',default='').value
            current_app.config['MAIL_PASSWORD'] = Setting.get_by_name('system_email_password',default='').value
            current_app.config['MAIL_DEFAULT_SENDER'] = Setting.get_by_name('system_email_from',default='youremail@example.com').value

            mail.init_app(current_app)

            # Generate a new session key if it doesn't exist for Flask sessions..
            secret_key = Setting.get_by_name('secret_key')
            if secret_key.value is None:
                secret_key.value = binascii.hexlify(os.urandom(24))
                db.session.add(secret_key)
                db.session.commit()

            current_app.secret_key = secret_key.value

            self.version = self.updater._components['AlarmDecoderWebapp'].version
            current_app.jinja_env.globals['version'] = self.version
            current_app.logger.info('AlarmDecoder Webapp booting up - v{}'.format(self.version))

            # Expose wrapped is_authenticated to jinja.
            current_app.jinja_env.globals['user_is_authenticated'] = user_is_authenticated

            # Check if the database schema needs updating and apply updates if necessary.
            # HACK: This direct access and refresh might be better handled within the updater component itself.
            self.updater._components['AlarmDecoderWebapp']._db_updater.refresh()

            if self.updater._components['AlarmDecoderWebapp']._db_updater.needs_update:
                current_app.logger.debug('Database needs updating!')

                self.updater._components['AlarmDecoderWebapp']._db_updater.update()
            else:
                current_app.logger.debug('Database is good!')
            # Trigger an initial device open if a device type has been configured previously.
            if device_type:
                self.trigger_reopen_device = True
            # Initialize background threads
            self._notifier_system = NotificationSystem()
            self._camera_thread = CameraChecker(self)
            self._discovery_thread = DiscoveryService(self)
            self._notification_thread = NotificationThread(self)
            self._exporter_thread = ExportChecker(self)
            self._version_thread = VersionChecker(self)

            if has_upnp:
                self._upnp_thread = UPNPThread(self)

    def open(self, no_reader_thread=False):
        """
        Opens and initializes the connection to the AlarmDecoder device based on
        configuration settings (local serial or network socket).

        Handles device type selection, interface setup (path/address/port),
        baudrate, SSL configuration, and finally opens the device connection
        using the python-alarmdecoder library.

        Args:
            no_reader_thread (bool): Passed to the AlarmDecoder library's open method.
                                     If True, prevents the library from starting its
                                     own background reader thread.

        Raises:
            NoDeviceError: If the device cannot be found or accessed.
            SSL.Error: If an SSL connection fails.
            Exception: For other potential errors during setup (e.g., missing certs).
        """
        with self.app.app_context():
            self._device_type = Setting.get_by_name('device_type').value
            self._device_location = Setting.get_by_name('device_location').value
            self._internal_address_mask = int(Setting.get_by_name('internal_address_mask', 'FFFFFFFF').value, 16)

            if self._device_type:
                interface = ('localhost', 10000)
                use_ssl = False
                devicetype = SocketDevice

                # Set up device interfaces based on our location.
                if self._device_location == 'local':
                    devicetype = SerialDevice
                    interface = Setting.get_by_name('device_path').value
                    self._device_baudrate = Setting.get_by_name('device_baudrate').value

                elif self._device_location == 'network':
                    interface = (Setting.get_by_name('device_address').value, Setting.get_by_name('device_port').value)
                    use_ssl = Setting.get_by_name('use_ssl', False).value

                # Create and open the device.
                try:
                    device = devicetype(interface=interface)
                    # Configure SSL if required for network devices
                    if use_ssl:
                        try:
                            # Load required certificates from the database
                            ca_cert = Certificate.query.filter_by(name='AlarmDecoder CA').one()
                            internal_cert = Certificate.query.filter_by(name='AlarmDecoder Internal').one()
                            # Apply SSL settings to the device instance
                            device.ssl = True
                            device.ssl_ca = ca_cert.certificate_obj
                            device.ssl_certificate = internal_cert.certificate_obj
                            device.ssl_key = internal_cert.key_obj
                        except NoResultFound as err:
                            self.app.logger.warning('No certificates found: %s', err[0], exc_info=True)
                            raise
                    # Create the main AlarmDecoder interface object
                    self.device = AlarmDecoder(device)
                    self.device.internal_address_mask = self._internal_address_mask
                    # Bind internal handlers to events from the AlarmDecoder library
                    self.bind_events()
                    # Attempt to open the connection
                    self.device.open(baudrate=self._device_baudrate, no_reader_thread=no_reader_thread)

                except NoDeviceError as err:
                    self.app.logger.warning('Open failed: %s', err[0], exc_info=True)
                    raise

                except SSL.Error as err:
                    source, fn, message = err[0][0]
                    self.app.logger.warning('SSL connection failed: %s - %s', fn, message, exc_info=True)
                    raise

    def close(self):
        """
        Closes the connection to the AlarmDecoder device, if currently open.
        Removes event handlers and releases the device object.
        """
        if self.device:
            self.remove_events()
            self.device.close()
            del self.device

    def bind_events(self):
        """
        Binds internal methods of this class as handlers for events emitted by the
        python-alarmdecoder library (e.g., on_message, on_arm, on_disarm).
        Uses helper lambdas to pass event type information to generic handlers.
        Logs warnings if specific events are not supported by the installed library version.
        """
        build_event_handler = lambda ftype: lambda sender, **kwargs: self._handle_event(ftype, sender, **kwargs)
        build_message_handler = lambda ftype: lambda sender, **kwargs: self._on_message(ftype, sender, **kwargs)

        self.device.on_message += build_message_handler('panel')
        self.device.on_lrr_message += build_message_handler('lrr')
        self.device.on_ready_changed += build_message_handler('ready')
        self.device.on_chime_changed += build_message_handler('chime')
        self.device.on_rfx_message += build_message_handler('rfx')
        try:
            self.device.on_aui_message += build_message_handler('aui')
        except AttributeError:
            self.app.logger.warning('Could not bind event "on_aui_message": alarmdecoder library is probably out of date.')

        self.device.on_expander_message += build_message_handler('exp')

        self.device.on_open += self._on_device_open
        self.device.on_close += self._on_device_close

        # Bind the event handler to all of our events.
        for event, device_event_name in EVENT_MAP.items():
            try:
                device_handler = getattr(self.device, device_event_name)
                device_handler += build_event_handler(event)

            except AttributeError:
                self.app.logger.warning('Could not bind event "%s": alarmdecoder library is probably out of date.', device_event_name)

    def remove_events(self):
        """
        Removes all previously bound internal event handlers from the
        python-alarmdecoder device instance. This is crucial before closing
        or deleting the device object to prevent potential issues.
        Logs warnings if specific events cannot be cleared (e.g., outdated library).
        """
        try:
            self.device.on_message.clear()
            self.device.on_lrr_message.clear()
            self.device.on_ready_changed.clear()
            self.device.on_chime_changed.clear()
            self.device.on_rfx_message.clear()
            try:
                self.device.on_aui_message.clear()
            except AttributeError:
                self.app.logger.warning('Could not remove event "on_aui_message": alarmdecoder library is probably out of date.')
    
            self.device.on_expander_message.clear()
    
            self.device.on_open.clear()
            self.device.on_close.clear()
    
            # Clear mapped events.
            for event, device_event_name in EVENT_MAP.items():
                try:
                    device_handler = getattr(self.device, device_event_name)
                    device_handler.clear()
    
                except AttributeError:
                    self.app.logger.warning('Could not clear event "%s": alarmdecoder library is probably out of date.', device_event_name)
    
        except AttributeError:
            self.app.logger.warning("Could not clear events: alarmdecoder library is probably out of date.")

    def refresh_notifier(self, id):
        """
        Triggers a refresh of a specific notifier configuration within the
        NotificationSystem.

        Args:
            id: The ID of the notifier to refresh.
        """
        self._notifier_system.refresh_notifier(id)

    def test_notifier(self, id):
        """
        Sends a test notification through a specific notifier.

        Args:
            id: The ID of the notifier to test.

        Returns:
            Result of the test notification attempt from NotificationSystem.
        """
        return self._notifier_system.test_notifier(id)

    def _on_device_open(self, sender):
        """
        Internal event handler called by the AlarmDecoder library when the
        device connection is successfully opened. Logs the event and broadcasts
        a 'device_open' message to connected WebSocket clients. Resets the
        reopen trigger flag.

        Args:
            sender: The AlarmDecoder device instance that triggered the event.
        """
        self.app.logger.info('AlarmDecoder device was opened.')

        self.broadcast('device_open')
        self.trigger_reopen_device = False

    def _on_device_close(self, sender):
        """
        Internal event handler called by the AlarmDecoder library when the
        device connection is closed (either intentionally or due to an error).
        Logs the event, broadcasts 'device_close' to clients, and sets the
        flag to trigger a reconnection attempt by the DecoderThread.

        Args:
            sender: The AlarmDecoder device instance that triggered the event.
        """
        self.app.logger.info('AlarmDecoder device was closed.')

        self.broadcast('device_close')
        self.trigger_reopen_device = True

    def _on_message(self, ftype, sender, **kwargs):
        """
        Internal event handler called by the AlarmDecoder library for various
        raw message types (panel, lrr, rfx, etc.). Logs the raw message and
        broadcasts it to connected WebSocket clients under the 'message' channel.
        Updates the last received panel message timestamp.

        Args:
            ftype (str): A string indicating the type of message (e.g., 'panel', 'lrr').
            sender: The AlarmDecoder device instance.
            **kwargs: Contains the message details (e.g., kwargs['message']).
        """
        try:
            message = str(kwargs.get('message', None))

            if ftype == 'panel':
                self.last_message_received = message

            self.broadcast('message', { 'message': kwargs.get('message', None), 'message_type': ftype } )

        except Exception:
            self.app.logger.error('Error while broadcasting message.', exc_info=True)

    def _handle_event(self, ftype, sender, **kwargs):
        """
        Internal generic event handler called by the AlarmDecoder library for
        higher-level events (arm, disarm, alarm, zone fault, etc., as defined
        in EVENT_MAP). Sends the event details to the NotificationSystem for
        processing and broadcasts the event data to connected WebSocket clients
        under the 'event' channel. Updates the last message timestamp.

        Args:
            ftype (int): The internal event type constant (e.g., ARM, DISARM).
            sender: The AlarmDecoder device instance.
            **kwargs: Contains event-specific data (e.g., zone, user).
        """
        try:
            self._last_message = time.time()

            with self.app.app_context():
                errors = self._notifier_system.send(ftype, **kwargs)
                for e in errors:
                    self.app.logger.error(e)

            self.broadcast('event', kwargs)

        except Exception:
            self.app.logger.error('Error while broadcasting event.', exc_info=True)

    def broadcast(self, channel, data={}):
        """
        Broadcasts a message to all authenticated WebSocket clients connected
        to the '/alarmdecoder' namespace.

        Args:
            channel (str): The Socket.IO event name (channel) to emit.
            data (dict): The data payload to send (will be JSON-encoded).
        """
        obj = jsonpickle.encode(data, unpicklable=False)
        packet = self._make_packet(channel, obj)

        self._broadcast_packet(packet)

    def _broadcast_packet(self, packet):
        """
        Sends a pre-formatted Socket.IO packet to all authenticated clients.

        Args:
            packet (dict): The Socket.IO packet dictionary.
        """
        for session, sock in self.websocket.sockets.items():
            authenticated = sock.session.get('authenticated', False)

            if authenticated:
                sock.send_packet(packet)

    def _make_packet(self, channel, data):
        """
        Constructs a standard Socket.IO event packet dictionary.

        Args:
            channel (str): The event name (channel).
            data (str): The JSON-encoded data payload.

        Returns:
            dict: A dictionary representing the Socket.IO packet.
        """
        return dict(type='event', name=channel, args=data, endpoint='/alarmdecoder')

class DecoderThread(threading.Thread):
    """
    Background worker thread responsible for handling periodic tasks related
    to the AlarmDecoder device state, primarily:
    1. Attempting to reconnect if the device connection drops (`trigger_reopen_device`).
    2. Triggering a service restart if requested (`trigger_restart`).
    """

    TIMEOUT = 5
    """Thread sleep time."""

    def __init__(self, decoder):
        """
        Constructor

        :param decoder: Parent decoder object
        :type decoder: Decoder
        """
        threading.Thread.__init__(self)
        self._decoder = decoder
        self._running = False

    def stop(self):
        """Signals the thread to stop its execution loop."""
        self._running = False

    def run(self):
        """
        Main execution loop for the thread. Periodically checks for reopen
        or restart triggers and acts accordingly within the Flask app context.
        Includes basic error handling for the loop itself.
        """
        self._running = True

        while self._running:
            with self._decoder.app.app_context():
                try:
                    # Handle reopen events
                    if self._decoder.trigger_reopen_device:
                        self._decoder.app.logger.info('Attempting to reconnect to the AlarmDecoder')
                        try:
                            self._decoder.open()
                        except NoDeviceError as err:
                            self._decoder.app.logger.error('Device not found: {}'.format(err[0]))

                    # Handle service restart events
                    if self._decoder.trigger_restart:
                        self._decoder.updates = {}
                        self._decoder.app.jinja_env.globals['update_available'] = False
                        self._decoder.app.logger.info('Restarting service..')
                        self._decoder.stop(restart=True)

                    time.sleep(self.TIMEOUT)

                except Exception as err:
                    self._decoder.app.logger.error('Error in DecoderThread: {}'.format(err), exc_info=True)

class VersionChecker(threading.Thread):
    """
    Background worker thread that periodically checks for available software updates
    for the webapp and potentially AlarmDecoder firmware. Updates global Jinja
    variables to indicate available updates in the UI. Respects configured check
    intervals and disable flags.
    """
    TIMEOUT = 60
    """Version checker sleep time."""

    def __init__(self, decoder):
        """
        Constructor

        :param decoder: Parent decoder object
        :type decoder: Decoder
        """
        threading.Thread.__init__(self)
        self._decoder = decoder
        self._updater = decoder.updater
        self._running = False
        self.last_check_time = float(Setting.get_by_name('version_checker_last_check_time', default=0).value)
        self.version_checker_timeout = int(Setting.get_by_name('version_checker_timeout', default=600).value)
        self.disable_version_checker = Setting.get_by_name('version_checker_disable', default=False).value

    def stop(self):
        """Signals the thread to stop its execution loop."""

        self._running = False

    def setTimeout(self, timeout):
        """
        Updates the interval (in seconds) between version checks.

        Args:
            timeout (int): The new check interval in seconds.
        """

        self._decoder.app.logger.info('Updating version check thread timeout to: {} seconds'.format(timeout))
        self.version_checker_timeout = int(timeout)

    def setDisable(self, disable):
        """
        Enables or disables the version checking functionality.

        Args:
            disable (bool): True to disable checks, False to enable.
        """
        self._decoder.app.logger.info('Updating version check enable/disable to: {}'.format("Enabled" if not disable else "Disabled"))
        self.disable_version_checker = disable

    def run(self):
        """
        Main execution loop. Performs update checks based on the configured
        timeout and disabled status. Updates database timestamp and Jinja globals.
        Handles potential errors during the check process.
        """
        self._running = True

        while self._running:
            with self._decoder.app.app_context():
                if not self.disable_version_checker:
                    try:
                        check_time = time.time()
                        if check_time > self.last_check_time + self.version_checker_timeout:
                            self._decoder.app.logger.info('Checking for version updates - last check at: {}'.format(datetime.datetime.fromtimestamp(self.last_check_time).strftime('%m-%d-%Y %H:%M:%S')))
                            self._decoder.updates = self._updater.check_updates()
                            update_available = not all(not needs_update for component, (needs_update, branch, revision, new_revision, status, project_url) in self._decoder.updates.items())

                            current_app.jinja_env.globals['update_available'] = update_available
                            current_app.jinja_env.globals['firmware_update_available'] = self._updater.check_firmware()

                            self.last_check_time = check_time
                            version_checker_last_check_time = Setting.get_by_name('version_checker_last_check_time')
                            version_checker_last_check_time.value = int(self.last_check_time)

                            db.session.add(version_checker_last_check_time)
                            db.session.commit()


                    except Exception as err:
                        self._decoder.app.logger.error('Error in VersionChecker: {}'.format(err), exc_info=True)

            time.sleep(self.TIMEOUT)

class CameraChecker(threading.Thread):
    """
    Background worker thread responsible for periodically polling configured
    camera streams to capture still images using the CameraSystem.
    """
    TIMEOUT = 1
    """Camera checker thread sleep time."""

    def __init__(self, decoder):
        """
        Constructor
        :param decoder: Parent decoder object
        :type decoder: Decoder
        """
        threading.Thread.__init__(self)
        self._decoder = decoder
        self._running = False
        self._cameras = CameraSystem()

    def stop(self):
        """Signals the thread to stop its execution loop."""
        self._running = False

    def run(self):
        """
        Main execution loop. Continuously refreshes the list of camera IDs
        and triggers image capture for each active camera within the Flask app context.
        Includes basic error handling.
        """
        self._running = True

        while self._running:
            with self._decoder.app.app_context():
                try:
                    self._cameras.refresh_camera_ids()
                    for n in self._cameras.get_camera_ids():
                        self._cameras.write_image(n)

                except Exception as err:
                    self._decoder.app.logger.error('Error in CameraChecker: {}'.format(err), exc_info=True)

            time.sleep(self.TIMEOUT)

class ExportChecker(threading.Thread):
    """
    Background worker thread responsible for performing scheduled backups
    (exports) of the application settings database.

    Handles exporting data, writing to a local file (optional), emailing the backup
    (optional), and cleaning up old backup files based on retention settings.
    Configuration is loaded initially and can be partially updated via `update*` methods,
    but a full refresh might require a restart or explicit call to `prepParams`.
    """
    TIMEOUT = 600

    def __init__(self, decoder):
        threading.Thread.__init__(self)
        self._decoder = decoder
        self._running = False
        self.first_run = True
        self.local_storage = None
        self.prepParams()
        

    def prepParams(self):
        """
        Loads all necessary configuration parameters for exporting and emailing
        from the application settings stored in the database. Initializes Mailer
        and Exporter instances. Called during thread initialization.
        Note: Does not automatically re-run if settings change later.
        """
        self.server = Setting.get_by_name('system_email_server', default='localhost').value
        self.port = Setting.get_by_name('system_email_port', default=25).value
        self.tls = Setting.get_by_name('system_email_tls', default=False).value
        self.auth_required = Setting.get_by_name('system_email_auth',default=False).value
        self.username = Setting.get_by_name('system_email_username', default=None).value
        self.password = Setting.get_by_name('system_email_password', default=None).value
        self.send_from = Setting.get_by_name('system_email_from',default='root@alarmdecoder').value
        self.subject = "AlarmDecoder Settings Database Backup"
        self.body = "AlarmDecoder Settings Database Backup\r\n"
        self.to = []
        self.to.append(Setting.get_by_name('export_mailer_to', default=None).value)

        self._mailer = Mailer(self.server, self.port, self.tls, self.auth_required, self.username, self.password)
        self._exporter = Exporter()

        self.export_frequency = Setting.get_by_name('export_frequency', default=0).value
        self.local_storage = Setting.get_by_name('enable_local_file_storage', default=False).value
        self.local_path = Setting.get_by_name('export_local_path', default=os.path.join(INSTANCE_FOLDER_PATH, 'exports')).value
        self.email_enable = Setting.get_by_name('export_email_enable', default=False).value
        self.days_to_keep = Setting.get_by_name('days_to_keep', default=7).value
        self.last_check_time = int(Setting.get_by_name('export_last_check_time', default=0).value)

        self._decoder.app.logger.info('Set export parameters to:  server {} port {} tls {} auth {} from {} frequency {} store files {} storage path {} days to keep files {} email enable {}'.format(self.server, self.port, self.tls, self.auth_required, self.send_from, self.export_frequency, self.local_storage, self.local_path, self.days_to_keep, self.email_enable))

    def stop(self):
        """Signals the thread to stop its execution loop."""
        self._running = False
    # Methods to update specific configuration parameters (mostly for the Mailer)
    # Note: These only update the thread's internal state, not the database settings.
    def updateFrequency(self, frequency):
        self.export_frequency = frequency

    def addTo(self, to):
        self.to.append(to)

    def updateUsername(self, username):
        self._mailer.updateUsername(username)

    def updatePassword(self, password):
        self._mailer.updatePassword(password)

    def updateFrom(self, email_from):
        self.send_from = email_from

    def updateServer(self, server):
        self._mailer.updateServer(server)

    def updatePort(self, port):
        self._mailer.updatePort(port)

    def updateTls(self, tls):
        self._mailer.updateTls(tls)

    def updateAuth(self, auth):
        self._mailer.updateAuth(auth)

    def updateSubject(self, subject):
        self.subject = subject

    def updateBody(self, body):
        self.body = body

    def run(self):
        """
        Main execution loop. Checks if an export is due based on the configured
        frequency and the last export time. If due, performs the export, handles
        file storage/emailing/cleanup according to settings. Updates the last
        export timestamp in the database. Manages cleanup of old export files.
        Includes error handling.
        """
        self._running = True

        while self._running:
            with self._decoder.app.app_context():
                if self.export_frequency > 0:
                    try:
                        check_time = time.time()
                        if check_time > self.last_check_time + self.export_frequency:
                            self._decoder.app.logger.info('Checking if we need to export settings.')
                            if not self.first_run:
                                self._exporter.exportSettings()
                                full_path = self._exporter.writeFile()

                                files = []
                                files.append(full_path)
                                if self.email_enable and full_path is not None:
                                    self._decoder.app.logger.info('Sending export email: {} - {}'.format(self.to, files))
                                    self._mailer.send_mail(self.send_from, self.to, self.subject, self.body, files)

                                if not self.local_storage:
                                    self._decoder.app.logger.info('Not keeping export on disk - {}'.format(full_path))
                                    self._exporter.removeFile()

                                self.last_check_time = check_time
                                export_last_check_time = Setting.get_by_name('export_last_check_time')
                                export_last_check_time.value = int(self.last_check_time)

                                db.session.add(export_last_check_time)
                                db.session.commit()
                            else:
                                self.first_run = False
                            
                            self._exporter.removeOldFiles(self.days_to_keep)
                    
                    except Exception as err:
                        self._decoder.app.logger.error('Error in ExportChecker: {}'.format(err), exc_info=True)

            time.sleep(self.TIMEOUT)

class DecoderNamespace(socketio.Namespace):
    """
    Socket.IO Namespace handler for '/alarmdecoder'.
    Manages WebSocket connections, authentication, and routes messages
    between clients and the main Decoder instance.
    """

    def on_connect(self, sid, environ):
        """
        Called when a client attempts to connect. Returns False to reject.
        Checks Flask session auth or initial setup stage before allowing further events.
        """
        try:
            # Enter Flask request context to access session
            with current_app.request_context(environ):
                flask_req = request._get_current_object()
                sess = current_app.session_interface.open_session(current_app, flask_req)
                user_id = sess.get('user_id')
                setup_stage = Setting.get_by_name('setup_stage', default=0).value

                # Allow if still in setup or user is logged in
                if (setup_stage and setup_stage != SETUP_COMPLETE) or user_id:
                    return True
        except Exception as err:
            current_app.logger.error(f"WebSocket auth failed: {err}")

        # Reject connection
        return False

    def on_keypress(self, sid, key):
        """Handle 'keypress' events by sending commands to the device."""
        try:
            dev = g.alarmdecoder.device
            if key == 1:
                dev.send(AlarmDecoder.KEY_F1)
            elif key == 2:
                dev.send(AlarmDecoder.KEY_F2)
            elif key == 3:
                dev.send(AlarmDecoder.KEY_F3)
            elif key == 4:
                dev.send(AlarmDecoder.KEY_F4)
            elif key == 5:
                dev.send(AlarmDecoder.KEY_PANIC)
            else:
                dev.send(key)
        except (CommError, AttributeError):
            current_app.logger.error('Error sending keypress', exc_info=True)

    def on_firmwareupload(self, sid, *args):
        """Handle 'firmwareupload' events and broadcast update stages."""
        reopen_reader = False
        dev = g.alarmdecoder.device
        try:
            enable_reconfig = callable(getattr(dev, 'get_config_string', None))
            orig_cfg = dev.get_config_string() if enable_reconfig else ''

            current_app.logger.info('Starting firmware upload: %s', g.alarmdecoder.firmware_file)
            fu = FirmwareUpdater(filename=g.alarmdecoder.firmware_file,
                                 length=g.alarmdecoder.firmware_length)
            fu.update()

            if fu.completed:
                if enable_reconfig:
                    self.emit('firmwareupload', {'stage': 'STAGE_CONFIGURE'})
                    time.sleep(10)
                    dev.send(f"C{orig_cfg}\r")
                    time.sleep(5)
                    current_app.jinja_env.globals['firmware_update_available'] = False

                self.emit('firmwareupload', {'stage': 'STAGE_FINISHED'})
                g.alarmdecoder.firmware_file = None
                g.alarmdecoder.firmware_length = -1
                reopen_reader = True

        except Exception as err:
            current_app.logger.error('Firmware upload error: %s', err)
            self.emit('firmwareupload', {'stage': 'STAGE_ERROR', 'error': 'Upload failed'})

        finally:
            g.alarmdecoder.close()
            g.alarmdecoder.open(no_reader_thread=not reopen_reader)

    def on_test(self, sid, *args):
        """Handle 'test' events: run open, config, send, and receive tests."""
        try:
            self._test_open(sid)
            time.sleep(0.5)
            self._test_config(sid)
            self._test_send(sid)
            self._test_receive(sid)
        except Exception:
            current_app.logger.error('Device test sequence error', exc_info=True)

    def _test_open(self, sid):
        results, details = 'PASS', ''
        try:
            g.alarmdecoder.close()
            g.alarmdecoder.open()
        except NoDeviceError as err:
            results, details = 'FAIL', f"{err[0]}: {err[1][1]}"
            current_app.logger.error('Test open error', exc_info=True)
        except Exception as err:
            results, details = 'FAIL', f"Open failed: {err}"
            current_app.logger.error('Test open exception', exc_info=True)
        finally:
            self.emit('test', {'test': 'open', 'results': results, 'details': details})

    def _test_config(self, sid):
        def on_config(device):
            timer.cancel()
            self.emit('test', {'test': 'config', 'results': 'PASS', 'details': ''})
            device.on_config_received.remove(on_config)

        def on_timeout():
            self.emit('test', {'test': 'config', 'results': 'TIMEOUT', 'details': 'Timeout'})
            g.alarmdecoder.device.on_config_received.remove(on_config)

        timer = threading.Timer(10, on_timeout)
        timer.start()
        try:
            # Load settings
            panel_mode = Setting.get_by_name('panel_mode').value
            address    = Setting.get_by_name('keypad_address').value
            mask_hex   = Setting.get_by_name('address_mask').value
            lrr        = Setting.get_by_name('lrr_enabled').value
            zone_vals  = Setting.get_by_name('emulate_zone_expanders').value
            relay_vals = Setting.get_by_name('emulate_relay_expanders').value
            dedup      = Setting.get_by_name('deduplicate').value

            # Apply config
            dev = g.alarmdecoder.device
            dev.mode            = panel_mode
            dev.address         = address
            dev.address_mask    = int(mask_hex, 16)
            dev.emulate_zone    = [v=='True' for v in zone_vals.split(',')]
            dev.emulate_relay   = [v=='True' for v in relay_vals.split(',')]
            dev.emulate_lrr     = lrr
            dev.deduplicate     = dedup

            dev.on_config_received.append(on_config)
            dev.save_config()
        except Exception:
            timer.cancel()
            if on_config in g.alarmdecoder.device.on_config_received:
                g.alarmdecoder.device.on_config_received.remove(on_config)
            self.emit('test', {'test': 'config', 'results':'FAIL', 'details':'Config error'})
            current_app.logger.error('Test config error', exc_info=True)

    def _test_send(self, sid):
        def on_send(device, status, msg):
            timer.cancel()
            device.on_sending_received.remove(on_send)
            res = 'PASS' if status else 'FAIL'
            det = '' if status else 'Send failure'
            self.emit('test', {'test':'send', 'results':res, 'details':det})

        def on_timeout():
            self.emit('test', {'test':'send', 'results':'TIMEOUT','details':'Timeout'})
            g.alarmdecoder.device.on_sending_received.remove(on_send)

        timer = threading.Timer(10, on_timeout)
        timer.start()
        try:
            dev = g.alarmdecoder.device
            dev.on_sending_received.append(on_send)
            dev.send("*\r")
        except Exception:
            timer.cancel()
            dev.on_sending_received.remove(on_send)
            self.emit('test', {'test':'send','results':'FAIL','details':'Send error'})
            current_app.logger.error('Test send error', exc_info=True)

    def _test_receive(self, sid):
        def on_msg(device, message):
            timer.cancel()
            device.on_message.remove(on_msg)
            self.emit('test', {'test':'recv','results':'PASS','details':''})

        def on_timeout():
            self.emit('test', {'test':'recv','results':'TIMEOUT','details':'Timeout'})
            g.alarmdecoder.device.on_message.remove(on_msg)

        timer = threading.Timer(10, on_timeout)
        timer.start()
        try:
            dev = g.alarmdecoder.device
            dev.on_message.append(on_msg)
            dev.send("*\r")
        except Exception:
            timer.cancel()
            dev.on_message.remove(on_msg)
            self.emit('test', {'test':'recv','results':'FAIL','details':'Receive error'})
            current_app.logger.error('Test recv error', exc_info=True)
