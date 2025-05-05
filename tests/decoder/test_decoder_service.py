import os
import sys
import time
import threading
import types
import pytest
from unittest.mock import MagicMock

import services.decoder_service as dec_service
DecoderService = dec_service.DecoderService

class DummyZone:
    """Dummy zone object with status and zone identifier."""
    def __init__(self, zone, status):
        self.zone = zone
        self.status = status

class DummyZoneTracker:
    """Dummy zone tracker with a dictionary of zones."""
    def __init__(self, zones=None):
        self.zones = zones or {}

class DummyDevice:
    """Dummy AlarmDecoder device to simulate hardware interactions and state."""
    def __init__(self):
        # Panel state attributes
        self.mode = 0  # simulate ADEMCO mode constant
        self._relay_status = {}
        self._zonetracker = DummyZoneTracker()
        self._power_status = True
        self._ready_status = True
        self._alarm_status = False
        self._bypass_status = False
        self._armed_status = False
        self._armed_stay = False
        self._fire_status = False
        self._battery_status = (False,)
        self._panic_status = False
        self._chime_status = False
        self._perimeter_only_status = False
        self._entry_delay_off_status = False
        self._exit = False
        # Configuration attributes
        self.address = 1
        self.configbits = 0
        self.address_mask = 0xFFFFFFFF
        self.emulate_zone = False
        self.emulate_relay = False
        self.emulate_lrr = False
        self.deduplicate = False
        # Mockable device methods
        self.send = MagicMock()
        self.reboot = MagicMock()
        self.save_config = MagicMock()
        self.close = MagicMock()

@pytest.fixture
def app_and_decoder():
    """Provides a Flask app and a DecoderService instance with dummy dependencies."""
    from flask import Flask
    app = Flask(__name__)
    # Use dummy logger to capture log calls
    app.logger = MagicMock()
    # Dummy websocket server with stop() and sockets
    dummy_socket = types.SimpleNamespace(stop=MagicMock(), sockets={})
    decoder = DecoderService(app, dummy_socket)
    return app, decoder

class DummySetting:
    """Dummy Setting object to simulate database settings."""
    def __init__(self, value):
        self.value = value

def test_initialization_defaults(app_and_decoder):
    """DecoderService initializes with correct default attributes."""
    app, decoder = app_and_decoder
    # Upon init, no device connected and flags reset
    assert decoder.device is None
    assert decoder.trigger_reopen_device is False
    assert decoder.trigger_restart is False
    assert decoder._monitor_thread is None
    assert decoder._monitor_running is False
    # Default baudrate and mask
    assert decoder._device_baudrate == 115200
    assert decoder._internal_address_mask == 0xFFFFFFFF
    # Services not started yet
    assert decoder.notification_service is None
    assert decoder.backup_service is None

def test_start_starts_monitor_thread_and_services(app_and_decoder, monkeypatch):
    """DecoderService.start launches the monitor thread and sub-services."""
    app, decoder = app_and_decoder
    # Patch threading.Thread to avoid starting a real thread
    started_threads = {}
    class DummyThread:
        def __init__(self, target=None, daemon=None):
            started_threads['target'] = target
            started_threads['daemon'] = daemon
        def start(self):
            started_threads['started'] = True
    monkeypatch.setattr(threading, "Thread", DummyThread)
    # Provide dummy services with start methods
    decoder.notification_service = MagicMock()
    decoder.backup_service = MagicMock()
    decoder._camera_thread = MagicMock()
    decoder._version_thread = MagicMock()
    decoder._discovery_thread = MagicMock()
    decoder._upnp_thread = None  # no UPNP thread for this test
    # Call start()
    decoder.start()
    # Monitor thread created and started
    assert decoder._monitor_thread is not None
    assert decoder._monitor_running is True
    assert started_threads.get('started', False) is True
    # Sub-services start() should be called
    decoder.notification_service.start.assert_called_once()
    decoder.backup_service.start.assert_called_once()
    # Background threads should start if present
    decoder._camera_thread.start.assert_called_once()
    decoder._version_thread.start.assert_called_once()
    decoder._discovery_thread.start.assert_called_once()

def test_start_idempotent_no_duplicate_monitor(app_and_decoder, monkeypatch):
    """Calling start() twice does not spawn a duplicate monitor thread."""
    app, decoder = app_and_decoder
    # Patch Thread to dummy on first call
    monkeypatch.setattr(threading, "Thread", lambda target=None, daemon=None: types.SimpleNamespace(start=lambda: None))
    decoder.start()
    first_thread = decoder._monitor_thread
    # Reset sub-services to None to isolate monitor check
    decoder.notification_service = None
    decoder.backup_service = None
    decoder._camera_thread = None
    decoder._version_thread = None
    decoder._discovery_thread = None
    # Call start() again
    decoder.start()
    # No new thread created (thread object unchanged)
    assert decoder._monitor_thread is first_thread

def test_stop_stops_services_and_threads(app_and_decoder):
    """DecoderService.stop stops all services/threads and closes device."""
    app, decoder = app_and_decoder
    # Attach dummy services and threads with stop/join
    decoder.notification_service = MagicMock()
    decoder.backup_service = MagicMock()
    dummy_cam = MagicMock(); dummy_cam.stop = MagicMock(); dummy_cam.join = MagicMock()
    dummy_ver = MagicMock(); dummy_ver.stop = MagicMock(); dummy_ver.join = MagicMock()
    dummy_disc = MagicMock(); dummy_disc.stop = MagicMock(); dummy_disc.join = MagicMock()
    decoder._camera_thread = dummy_cam
    decoder._version_thread = dummy_ver
    decoder._discovery_thread = dummy_disc
    decoder._upnp_thread = None
    # Attach dummy device to close
    decoder.device = DummyDevice()
    decoder.stop(restart=False)
    # All service stop() methods called
    decoder.notification_service.stop.assert_called_once()
    decoder.backup_service.stop.assert_called_once()
    # All thread stop() called
    dummy_cam.stop.assert_called_once()
    dummy_ver.stop.assert_called_once()
    dummy_disc.stop.assert_called_once()
    # All thread join() called with timeout
    dummy_cam.join.assert_called_once_with(timeout=5)
    dummy_ver.join.assert_called_once_with(timeout=5)
    dummy_disc.join.assert_called_once_with(timeout=5)
    # Device closed and cleared
    decoder.device.close.assert_called_once()
    assert decoder.device is None
    # Websocket stop called
    decoder.websocket.stop.assert_called_once()

def test_stop_with_restart_executes_restart(app_and_decoder, monkeypatch):
    """DecoderService.stop(restart=True) attempts a process restart after cleanup."""
    app, decoder = app_and_decoder
    decoder.notification_service = MagicMock()
    decoder.backup_service = MagicMock()
    decoder.device = DummyDevice()
    monkeypatch.setattr(os, "execv", MagicMock())
    decoder.stop(restart=True)
    os.execv.assert_called_once()
    app.logger.info.assert_any_call('Restarting AlarmDecoder service...')

def test_open_no_device_configured(app_and_decoder, monkeypatch):
    """DecoderService.open does nothing if no device_type configured."""
    app, decoder = app_and_decoder
    monkeypatch.setattr(dec_service.Setting, "get_by_name", lambda name, default=None: DummySetting(None))
    decoder.device = None
    decoder.open()
    # No device opened
    assert decoder.device is None

def test_open_serial_device_success(monkeypatch, app_and_decoder):
    """DecoderService.open opens a serial device with correct parameters."""
    app, decoder = app_and_decoder
    dummy_dev = DummyDevice()
    # Prepare settings for serial device
    settings = {
        'device_type': 'SERIAL',
        'device_location': 'local',
        'device_path': '/dev/ttyS0',
        'device_baudrate': 9600
    }
    def fake_get_by_name(name, default=None):
        val = settings.get(name, None)
        if val is None:
            val = default
        return DummySetting(val)
    monkeypatch.setattr(dec_service.Setting, "get_by_name", staticmethod(fake_get_by_name))
    captured = {}
    class DummySerialDevice:
        def __init__(self, interface):
            captured['serial_interface'] = interface
    class DummySocketDevice:
        def __init__(self, interface):
            captured['socket_interface'] = interface
    monkeypatch.setattr(dec_service, "SerialDevice", DummySerialDevice)
    monkeypatch.setattr(dec_service, "SocketDevice", DummySocketDevice)
    monkeypatch.setattr(dec_service, "AlarmDecoder", lambda dev: dummy_dev)
    decoder.open()
    # SerialDevice should be used with correct interface path
    assert captured.get('serial_interface') == settings['device_path']
    # Device opened with given baud rate and no SSL
    dummy_dev.open.assert_called_once_with(no_reader_thread=False, baudrate=9600, ssl=False)
    # Internal address mask applied
    assert dummy_dev.internal_address_mask == 0xFFFFFFFF

def test_open_serial_default_baudrate(monkeypatch, app_and_decoder):
    """If baudrate setting is missing, DecoderService.open defaults to 115200."""
    app, decoder = app_and_decoder
    dummy_dev = DummyDevice()
    settings = {
        'device_type': 'SERIAL',
        'device_location': 'local',
        'device_path': '/dev/ttyUSB0',
        'device_baudrate': None
    }
    def fake_get(name, default=None):
        val = settings.get(name, None)
        if val is None:
            val = default
        return DummySetting(val)
    monkeypatch.setattr(dec_service.Setting, "get_by_name", staticmethod(fake_get))
    monkeypatch.setattr(dec_service, "SerialDevice", lambda interface: "SERIAL_IFACE")
    monkeypatch.setattr(dec_service, "AlarmDecoder", lambda dev: dummy_dev)
    decoder.open()
    dummy_dev.open.assert_called_once_with(no_reader_thread=False, baudrate=115200, ssl=False)

def test_open_network_device_success(monkeypatch, app_and_decoder):
    """DecoderService.open opens a network device with correct host/port and SSL."""
    app, decoder = app_and_decoder
    dummy_dev = DummyDevice()
    settings = {
        'device_type': 'NET',
        'device_location': 'network',
        'device_address': '192.168.1.100',
        'device_port': 12345,
        'use_ssl': True
    }
    def fake_get(name, default=None):
        val = settings.get(name, None)
        if val is None:
            val = default
        return DummySetting(val)
    monkeypatch.setattr(dec_service.Setting, "get_by_name", staticmethod(fake_get))
    captured = {}
    class DummySocketDevice:
        def __init__(self, interface):
            captured['socket_iface'] = interface
    monkeypatch.setattr(dec_service, "SocketDevice", DummySocketDevice)
    monkeypatch.setattr(dec_service, "SerialDevice", lambda iface: None)
    monkeypatch.setattr(dec_service, "AlarmDecoder", lambda dev: dummy_dev)
    decoder.open()
    # SocketDevice should be used with correct address/port and SSL
    assert captured.get('socket_iface') == (settings['device_address'], settings['device_port'])
    dummy_dev.open.assert_called_once_with(no_reader_thread=False, baudrate=115200, ssl=True)

def test_open_device_not_found(monkeypatch, app_and_decoder):
    """If AlarmDecoder raises NoDeviceError, device remains None and error is logged."""
    app, decoder = app_and_decoder
    monkeypatch.setattr(dec_service.Setting, "get_by_name", lambda name, default=None: DummySetting('SERIAL') if name == 'device_type' else DummySetting('local'))
    monkeypatch.setattr(dec_service, "SerialDevice", lambda iface: "IFACE")
    def raise_no_device(dev):
        raise dec_service.NoDeviceError("Not found")
    monkeypatch.setattr(dec_service, "AlarmDecoder", raise_no_device)
    decoder.device = None
    decoder.open()
    assert decoder.device is None
    app.logger.error.assert_any_call(pytest.helpers.contains("device not found or inaccessible"),)

def test_open_device_exception(monkeypatch, app_and_decoder):
    """If AlarmDecoder raises generic Exception, it is handled and device remains None."""
    app, decoder = app_and_decoder
    monkeypatch.setattr(dec_service.Setting, "get_by_name", lambda name, default=None: DummySetting('SERIAL') if name == 'device_type' else DummySetting('local'))
    monkeypatch.setattr(dec_service, "SerialDevice", lambda iface: "IFACE")
    def raise_general(dev):
        raise Exception("Open failure")
    monkeypatch.setattr(dec_service, "AlarmDecoder", raise_general)
    decoder.device = None
    decoder.open()
    assert decoder.device is None
    app.logger.error.assert_any_call(pytest.helpers.contains("Error opening AlarmDecoder device"), exc_info=True)

def test_monitor_loop_reopens_device(monkeypatch, app_and_decoder):
    """_monitor_loop triggers device reopen when flag is set."""
    app, decoder = app_and_decoder
    decoder._monitor_running = True
    decoder.trigger_reopen_device = True
    opened = {"called": False}
    decoder.open = lambda: opened.__setitem__("called", True)
    monkeypatch.setattr(time, "sleep", lambda s: (_ for _ in ()).throw(StopIteration()))
    with pytest.raises(StopIteration):
        decoder._monitor_loop()
    assert opened["called"] is True
    assert decoder.trigger_reopen_device is False

def test_monitor_loop_restart_triggers_stop(monkeypatch, app_and_decoder):
    """_monitor_loop triggers service restart when flag is set."""
    app, decoder = app_and_decoder
    decoder._monitor_running = True
    decoder.trigger_restart = True
    decoder.updates = {"dummy": "data"}
    app.jinja_env.globals['update_available'] = True
    decoder.stop = MagicMock()
    decoder._monitor_loop()
    assert decoder.trigger_restart is False
    assert decoder.updates == {}
    assert app.jinja_env.globals['update_available'] is False
    decoder.stop.assert_called_once_with(restart=True)

def test_monitor_loop_logs_exceptions(monkeypatch, app_and_decoder):
    """Exceptions in _monitor_loop are caught and logged."""
    app, decoder = app_and_decoder
    decoder._monitor_running = True
    decoder.trigger_reopen_device = True
    decoder.open = lambda: (_ for _ in ()).throw(Exception("Failure in reopen"))
    monkeypatch.setattr(time, "sleep", lambda s: (_ for _ in ()).throw(StopIteration()))
    with pytest.raises(StopIteration):
        decoder._monitor_loop()
    app.logger.error.assert_any_call(pytest.helpers.contains("Error in DecoderService monitor loop"), exc_info=True)

def test_handle_device_message_broadcasts(app_and_decoder):
    """_handle_device_message broadcasts panel message and stores last message."""
    app, decoder = app_and_decoder
    decoder.broadcast = MagicMock()
    msg = "TEST MESSAGE"
    decoder._handle_device_message(sender=None, message=msg)
    assert decoder.last_message_received == msg
    decoder.broadcast.assert_called_once_with('message', {'message': msg, 'message_type': 'panel'})

def test_handle_device_event_broadcasts_and_notifies(monkeypatch, app_and_decoder):
    """_handle_device_event sends notification and broadcasts event data."""
    app, decoder = app_and_decoder
    decoder.notification_service = MagicMock()
    decoder.broadcast = MagicMock()
    monkeypatch.setattr(time, "time", lambda: 123456.0)
    decoder._handle_device_event('ALARM', zone=5)
    assert decoder._last_message_time == 123456.0
    decoder.notification_service.notify_event.assert_called_once_with('ALARM', zone=5)
    decoder.broadcast.assert_called_once_with('event', {'zone': 5})

def test_handle_device_event_without_notification(app_and_decoder):
    """_handle_device_event still broadcasts without notification service."""
    app, decoder = app_and_decoder
    decoder.notification_service = None
    decoder.broadcast = MagicMock()
    decoder._handle_device_event('TEST_EVENT')
    decoder.broadcast.assert_called_once_with('event', {})
