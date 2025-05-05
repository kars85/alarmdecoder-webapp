# File: tests/test_discovery.py
import builtins
import socket
import struct
import uuid
import types
import pytest

# Import the DiscoveryServer and DiscoveryRequest classes from the discovery module
# (assuming the package name is ad2web as in the repository structure).
from ad2web import discovery

# Dummy socket class to simulate a UDP socket without real network activity.
class DummySocket:
    last_instance = None      # track the last created instance
    def __init__(self, *args, **kwargs):
        DummySocket.last_instance = self
        self.options = {}          # store setsockopt calls (keyed by (level,optname))
        self.bound_addr = None
        self.sent_messages = []    # store messages sent via sendto
    def setsockopt(self, level, optname, value):
        # Record socket options set (e.g. reuse address, multicast membership)
        self.options[(level, optname)] = value
    def bind(self, addr):
        # Record bind address (multicast group and port)
        self.bound_addr = addr
    def sendto(self, message, addr):
        # Record sent message and destination
        self.sent_messages.append((message, addr))
    def recvfrom(self, bufsize):
        # Not used directly in these tests (we patch select to simulate incoming data)
        return (b"", ("0.0.0.0", 0))
    def close(self):
        pass  # not explicitly used in discovery code

def make_ssdp_request(st_header: str) -> bytes:
    """Utility to build a raw SSDP M-SEARCH request payload with given ST header value."""
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        f"ST: {st_header}\r\n"
        "MX: 1\r\n"
        "\r\n"
    ).encode("ascii")

def create_discovery_server(monkeypatch) -> discovery.DiscoveryServer:
    """Helper to instantiate DiscoveryServer with all external effects patched out."""
    # Patch socket.socket to use DummySocket, avoiding real UDP socket usage:contentReference[oaicite:1]{index=1}.
    monkeypatch.setattr(discovery.socket, "socket", DummySocket)
    # Patch _get_ip_address to return a deterministic IP (no real netifaces needed).
    monkeypatch.setattr(discovery.DiscoveryServer, "_get_ip_address", lambda self: "1.2.3.4")
    # Patch _get_device_uuid to avoid DB access and return a fixed UUID.
    monkeypatch.setattr(discovery.DiscoveryServer, "_get_device_uuid", lambda self: "1234-uuid")
    # Now create a DiscoveryServer with a dummy decoder object that has the required interface.
    class DummyApp:
        def __init__(self):
            self.logger = types.SimpleNamespace( info=lambda *args, **kwargs: None,
                                                 debug=lambda *args, **kwargs: None,
                                                 warning=lambda *args, **kwargs: None,
                                                 error=lambda *args, **kwargs: None )
        def app_context(self):
            # Context manager that does nothing (for `with app.app_context():` usage)
            return types.SimpleNamespace(__enter__=lambda self: None, __exit__=lambda self,*args: None)
    dummy_decoder = types.SimpleNamespace(app=DummyApp())
    server = discovery.DiscoveryServer(dummy_decoder)
    return server

def test_discovery_request_parsing_valid(monkeypatch):
    """A valid SSDP M-SEARCH request should parse into the correct command, path, and headers with no errors."""
    data = make_ssdp_request("ssdp:all")
    req = discovery.DiscoveryRequest(data)
    # The request should be parsed as an SSDP M-SEARCH with correct path and headers.
    assert req.command == "M-SEARCH"
    assert req.path == "*"
    # Headers should include the ST and MAN fields as provided (case-insensitive access).
    assert req.headers["ST"] == "ssdp:all"
    assert req.headers["MAN"] == "\"ssdp:discover\""
    # No parsing errors should be present.
    assert req.error_code is None and req.error_message is None

def test_discovery_request_parsing_invalid():
    """An invalid SSDP request (e.g. malformed headers) should result in an error code and message."""
    # Create a malformed request (missing value after ST:)
    bad_request = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        "ST\r\n"      # malformed header line
        "\r\n"
    ).encode("ascii")
    req = discovery.DiscoveryRequest(bad_request)
    # BaseHTTPRequestHandler should flag this as a bad request.
    assert req.error_code == 400  # HTTP 400 Bad Request
    assert "Bad request syntax" in req.error_message or "Invalid header line" in req.error_message

@pytest.mark.parametrize("st_val, expected", [
    ("ssdp:all", True),
    ("urn:schemas-upnp-org:device:AlarmDecoder:1", True),
    ("upnp:rootdevice", False),   # does not contain "AlarmDecoder" or "ssdp:all"
    ("some:other:service", False) # unrelated ST value
])
def test_match_search_request(monkeypatch, st_val, expected):
    """_match_search_request should correctly identify valid discovery search requests by ST value."""
    data = make_ssdp_request(st_val)
    req = discovery.DiscoveryRequest(data)
    server = create_discovery_server(monkeypatch)
    # Monkeypatch server._match_search_request if needed (it's an instance method; we'll just call it)
    result = server._match_search_request(req)
    assert result is expected, f"Expected match_search_request({st_val}) == {expected}"

def test_handle_request_no_response_on_error(monkeypatch):
    """If DiscoveryRequest has a parsing error, _handle_request should not attempt to respond."""
    server = create_discovery_server(monkeypatch)
    # Create a request object with an error (simulate parse error).
    err_req = types.SimpleNamespace(error_code=400, error_message="Bad Request")
    # Patch server._send_message to ensure it's not called (would raise if invoked).
    called = False
    def dummy_send(self, msg, addr):
        nonlocal called
        called = True
    monkeypatch.setattr(discovery.DiscoveryServer, "_send_message", dummy_send)
    server._handle_request(err_req, ("192.168.0.50", 1900))
    # Since request had an error, _send_message should NOT have been called.
    assert called is False

def test_handle_request_sends_response(monkeypatch):
    """A valid discovery M-SEARCH request should trigger sending a discovery response."""
    server = create_discovery_server(monkeypatch)
    # Prepare a valid discovery request (ST includes "AlarmDecoder" to satisfy match).
    data = make_ssdp_request("urn:schemas-upnp-org:device:AlarmDecoder:1")
    req = discovery.DiscoveryRequest(data)
    assert server._match_search_request(req)  # sanity check that it matches
    sent = []  # to capture message and address
    def fake_send(self, msg, addr):
        sent.append((msg, addr))
    monkeypatch.setattr(discovery.DiscoveryServer, "_send_message", fake_send)
    server._handle_request(req, ("192.168.0.100", 1900))
    # A response should have been sent exactly once
    assert len(sent) == 1
    resp_msg, dest = sent[0]
    # Destination should be the address passed in
    assert dest == ("192.168.0.100", 1900)
    # Response message should be an HTTP 200 OK with matching ST and the server's USN/LOCATION.
    assert resp_msg.startswith("HTTP/1.1 200 OK"), "Response not starting with HTTP 200 OK"
    assert "ST: urn:schemas-upnp-org:device:AlarmDecoder:1" in resp_msg
    assert "USN: uuid:1234-uuid" in resp_msg  # uses server's device UUID
    # LOCATION should point to the device description URL with current IP and port
    assert f"LOCATION: http://{server._current_ip_address}:{server._current_port}/static/device_description.xml" in resp_msg

def test_discovery_response_format(monkeypatch):
    """_create_discovery_response should incorporate the correct ST, USN, and LOCATION in the message."""
    server = create_discovery_server(monkeypatch)
    # Simulate a request with a specific ST header.
    data = make_ssdp_request("ssdp:all")
    req = discovery.DiscoveryRequest(data)
    resp = server._create_discovery_response(req)
    # The response should be a single HTTP response string with expected fields.
    assert resp.startswith("HTTP/1.1 200 OK")
    # It should contain the same ST as in the request
    assert "\r\nST: ssdp:all\r\n" in resp
    # The USN should contain the UUID of the device
    assert f"\r\nUSN: uuid:{server._device_uuid}" in resp
    # LOCATION should point to the device description XML on the current host
    expected_loc = f"http://{server._current_ip_address}:{server._current_port}/static/device_description.xml"
    assert f"\r\nLOCATION: {expected_loc}\r\n" in resp

def test_notify_message_contents(monkeypatch):
    """_create_notify_message should return three NOTIFY messages with correct NT, NTS, and USN fields."""
    server = create_discovery_server(monkeypatch)
    notifications = server._create_notify_message()
    # Expect three notify messages (as a tuple)
    assert isinstance(notifications, tuple) and len(notifications) == 3
    msg1, msg2, msg3 = notifications
    # All messages should start with the SSDP NOTIFY request line
    assert msg1.startswith("NOTIFY * HTTP/1.1")
    assert msg2.startswith("NOTIFY * HTTP/1.1")
    assert msg3.startswith("NOTIFY * HTTP/1.1")
    # The first message should advertise the root device
    assert "NT: upnp:rootdevice" in msg1 and "NTS: ssdp:alive" in msg1
    assert f"USN: uuid:{server._device_uuid}::upnp:rootdevice" in msg1
    # The second message should advertise the UUID itself
    assert f"NT: uuid:{server._device_uuid}" in msg2 and "NTS: ssdp:alive" in msg2
    assert f"USN: uuid:{server._device_uuid}" in msg2 and "::" not in msg2  # no :: suffix in second USN
    # The third message should advertise the device type (AlarmDecoder)
    assert "NT: urn:schemas-upnp-org:device:AlarmDecoder:1" in msg3
    assert "NTS: ssdp:alive" in msg3
    assert f"USN: uuid:{server._device_uuid}::urn:schemas-upnp-org:device:AlarmDecoder:1" in msg3

def test_send_message_sends_twice(monkeypatch):
    """_send_message should send the message twice (as UDP is unreliable) and use the stored socket."""
    server = create_discovery_server(monkeypatch)
    dummy_sock = DummySocket.last_instance
    assert dummy_sock is not None, "DummySocket should have been used for DiscoveryServer"
    # Clear any prior sends
    dummy_sock.sent_messages.clear()
    # Patch time.sleep to avoid actual delay.
    monkeypatch.setattr(discovery.time, "sleep", lambda t: None)
    # Invoke _send_message
    test_msg = "TEST MESSAGE"
    test_addr = ("239.255.255.250", 1900)
    server._send_message(test_msg, test_addr)
    # The DummySocket should have recorded exactly two sendto calls with identical message and addr
    assert len(dummy_sock.sent_messages) == 2
    assert dummy_sock.sent_messages[0] == (test_msg, test_addr)
    assert dummy_sock.sent_messages[1] == (test_msg, test_addr)

def test_socket_configuration_on_init(monkeypatch):
    """DiscoveryServer.__init__ should set up the UDP socket with correct multicast options and binding."""
    # Create server with dummy socket to intercept socket configuration
    server = create_discovery_server(monkeypatch)
    sock = DummySocket.last_instance
    # The socket should be bound to the multicast group address and port
    expected_addr = (discovery.DiscoveryServer.MCAST_ADDRESS, discovery.DiscoveryServer.MCAST_PORT)
    assert sock.bound_addr == expected_addr
    # It should have set SO_REUSEADDR on the socket
    key1 = (socket.SOL_SOCKET, socket.SO_REUSEADDR)
    assert key1 in sock.options and sock.options[key1] == 1
    # It should have set IP_MULTICAST_TTL (to 2 as per code)
    key2 = (socket.IPPROTO_IP, socket.IP_MULTICAST_TTL)
    assert key2 in sock.options and sock.options[key2] == 2
    # It should have joined the multicast group (IP_ADD_MEMBERSHIP)
    key3 = (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)
    assert key3 in sock.options
    # The membership request should contain the multicast address and INADDR_ANY
    mreq_bytes = sock.options[key3]
    expected_mreq = struct.pack("4sl", socket.inet_aton(discovery.DiscoveryServer.MCAST_ADDRESS), socket.INADDR_ANY)
    assert mreq_bytes == expected_mreq

def test_get_device_uuid_uses_existing(monkeypatch):
    """If a device_uuid exists in the DB, _get_device_uuid should return it without generating a new one."""
    server = create_discovery_server(monkeypatch)
    # Monkeypatch Setting.get_by_name to simulate an existing UUID in DB
    class DummySetting:
        def __init__(self, val): self.value = val
    monkeypatch.setattr(discovery, "Setting", types.SimpleNamespace(get_by_name=lambda name, default=None: DummySetting("existing-uuid")))
    # Monkeypatch the database session to capture any commit attempts
    committed = False
    monkeypatch.setattr(discovery.db.session, "commit", lambda: builtins.exec("nonlocal_committed := True"), raising=False)
    nonlocal_committed = False  # flag to detect commit call (using exec to assign in lambda above)
    result = server._get_device_uuid()
    # Should return the existing UUID and not generate a new one
    assert result == "existing-uuid"
    # Ensure no new Setting was added to the session (commit should not be called for existing value)
    assert nonlocal_committed is False, "DB commit should not occur when UUID exists"

def test_get_device_uuid_generates_and_saves(monkeypatch):
    """If no device_uuid exists, _get_device_uuid should generate a new one and store it via the DB session."""
    server = create_discovery_server(monkeypatch)
    # Simulate no existing UUID: get_by_name returns object with empty value
    class DummySetting:
        def __init__(self, name): self.name = name; self.value = None
        # Simulate DB model behavior for new setting
    dummy_setting_instance = None
    def fake_get_by_name(name, default=None):
        # Return a DummySetting with no value to trigger generation
        return DummySetting(name)
    monkeypatch.setattr(discovery, "Setting", types.SimpleNamespace(get_by_name=fake_get_by_name))
    # Patch uuid.uuid1 to return a fixed UUID value
    monkeypatch.setattr(uuid, "uuid1", lambda: uuid.UUID("12345678-1234-1234-1234-123456789012"))
    # Set up dummy DB session to track adds and commits
    added_obj = None
    committed = False
    def fake_add(obj):
        nonlocal added_obj
        added_obj = obj
    def fake_commit():
        nonlocal committed
        committed = True
    monkeypatch.setattr(discovery.db, "session", types.SimpleNamespace(add=fake_add, commit=fake_commit))
    new_uuid = server._get_device_uuid()
    # The returned UUID should match the fake generated UUID
    assert new_uuid == "12345678-1234-1234-1234-123456789012"
    # A new Setting object should have been added to the session
    assert added_obj is not None and hasattr(added_obj, "name") and added_obj.name == "device_uuid"
    assert getattr(added_obj, "value", None) == new_uuid
    # The session commit should have been called to save the new UUID
    assert committed is True

def test_get_ip_address_with_netifaces(monkeypatch):
    """_get_ip_address should return the address of the default interface when netifaces is available."""
    server = create_discovery_server(monkeypatch)
    # Force has_netifaces to True and provide a dummy netifaces module
    monkeypatch.setattr(discovery, "has_netifaces", True)
    # Create dummy netifaces with expected interface data
    class DummyNetifaces:
        AF_INET = socket.AF_INET
        def gateways(self):
            # Simulate a default gateway entry (gateway IP, interface name)
            return {"default": {DummyNetifaces.AF_INET: ("10.0.0.1", "eth0")}}
        def ifaddresses(self, ifname):
            # Simulate interface addresses for eth0
            if ifname == "eth0":
                return {DummyNetifaces.AF_INET: [ {"addr": "10.0.0.42"} ]}
            return {}
    monkeypatch.setitem(sys.modules, "netifaces", DummyNetifaces())  # insert dummy module
    # Now call _get_ip_address; it should use DummyNetifaces and return the 'addr'
    ip = server._get_ip_address()
    assert ip == "10.0.0.42", "Should return the IPv4 address of the default gateway interface"

def test_get_ip_address_fallback(monkeypatch):
    """_get_ip_address should fall back to socket ioctl if netifaces is unavailable."""
    server = create_discovery_server(monkeypatch)
    # Ensure has_netifaces is False (simulate no netifaces)
    monkeypatch.setattr(discovery, "has_netifaces", False)
    # Patch socket and fcntl to simulate getting an IP via ioctl
    dummy_ip = "192.168.99.50"
    def fake_ioctl(fd, request, arg):
        # Return 256 bytes with bytes [20:24] as the IP address in network byte order
        return b'\x00' * 20 + socket.inet_aton(dummy_ip) + b'\x00' * 232
    monkeypatch.setattr(discovery.fcntl, "ioctl", fake_ioctl)
    # Now call _get_ip_address; it should use the fallback path
    ip = server._get_ip_address()
    assert ip == dummy_ip
