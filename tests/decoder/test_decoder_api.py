import types
import pytest
from flask import Flask
from unittest.mock import MagicMock

from ad2web import create_app
import ad2web.api as api_module

class DummyUser:
    def __init__(self, admin=True):
        self.id = 1
        self.role_code = 0 if admin else 1

class DummyAPIKey:
    def __init__(self, user_id):
        self.id = 1
        self.user_id = user_id
        self.key = "dummykey"

class DummyQuery:
    def __init__(self, result):
        self._result = result
    def first(self):
        return self._result

class DummyNotifierSystem:
    def __init__(self):
        self.add_calls = []
        self.remove_calls = []
    def add_subscriber(self, host, callback, timeout):
        self.add_calls.append((host, callback, timeout))
        return "sub-1234"
    def remove_subscriber(self, host, sid):
        self.remove_calls.append((host, sid))

@pytest.fixture(scope="module")
def test_app(monkeypatch):
    """Create Flask test app with API routes and dummy decoder/device."""
    # Prevent starting threads or external services
    monkeypatch.setattr(api_module, "init_discovery", lambda app: None, raising=False)
    monkeypatch.setattr(api_module, "start_discovery", lambda: None, raising=False)
    import services.decoder_service as dec_service
    monkeypatch.setattr(dec_service.DecoderService, "init", lambda self: None)
    monkeypatch.setattr(dec_service.DecoderService, "start", lambda self: None)
    monkeypatch.setattr(dec_service, "Updater", lambda *args, **kwargs: types.SimpleNamespace(_components={}))
    # Stub out API key authentication queries
    dummy_user = DummyUser(admin=True)
    monkeypatch.setattr(api_module.APIKey, "query", types.SimpleNamespace(filter_by=lambda **kwargs: DummyQuery(DummyAPIKey(user_id=dummy_user.id))))
    monkeypatch.setattr(api_module.User, "query", types.SimpleNamespace(filter_by=lambda **kwargs: DummyQuery(dummy_user)))
    result = create_app()
    app = result[0] if isinstance(result, tuple) else result
    app.testing = True
    # Attach dummy decoder and device to app
    from services.decoder_service import DecoderService
    dummy_socket = types.SimpleNamespace(stop=lambda: None, sockets={})
    decoder = DecoderService(app, dummy_socket)
    decoder.device = types.SimpleNamespace(**{
        "mode": 0,
        "_relay_status": {},
        "_zonetracker": types.SimpleNamespace(zones={}),
        "_power_status": True,
        "_ready_status": True,
        "_alarm_status": False,
        "_bypass_status": False,
        "_armed_status": False,
        "_armed_stay": False,
        "_fire_status": False,
        "_battery_status": (False,),
        "_panic_status": False,
        "_chime_status": False,
        "_perimeter_only_status": False,
        "_entry_delay_off_status": False,
        "_exit": False
    })
    decoder.last_message_received = ""
    decoder._notifier_system = DummyNotifierSystem()
    app.decoder = decoder
    return app

@pytest.fixture()
def client(test_app):
    """Provide a Flask test client for the app."""
    return test_app.test_client()

AUTH_HEADERS = {"Authorization": "dummykey"}

def test_get_alarmdecoder_status(client):
    """GET /alarmdecoder returns panel status JSON."""
    resp = client.get("/api/v1/alarmdecoder", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    # Basic panel status fields
    assert data["panel_powered"] is True
    assert data["panel_ready"] is True
    assert data["panel_armed"] is False
    assert data["panel_battery_trouble"] is False
    assert isinstance(data["panel_type"], str)
    assert data["panel_type"] in ("ADEMCO", "DSC", "UNKNOWN")
    assert data["panel_zones_faulted"] == []
    assert data["last_message_received"] == ""

def test_post_alarmdecoder_send_success(client, test_app):
    """POST /alarmdecoder/send should send keys to the panel and return 204."""
    dummy_device = test_app.decoder.device
    dummy_device.send = MagicMock()
    payload = {"keys": "<F1>1234"}
    resp = client.post("/api/v1/alarmdecoder/send", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 204
    dummy_device.send.assert_called_once()
    sent_keys = dummy_device.send.call_args[0][0]
    assert "<" not in sent_keys  # special tokens replaced

def test_post_alarmdecoder_send_missing_keys(client):
    """POST /alarmdecoder/send without 'keys' field returns 422."""
    resp = client.post("/api/v1/alarmdecoder/send", json={}, headers=AUTH_HEADERS)
    assert resp.status_code == 422
    data = resp.get_json()
    assert "error" in data and "Missing 'keys' in request." in data["error"]["message"]

def test_subscribe_event_notifications(client, test_app):
    """SUBSCRIBE on /alarmdecoder/event registers a new subscriber."""
    dummy_notifier = test_app.decoder._notifier_system
    headers = {
        "HOST": "example.com",
        "CALLBACK": "<http://client/callback>",
        "TIMEOUT": "Second-1800",
        **AUTH_HEADERS
    }
    resp = client.open("/api/v1/alarmdecoder/event", method="SUBSCRIBE", headers=headers)
    assert resp.status_code == 200
    # Check response headers
    assert resp.headers.get("SID", "").startswith("uuid:sub-")
    assert resp.headers.get("TIMEOUT") == "Second-1800"
    assert resp.headers.get("SERVER") == "Linux UPnP/1.0 AlarmDecoder"
    assert resp.headers.get("X-User-Agent") == "AD2WEB"
    # Notifier system should have recorded the subscription
    assert dummy_notifier.add_calls, "add_subscriber was not called"
    host, callback, timeout = dummy_notifier.add_calls[-1]
    assert host == "example.com"
    assert callback == "<http://client/callback>"
    assert timeout == "Second-1800"

def test_unsubscribe_event_notifications(client, test_app):
    """UNSUBSCRIBE on /alarmdecoder/event removes an existing subscriber."""
    dummy_notifier = test_app.decoder._notifier_system
    headers = {
        "HOST": "example.com",
        "SID": "uuid:sub-1234",
        **AUTH_HEADERS
    }
    resp = client.open("/api/v1/alarmdecoder/event", method="UNSUBSCRIBE", headers=headers)
    assert resp.status_code == 200
    assert dummy_notifier.remove_calls, "remove_subscriber was not called"
    host, sid = dummy_notifier.remove_calls[-1]
    assert host == "example.com"
    assert sid == "uuid:sub-1234"
    assert resp.headers.get("SID") == "uuid:sub-1234"

def test_reboot_device_unauthorized(monkeypatch, client):
    """POST /alarmdecoder/reboot with non-admin user returns 401."""
    monkeypatch.setattr(api_module, "check_admin", lambda user: False)
    resp = client.post("/api/v1/alarmdecoder/reboot", headers=AUTH_HEADERS)
    assert resp.status_code == 401
    data = resp.get_json()
    assert "error" in data and data["error"]["code"] != 200
    monkeypatch.setattr(api_module, "check_admin", lambda user: True)

def test_reboot_device_success(client, test_app):
    """POST /alarmdecoder/reboot as admin triggers device reboot."""
    dummy_device = test_app.decoder.device
    dummy_device.reboot = MagicMock()
    resp = client.post("/api/v1/alarmdecoder/reboot", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    dummy_device.reboot.assert_called_once()

def test_get_device_configuration(client):
    """GET /alarmdecoder/configuration returns current device config."""
    resp = client.get("/api/v1/alarmdecoder/configuration", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    # Config fields should match dummy device
    assert data["address"] == 1
    assert data["config_bits"] == 0
    assert data["emulate_zone"] is False
    assert isinstance(data["mode"], str)
    assert data["mode"] in ("ADEMCO", "DSC", "UNKNOWN")

def test_put_device_configuration_unauthorized(monkeypatch, client):
    """PUT /alarmdecoder/configuration by non-admin returns 401."""
    monkeypatch.setattr(api_module, "check_admin", lambda user: False)
    resp = client.put("/api/v1/alarmdecoder/configuration", json={"address": 2}, headers=AUTH_HEADERS)
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["error"]["code"] != 200
    monkeypatch.setattr(api_module, "check_admin", lambda user: True)

def test_put_device_configuration_invalid_mode(client):
    """PUT /alarmdecoder/configuration with invalid mode returns 422."""
    payload = {"mode": "INVALID"}
    resp = client.put("/api/v1/alarmdecoder/configuration", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 422
    data = resp.get_json()
    assert "Invalid value for 'mode'." in data["error"]["message"]

def test_put_device_configuration_success(client, test_app):
    """PUT /alarmdecoder/configuration as admin updates device settings."""
    dummy_device = test_app.decoder.device
    dummy_device.save_config = MagicMock()
    new_config = {
        "address": 5,
        "config_bits": 7,
        "emulate_zone": True,
        "mode": "DSC"
    }
    resp = client.put("/api/v1/alarmdecoder/configuration", json=new_config, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.get_json()
    # Device attributes updated
    assert dummy_device.address == 5
    assert dummy_device.configbits == 7
    assert dummy_device.emulate_zone is True
    assert data["mode"] == "DSC"
    dummy_device.save_config.assert_called_once()
