# Import necessary modules and the discovery module
import socket
import struct
import select
import types
import ad2web.discovery as discovery

# Dummy socket class to simulate a UDP socket without real network I/O
class DummySocket:
    last_instance = None
    instances = []
    def __init__(self, *args, **kwargs):
        DummySocket.last_instance = self
        DummySocket.instances.append(self)
        self.options = {}
        self.bound_addr = None
        self.sent_messages = []
    def setsockopt(self, level, optname, value):
        # Record socket options set
        self.options[(level, optname)] = value
    def bind(self, addr):
        # Record the bind address
        self.bound_addr = addr
    def sendto(self, message, addr):
        # Record message and destination for verification
        self.sent_messages.append((message, addr))
    def recvfrom(self, bufsize):
        # Provide queued data or raise StopIteration to break loop
        if hasattr(DummySocket, "recv_queue") and DummySocket.recv_queue:
            return DummySocket.recv_queue.pop(0)
        raise StopIteration
    def close(self):
        # No action needed for dummy
        pass

def make_ssdp_request(st_header: str) -> bytes:
    """Utility to build a raw SSDP M-SEARCH request payload with the given ST header."""
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {discovery.DiscoveryService.MCAST_GRP}:{discovery.DiscoveryService.MCAST_PORT}\r\n"
        "MAN: \"ssdp:discover\"\r\n"
        f"ST: {st_header}\r\n"
        "\r\n"
    ).encode("utf-8")

def create_discovery_service(monkeypatch):
    """Instantiate a DiscoveryService with external effects patched out."""
    # Reset DummySocket state
    DummySocket.instances.clear()
    DummySocket.last_instance = None
    DummySocket.recv_queue = []
    # Patch socket.socket to use DummySocket
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: DummySocket(*args, **kwargs))
    # Patch select.select to always indicate the socket is ready (to trigger recvfrom)
    monkeypatch.setattr(select, "select", lambda r, w, x, timeout=None: (r, [], []))
    # Patch Setting.get_by_name to avoid database access, returning dummy values for discovery settings
    import ad2web.services.discovery_service as discovery_service
    def dummy_get_by_name(name, default=None):
        if name == "discovery_host":
            return types.SimpleNamespace(value="1.2.3.4")
        elif name == "discovery_port":
            return types.SimpleNamespace(value="1234")
        elif name == "discovery_usn":
            return types.SimpleNamespace(value="uuid:1234-uuid")
        # Fallback for other settings: use default if provided, else None
        return types.SimpleNamespace(value=default if default is not None else None)
    monkeypatch.setattr(discovery_service.Setting, "get_by_name", dummy_get_by_name)
    # Create a dummy Flask app object with minimal attributes needed
    dummy_app = types.SimpleNamespace(
        config={"SERVER_NAME": "localhost", "SERVER_PORT": 5000, "SECRET_KEY": "dummykey"},
        logger=types.SimpleNamespace(info=lambda *args, **kwargs: None,
                                     debug=lambda *args, **kwargs: None,
                                     error=lambda *args, **kwargs: None),
        # Dummy app_context that can be used in with-statement
        app_context=lambda: types.SimpleNamespace(__enter__=lambda self: None,
                                                  __exit__=lambda self, exc_type, exc_val, exc_tb: False)
    )
    # Instantiate the DiscoveryService with the dummy app
    service = discovery.DiscoveryService(dummy_app)
    return service

def test_discovery_socket_setup(monkeypatch):
    """DiscoveryService should set up the UDP socket with correct multicast options and binding."""
    service = create_discovery_service(monkeypatch)
    # No data queued; the first recvfrom will raise to break out after socket setup
    DummySocket.recv_queue = []  # ensure no incoming data
    try:
        # Manually run one iteration of the discovery loop
        service._running = True
        service._run()
    except StopIteration:
        pass
    # Verify that the socket was bound to the multicast group and port
    sock = DummySocket.instances[0]
    expected_addr = (discovery.DiscoveryService.MCAST_GRP, discovery.DiscoveryService.MCAST_PORT)
    assert sock.bound_addr == expected_addr
    # It should have set SO_REUSEADDR on the socket
    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR) in sock.options
    assert sock.options[(socket.SOL_SOCKET, socket.SO_REUSEADDR)] == 1
    # It should have joined the multicast group (IP_ADD_MEMBERSHIP option set)
    assert (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP) in sock.options
    mreq = struct.pack("4sl", socket.inet_aton(discovery.DiscoveryService.MCAST_GRP), socket.INADDR_ANY)
    assert sock.options[(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)] == mreq

import pytest

@pytest.mark.parametrize("st_val", ["ssdp:all", "urn:schemas-upnp-org:device:AlarmDecoder:1"])
def test_discovery_responds_to_search(monkeypatch, st_val):
    """DiscoveryService should send a response for valid search targets (ssdp:all or AlarmDecoder device type)."""
    service = create_discovery_service(monkeypatch)
    # Queue a single M-SEARCH request for the given ST value
    DummySocket.recv_queue = [(make_ssdp_request(st_val), ("192.168.0.50", 1900))]
    try:
        service._running = True
        service._run()
    except StopIteration:
        pass
    # A response should have been sent using a new socket (DummySocket instances[1])
    assert len(DummySocket.instances) >= 2
    send_sock = DummySocket.instances[1]
    # Exactly one response message should be sent
    assert len(send_sock.sent_messages) == 1
    resp_bytes, dest_addr = send_sock.sent_messages[0]
    # The destination should be the source address of the request
    assert dest_addr == ("192.168.0.50", 1900)
    resp_msg = resp_bytes.decode("utf-8")
    # Response should be an HTTP 200 OK message
    assert "HTTP/1.1 200 OK" in resp_msg
    # The USN in the response should include the dummy UUID
    assert "uuid:1234-uuid" in resp_msg

@pytest.mark.parametrize("st_val", ["upnp:rootdevice", "some:other:service"])
def test_discovery_ignores_unmatched_search(monkeypatch, st_val):
    """DiscoveryService should not respond to search targets it does not recognize."""
    service = create_discovery_service(monkeypatch)
    DummySocket.recv_queue = [(make_ssdp_request(st_val), ("10.0.0.1", 1900))]
    try:
        service._running = True
        service._run()
    except StopIteration:
        pass
    # No additional socket should have been created for sending (only the listening socket exists)
    assert len(DummySocket.instances) == 1
    listening_sock = DummySocket.instances[0]
    # And no messages should have been sent by the listening socket
    assert listening_sock.sent_messages == []

def test_discovery_response_format(monkeypatch):
    """The discovery response should contain the correct ST, USN, and LOCATION headers."""
    service = create_discovery_service(monkeypatch)
    st_val = "urn:schemas-upnp-org:device:AlarmDecoder:1"
    DummySocket.recv_queue = [(make_ssdp_request(st_val), ("1.2.3.4", 1900))]
    try:
        service._running = True
        service._run()
    except StopIteration:
        pass
    # Capture the sent response message
    assert len(DummySocket.instances) >= 2
    resp_bytes, dest_addr = DummySocket.instances[1].sent_messages[0]
    resp_text = resp_bytes.decode("utf-8")
    # It should echo the ST value in the response
    assert f"ST: {st_val}" in resp_text
    # USN should include the configured UUID
    assert "USN: uuid:1234-uuid" in resp_text
    # LOCATION should point to the device description URL on the dummy host and port
    assert "LOCATION: http://1.2.3.4:1234" in resp_text
    assert "device_description.xml" in resp_text
