import os
import sys
import time
import binascii
import threading

from flask import current_app

# Import AlarmDecoder library components
from alarmdecoder import AlarmDecoder
from alarmdecoder.devices import SocketDevice, SerialDevice
from alarmdecoder.util import NoDeviceError, CommError

# Import project components (database, settings, etc.)
from ad2web.extensions import db
from ad2web.settings.models import Setting
from ad2web.certificate.models import Certificate
from ad2web.updater import Updater
from ad2web.updater.models import FirmwareUpdater

# Import the new services for notifications and backups
from ad2web.services.notification_service import NotificationService
from ad2web.services.backup_service import BackupService

# Optional imports for UPNP and discovery (reuse existing implementations)
try:
    from ad2web.upnp import UPNPThread

    HAS_UPNP = True
except ImportError:
    UPNPThread = None
    HAS_UPNP = False

from ad2web.services.discovery_service import DiscoveryService


class DecoderService:
    """
    Service class for managing the AlarmDecoder device connection and all related
    background tasks (notification dispatching, backup scheduling, UPNP, discovery, etc.).
    """

    def __init__(self, app, websocket_server):
        """
        Initialize the DecoderService with the Flask app and a SocketIO server for WebSocket communication.
        """
        self.app = app
        self.websocket = websocket_server
        self.device = None
        self.updater = Updater()
        self.updates = {}  # Holds information about available updates
        self.version = ''  # Webapp version string
        self.firmware_file = None
        self.firmware_length = -1

        # Flags to trigger certain actions from the monitor thread
        self.trigger_reopen_device = False
        self.trigger_restart = False

        # Device configuration state
        self._last_message_time = None
        self._device_baudrate = 115200
        self._device_type = None
        self._device_location = None
        self._internal_address_mask = 0xFFFFFFFF

        # Background services (will be initialized in init())
        self.notification_service = None
        self.backup_service = None
        self._camera_thread = None
        self._version_thread = None
        self._upnp_thread = None
        self._discovery_thread = None

        # Internal monitoring thread for device reconnection and restart events
        self._monitor_thread = None
        self._monitor_running = False

    def init(self):
        """
        Perform one-time setup for the DecoderService. This loads configuration from the database,
        sets up notifications and backup services, and prepares background threads. Should be called
        once after construction, before starting the service.
        """
        with self.app.app_context():
            # Load essential settings
            device_type_setting = Setting.get_by_name('device_type')
            device_type = device_type_setting.value if device_type_setting else None

            # Ensure default event notification messages are present/up-to-date
            from ad2web.notifications.constants import DEFAULT_EVENT_MESSAGES, EVMSG_VERSION
            from ad2web.notifications.models import NotificationMessage
            ev_ver = NotificationMessage.query.filter_by(id=EVMSG_VERSION).first()
            if ev_ver is None or ev_ver.text != DEFAULT_EVENT_MESSAGES.get(EVMSG_VERSION):
                current_app.logger.info('Updating default event messages to latest format.')
                # Replace all stored messages with defaults
                for event_id, text in DEFAULT_EVENT_MESSAGES.items():
                    old_msg = NotificationMessage.query.filter_by(id=event_id).first()
                    if old_msg:
                        db.session.delete(old_msg)
                    db.session.add(NotificationMessage(id=event_id, text=text))
                db.session.commit()

            # Load email (Mailer) configuration from settings into Flask app config
            current_app.config['MAIL_SERVER'] = Setting.get_by_name('system_email_server', default='localhost').value
            current_app.config['MAIL_PORT'] = Setting.get_by_name('system_email_port', default=25).value
            current_app.config['MAIL_USE_TLS'] = Setting.get_by_name('system_email_tls', default=False).value
            current_app.config['MAIL_USERNAME'] = Setting.get_by_name('system_email_username', default='').value
            current_app.config['MAIL_PASSWORD'] = Setting.get_by_name('system_email_password', default='').value
            current_app.config['MAIL_DEFAULT_SENDER'] = Setting.get_by_name('system_email_from',
                                                                            default='youremail@example.com').value

            # Initialize Flask-Mail extension with updated config (if used elsewhere)
            from ad2web.extensions import mail
            mail.init_app(current_app)

            # Ensure a secret key for Flask sessions exists
            secret_key_setting = Setting.get_by_name('secret_key')
            if secret_key_setting.value is None:
                secret_key_setting.value = binascii.hexlify(os.urandom(24)).decode('utf-8')
                db.session.add(secret_key_setting)
                db.session.commit()
            current_app.secret_key = secret_key_setting.value

            # Determine application version from Updater component
            try:
                self.version = self.updater._components['AlarmDecoderWebapp'].version
            except Exception:
                self.version = 'unknown'
            current_app.jinja_env.globals['version'] = self.version
            current_app.logger.info(f'AlarmDecoder Webapp starting up - v{self.version}')

            # Expose user authentication helper to templates
            from ad2web.utils.user_utils import user_is_authenticated
            current_app.jinja_env.globals['user_is_authenticated'] = user_is_authenticated

            # Apply any pending database schema updates (via Updater)
            try:
                db_updater = self.updater._components['AlarmDecoderWebapp']._db_updater
                db_updater.refresh()
                if db_updater.needs_update:
                    current_app.logger.debug('Database schema out-of-date, applying updates.')
                    db_updater.update()
                else:
                    current_app.logger.debug('Database schema is up-to-date.')
            except Exception as err:
                current_app.logger.error(f'Error during DB update check: {err}', exc_info=True)

            # If a device type is configured, flag the device to open on service start
            if device_type:
                self.trigger_reopen_device = True

            # Initialize Notification and Backup services (with Mailer integrated into NotificationService)
            self.notification_service = NotificationService(app=self.app)
            self.backup_service = BackupService(app=self.app, notification_service=self.notification_service)

            # Initialize other background threads (camera polling, version checking, discovery, UPNP)
            from ad2web.decoder import VersionChecker  # thread that checks for updates
            from ad2web.decoder import CameraSystem, CameraChecker  # camera monitoring thread

            self._camera_thread = CameraChecker(self)
            self._version_thread = VersionChecker(self)
            self._discovery_thread = DiscoveryServer(self)
            if HAS_UPNP:
                # Initialize UPNP port-forwarding thread if library is available
                self._upnp_thread = UPNPThread(self)

    def start(self):
        """
        Start the AlarmDecoder service by launching all background threads and services.
        This opens the device connection (if configured) and begins handling events.
        """
        # Start the device monitor thread (handles reconnections and restarts)
        if self._monitor_thread is None:
            self._monitor_running = True
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()

        # Start each background service/thread
        if self.notification_service:
            self.notification_service.start()
        if self.backup_service:
            self.backup_service.start()
        if self._camera_thread:
            self._camera_thread.daemon = True
            self._camera_thread.start()
        if self._version_thread:
            self._version_thread.daemon = True
            self._version_thread.start()
        if self._discovery_thread:
            self._discovery_thread.daemon = True
            self._discovery_thread.start()
        if HAS_UPNP and self._upnp_thread:
            self._upnp_thread.daemon = True
            self._upnp_thread.start()

    def stop(self, restart=False):
        """
        Gracefully stop the AlarmDecoder service, including all background tasks and device connection.
        If `restart` is True, the process will attempt to restart itself after shutdown.
        """
        self.app.logger.info('Stopping AlarmDecoder service...')
        # Signal all threads/services to stop
        self._monitor_running = False
        if self.notification_service:
            self.notification_service.stop()
        if self.backup_service:
            self.backup_service.stop()
        if self._camera_thread:
            self._camera_thread.stop()
        if self._version_thread:
            self._version_thread.stop()
        if self._discovery_thread:
            self._discovery_thread.stop()
        if HAS_UPNP and self._upnp_thread:
            self._upnp_thread.stop()

        # Join threads for a short duration to ensure they exit
        try:
            if self._monitor_thread:
                self._monitor_thread.join(timeout=5)
            if self._camera_thread:
                self._camera_thread.join(timeout=5)
            if self._version_thread:
                self._version_thread.join(timeout=5)
            if self._discovery_thread:
                self._discovery_thread.join(timeout=5)
            if HAS_UPNP and self._upnp_thread:
                self._upnp_thread.join(timeout=5)
        except RuntimeError:
            # Threads may already be dead or not yet started
            pass

        # Close device and stop SocketIO server
        self.close()
        if hasattr(self.websocket, 'stop'):
            self.websocket.stop()

        if restart:
            self.app.logger.info('Restarting AlarmDecoder service...')
            os.execv(sys.executable, [sys.executable] + sys.argv)

    def open(self, no_reader_thread=False):
        """
        Open and initialize the connection to the AlarmDecoder device based on configured settings.
        This uses the device type (serial or network) and associated parameters from the database.
        If `no_reader_thread` is True, it will prevent the AlarmDecoder library from starting its own internal reader thread.
        """
        with self.app.app_context():
            self._device_type = Setting.get_by_name('device_type').value
            self._device_location = Setting.get_by_name('device_location').value
            try:
                mask_str = Setting.get_by_name('internal_address_mask', default='FFFFFFFF').value
                self._internal_address_mask = int(mask_str, 16)
            except Exception:
                self._internal_address_mask = 0xFFFFFFFF

            if not self._device_type:
                # No device configured
                return

            # Determine device interface based on type and location
            use_ssl = False
            if self._device_location == 'local':
                # Serial device
                dev_class = SerialDevice
                interface = Setting.get_by_name('device_path').value
                baud = Setting.get_by_name('device_baudrate').value
                self._device_baudrate = baud if baud else 115200
            elif self._device_location == 'network':
                # Network device (IP)
                dev_class = SocketDevice
                addr = Setting.get_by_name('device_address').value or 'localhost'
                port = Setting.get_by_name('device_port').value or 10000
                interface = (addr, port)
                use_ssl = Setting.get_by_name('use_ssl', default=False).value
            else:
                # Default to network socket if unspecified
                dev_class = SocketDevice
                interface = ('localhost', 10000)

            # Close any existing connection first
            self.close()

            # Attempt to open the AlarmDecoder device
            try:
                self.device = AlarmDecoder(dev_class(interface))
                # Apply internal address mask setting
                self.device.internal_address_mask = self._internal_address_mask
                # Bind event handlers from AlarmDecoder library to our handler methods
                from ad2web.decoder import EVENT_MAP  # mapping of internal events to AlarmDecoder signals
                for event_const, signal_name in EVENT_MAP.items():
                    # Bind both message events and higher-level events to a unified handler
                    if hasattr(self.device, signal_name):
                        getattr(self.device, signal_name).add_handler(
                            lambda sender, event_const=event_const, **kwargs: self._handle_device_event(event_const,
                                                                                                        **kwargs)
                        )
                # Also bind raw message handler for panel messages
                if hasattr(self.device, 'on_message'):
                    self.device.on_message += self._handle_device_message

                # Open the device (no internal reader thread if specified, since we handle via SocketIO)
                self.device.open(no_reader_thread=no_reader_thread, baudrate=self._device_baudrate, ssl=use_ssl)
                self.app.logger.info(
                    f'AlarmDecoder device connection opened ({self._device_type}/{self._device_location}).')
            except NoDeviceError as err:
                self.app.logger.error(f'AlarmDecoder device not found or inaccessible: {err}')
                self.device = None
            except Exception as err:
                self.app.logger.error(f'Error opening AlarmDecoder device: {err}', exc_info=True)
                self.device = None

    def close(self):
        """
        Close the AlarmDecoder device connection if open.
        """
        if self.device is not None:
            try:
                self.device.close()
            except Exception as err:
                self.app.logger.warning(f'Error closing device: {err}')
        self.device = None

    def _monitor_loop(self):
        """
        Internal thread loop for monitoring device and service status.
        Handles automatic device reconnection and service restarts based on flags.
        """
        while self._monitor_running:
            with self.app.app_context():
                try:
                    # If a device reconnection was requested, attempt to reopen the device
                    if self.trigger_reopen_device:
                        self.app.logger.info('Reconnecting to AlarmDecoder device...')
                        self.trigger_reopen_device = False
                        try:
                            self.open()
                        except NoDeviceError as err:
                            self.app.logger.error(f'Device reconnect failed: {err}')
                    # If a service restart was triggered, perform graceful shutdown and exec restart
                    if self.trigger_restart:
                        self.trigger_restart = False
                        self.updates = {}
                        current_app.jinja_env.globals['update_available'] = False
                        self.app.logger.info('Service restart flag detected. Restarting...')
                        # Stop with restart=True will execv the process
                        self.stop(restart=True)
                        return  # ensure loop exits if restarting
                except Exception as err:
                    self.app.logger.error(f'Error in DecoderService monitor loop: {err}', exc_info=True)
            time.sleep(5)  # short delay between checks

    def _handle_device_message(self, sender, **kwargs):
        """
        Handler for low-level message events from the AlarmDecoder device (panel data).
        Broadcasts raw messages to WebSocket clients.
        """
        try:
            message = str(kwargs.get('message', ''))
            # Save the last raw message (for internal usage or status)
            self.last_message_received = message
            # Broadcast the message to all connected WebSocket clients
            self.broadcast('message', {'message': message, 'message_type': 'panel'})
        except Exception:
            self.app.logger.error('Error while broadcasting raw panel message.', exc_info=True)

    def _handle_device_event(self, event_type, **kwargs):
        """
        Handler for high-level AlarmDecoder events (arm, disarm, alarm, zone fault, etc.).
        Dispatches notifications and broadcasts events to WebSocket clients.
        """
        try:
            self._last_message_time = time.time()  # mark timestamp of last event
            # Send notifications for this event through the NotificationService
            if self.notification_service:
                self.notification_service.notify_event(event_type, **kwargs)
            # Broadcast the event data to WebSocket clients
            self.broadcast('event', kwargs)
        except Exception:
            self.app.logger.error('Error handling AlarmDecoder event.', exc_info=True)

    def broadcast(self, channel, data=None):
        """
        Broadcast a message or event to all authenticated WebSocket clients on the '/alarmdecoder' namespace.

        :param channel: Name of the Socket.IO event channel.
        :param data: Data payload (dict) to send, which will be JSON-encoded.
        """
        if data is None:
            data = {}
        try:
            import jsonpickle
            payload = jsonpickle.encode(data, unpicklable=False)
        except Exception:
            payload = '{}'
        packet = dict(type='event', name=channel, args=payload, endpoint='/alarmdecoder')
        # Send to each connected and authenticated socket
        for session_id, socket in list(self.websocket.sockets.items()):
            if socket.session.get('authenticated', False):
                try:
                    socket.send_packet(packet)
                except Exception as err:
                    self.app.logger.error(f"WebSocket send error: {err}")
