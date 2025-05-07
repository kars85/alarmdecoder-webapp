# services/discovery_service.py

import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler
from io import BytesIO

from flask import current_app
from ad2web.settings.models import Setting

class DiscoveryService:
    """
    SSDP-based UPnP/SSDP discovery service.
    Listens for M-SEARCH requests and responds with the webapp's location.
    """

    MCAST_GRP = "239.255.255.250"
    MCAST_PORT = 1900
    RESPONSE_TTL = 1800  # seconds

    def __init__(self, app):
        self.app = app
        self._sock = None
        self._thread = None
        self._running = False

    def start(self):
        """Start the background discovery thread."""
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self.app.logger.info("DiscoveryService started.")

    def stop(self):
        """Stop the discovery thread and close the socket."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self.app.logger.info("DiscoveryService stopped.")

    def _run(self):
        """Main loop: bind to SSDP multicast address and respond to M-SEARCH."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.MCAST_GRP, self.MCAST_PORT))
            mreq = struct.pack("4sl", socket.inet_aton(self.MCAST_GRP), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            self._sock = sock
        except Exception as e:
            self.app.logger.error(f"DiscoveryService socket setup failed: {e}", exc_info=True)
            return

        while self._running:
            try:
                data, addr = sock.recvfrom(1024)
                # Simple parse: look for M-SEARCH and target service
                if b"M-SEARCH" in data and b"ssdp:discover" in data:
                    self._respond(addr)
            except socket.timeout:
                continue
            except Exception as e:
                self.app.logger.error(f"DiscoveryService error: {e}", exc_info=True)
                break

        sock.close()

    def _respond(self, addr):
        """Send an SSDP response to the given address."""
        # Build LOCATION header from settings or default to root URL
        with self.app.app_context():
            host = Setting.get_by_name('discovery_host', default=self.app.config.get('SERVER_NAME', 'localhost')).value
            port = Setting.get_by_name('discovery_port', default=self.app.config.get('SERVER_PORT', 5000)).value
            location = f"http://{host}:{port}/"
            usn = Setting.get_by_name('discovery_usn', default=f"uuid:{self.app.config.get('SECRET_KEY')}").value

        resp_lines = [
            "HTTP/1.1 200 OK",
            f"CACHE-CONTROL: max-age={self.RESPONSE_TTL}",
            "EXT:",
            f"LOCATION: {location}",
            f"SERVER: AlarmDecoder/1.0 UPnP/1.1 AlarmDecoderWebapp/1.0",
            "ST: urn:schemas-upnp-org:device:Basic:1",
            f"USN: {usn}",
            "",
            ""
        ]
        response = "\r\n".join(resp_lines).encode("utf-8")

        try:
            # Send UDP response back to the requester
            resp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            resp_sock.sendto(response, addr)
            resp_sock.close()
            self.app.logger.debug(f"DiscoveryService responded to {addr}")
        except Exception as e:
            self.app.logger.error(f"DiscoveryService response failed: {e}", exc_info=True)

class DiscoveryRequest(BaseHTTPRequestHandler):
    """
    Parses a raw SSDP M-SEARCH packet into a BaseHTTPRequestHandler-like object.
    """
    def __init__(self, request_bytes: bytes):
        self.rfile = BytesIO(request_bytes)
        self.raw_requestline = self.rfile.readline()
        self.error_code = self.error_message = None
        self.parse_request()

    def send_error(self, code, message):
        self.error_code = code
        self.error_message = message