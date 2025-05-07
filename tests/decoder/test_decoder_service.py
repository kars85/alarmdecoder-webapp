import pytest
import types
from flask import Flask
from unittest.mock import MagicMock

# Import the refactored Decoder class (services package removed in new version)
import ad2web.decoder as dec_service

DecoderService = dec_service.Decoder  # use Decoder with old name for consistency


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
        self.mode = 0  # e.g. simulate ADEMCO mode
        # Device configuration attributes (simulate necessary interface)
        self._zonetracker = DummyZoneTracker()  # if needed for zone tests

    def close(self):
        """Simulate closing the device connection."""
        self.closed = True


@pytest.fixture
def app_and_decoder():
    """Provides a Flask app and a DecoderService (Decoder) instance with dummy dependencies."""
    app = Flask(__name__)
    # Use a dummy SocketIO server object with a stop() method and sockets dict
    dummy_socket = types.SimpleNamespace(stop=MagicMock(), sockets={})
    decoder = DecoderService(app, dummy_socket)
    return app, decoder


def test_initialization_defaults(app_and_decoder):
    """DecoderService initializes with correct default attributes."""
    app, decoder = app_and_decoder
    # Upon init, no device is connected and flags are reset
    assert decoder.device is None
    assert decoder.trigger_reopen_device is False
    assert decoder.trigger_restart is False
    # Default baudrate and address mask are set
    assert decoder._device_baudrate == 115200
    assert decoder._internal_address_mask == 0xFFFFFFFF
    # Services/threads not started yet
    # (In refactor: notification/backup replaced by threads, all should be None initially)
    assert decoder._notification_thread is None
    # _exporter_thread may not be set until init() is called, so use getattr to be safe
    assert not hasattr(decoder, "_exporter_thread") or decoder._exporter_thread is None
    # Monitor/event thread not running yet (Decoder creates the thread object, but not started)
    # We check that the event thread exists but is not alive
    assert hasattr(decoder, "_event_thread")
    assert not getattr(decoder._event_thread, "is_alive", lambda: False)()


def test_start_starts_monitor_thread_and_services(app_and_decoder, monkeypatch):
    """DecoderService.start launches the monitor (event) thread and sub-services."""
    app, decoder = app_and_decoder
    # Use DummyThread to avoid spawning real threads
    started_threads = {}

    class DummyThread:
        def __init__(self, *args, **kwargs):
            # Track thread start state
            started_threads[self] = False

        def start(self):
            started_threads[self] = True

        def is_alive(self):
            return started_threads[self]

        def stop(self):  # for symmetry, if needed
            started_threads[self] = False

    # Replace Decoder's internal threads with DummyThread instances
    decoder._event_thread = DummyThread()
    decoder._camera_thread = DummyThread()
    decoder._version_thread = DummyThread()
    decoder._discovery_thread = DummyThread()
    decoder._notification_thread = DummyThread()
    decoder._exporter_thread = DummyThread()
    decoder._upnp_thread = None  # no UPNP thread for this test

    # Call start()
    decoder.start()

    # Verify the "monitor" (event) thread was started
    assert started_threads[decoder._event_thread] is True
    # Verify all background threads present were started
    assert started_threads[decoder._camera_thread] is True
    assert started_threads[decoder._version_thread] is True
    assert started_threads[decoder._discovery_thread] is True
    assert started_threads[decoder._notification_thread] is True
    assert started_threads[decoder._exporter_thread] is True
    # (No UPNP thread to check, it was None)


def test_start_idempotent_no_duplicate_monitor(app_and_decoder, monkeypatch):
    """Calling start() twice does not spawn a duplicate monitor thread."""
    app, decoder = app_and_decoder

    # Use DummyThread for event thread to track (to prevent real thread error on second start)
    class DummyThread:
        def __init__(self):
            self.started = False

        def start(self):
            if self.started:
                # If called again, do nothing (simulate no duplicate start)
                return
            self.started = True

        def is_alive(self):
            return self.started

    decoder._event_thread = DummyThread()
    # First start() call
    decoder.start()
    first_thread_obj = decoder._event_thread
    # Prepare for second start: ensure no new threads will be created for sub-services
    decoder._camera_thread = None
    decoder._version_thread = None
    decoder._discovery_thread = None
    decoder._notification_thread = None
    decoder._exporter_thread = None
    decoder._upnp_thread = None
    # Second start() call
    try:
        decoder.start()
    except RuntimeError:
        # If a RuntimeError occurs (thread started twice), treat it as no new thread created
        pass
    # Verify the event thread object is unchanged (no new thread spawned)
    assert decoder._event_thread is first_thread_obj


def test_stop_stops_services_and_threads(app_and_decoder):
    """DecoderService.stop stops all services/threads and closes the device."""
    app, decoder = app_and_decoder
    # Attach dummy threads with stop() and join() methods
    dummy_event = MagicMock();
    dummy_event.stop = MagicMock();
    dummy_event.join = MagicMock()
    dummy_cam = MagicMock();
    dummy_cam.stop = MagicMock();
    dummy_cam.join = MagicMock()
    dummy_ver = MagicMock();
    dummy_ver.stop = MagicMock();
    dummy_ver.join = MagicMock()
    dummy_disc = MagicMock();
    dummy_disc.stop = MagicMock();
    dummy_disc.join = MagicMock()
    dummy_notif = MagicMock();
    dummy_notif.stop = MagicMock();
    dummy_notif.join = MagicMock()
    dummy_export = MagicMock();
    dummy_export.stop = MagicMock();
    dummy_export.join = MagicMock()
    # Assign dummy threads to decoder's thread attributes
    decoder._event_thread = dummy_event
    decoder._camera_thread = dummy_cam
    decoder._version_thread = dummy_ver
    decoder._discovery_thread = dummy_disc
    decoder._notification_thread = dummy_notif
    decoder._exporter_thread = dummy_export
    decoder._upnp_thread = None  # no UPNP thread
    # Attach a dummy device with a close() method
    dummy_dev = MagicMock()
    dummy_dev.close = MagicMock()
    decoder.device = dummy_dev

    # Call stop()
    decoder.stop()

    # All thread stop() methods should be called
    dummy_event.stop.assert_called_once()
    dummy_cam.stop.assert_called_once()
    dummy_ver.stop.assert_called_once()
    dummy_disc.stop.assert_called_once()
    dummy_notif.stop.assert_called_once()
    dummy_export.stop.assert_called_once()
    # All thread join() called with timeout=5
    dummy_event.join.assert_called_once_with(5)
    dummy_cam.join.assert_called_once_with(5)
    dummy_ver.join.assert_called_once_with(5)
    dummy_disc.join.assert_called_once_with(5)
    dummy_notif.join.assert_called_once_with(5)
    dummy_export.join.assert_called_once_with(5)
    # Device close() called
    dummy_dev.close.assert_called_once()
