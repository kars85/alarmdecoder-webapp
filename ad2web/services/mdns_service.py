# services/mdns_service.py

import socket
import threading
import time
import os

from zeroconf import Zeroconf, ServiceInfo
from flask import current_app  # for logging if needed (app logger passed in init)

class MDNSService:
    """
    mDNS discovery service for the AlarmDecoder webapp.
    Advertises the web application on the local network via Zeroconf (Bonjour).
    """
    SERVICE_TYPE = "_alarmdecoder._tcp.local."

    def __init__(self, app):
        """
        Initialize the MDNSService with the given Flask app.
        """
        self.app = app
        self._zeroconf = None
        self._service_info = None
        self._thread = None
        self._running = False

    def start(self):
        """Start the mDNS advertisement thread."""
        if not self._running:
            self._running = True
            # Launch the background thread for mDNS service (daemon so it won't block exit)
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
            self.app.logger.info("MDNSService started.")

    def stop(self):
        """Stop the mDNS service thread and unregister the service."""
        self._running = False
        if self._thread:
            # Give the thread a moment to finish unregistering
            self._thread.join(timeout=2)
        self.app.logger.info("MDNSService stopped.")

    def _run(self):
        """Background thread method to register the mDNS service and keep it alive."""
        # Determine hostname and port for advertisement
        hostname = socket.gethostname() or "alarmdecoder"
        port = int(os.getenv('AD_LISTENER_PORT', 5000))
        # Get a local IP address for the service (prefer a non-loopback address)
        ip_addr = self._get_local_ip()
        try:
            # Prepare Zeroconf and service info
            self._zeroconf = Zeroconf()
            service_name = f"{hostname}.{MDNSService.SERVICE_TYPE}"  # full service name with type
            # Create ServiceInfo with address, port, and hostname (server)
            self._service_info = ServiceInfo(
                MDNSService.SERVICE_TYPE,
                service_name,
                addresses=[socket.inet_aton(ip_addr)],
                port=port,
                properties={},  # no TXT record properties for now
                server=f"{hostname}.local."
            )
            # Register service on the network
            self._zeroconf.register_service(self._service_info)
            self.app.logger.info(f"mDNS service registered: host={hostname}, port={port}, type={MDNSService.SERVICE_TYPE}")
        except Exception as e:
            # Log any failure in setting up Zeroconf
            self.app.logger.error(f"MDNSService registration failed: {e}", exc_info=True)
            self._running = False
            return

        # Keep the service active until stop is called
        try:
            while self._running:
                time.sleep(1.0)  # Sleep to keep thread alive (Zeroconf handles network I/O internally)
        finally:
            # On exiting, unregister and close the Zeroconf service
            if self._zeroconf and self._service_info:
                try:
                    self._zeroconf.unregister_service(self._service_info)
                    self._zeroconf.close()
                    self.app.logger.info("mDNS service unregistered.")
                except Exception as ex:
                    self.app.logger.warning(f"Error during mDNS service shutdown: {ex}", exc_info=True)

    def _get_local_ip(self):
        """
        Obtain the local IP address to advertise. Tries to get a non-loopback
        interface IP. Falls back to 127.0.0.1 if unable to determine.
        """
        ip = None
        # Try using netifaces (if installed) to get the default gateway interface IP
        try:
            import netifaces
            gws = netifaces.gateways()
            default_iface = gws.get('default', {}).get(netifaces.AF_INET)
            if default_iface:
                iface_name = default_iface[1]
                addr_info = netifaces.ifaddresses(iface_name).get(netifaces.AF_INET)
                if addr_info and len(addr_info) > 0:
                    ip = addr_info[0].get('addr')
        except ImportError:
            # netifaces not available, proceed with alternative method
            pass
        except Exception as err:
            self.app.logger.debug(f"MDNSService: netifaces error: {err}", exc_info=True)
        # Fallback: use a socket connection trick to detect IP
        if not ip:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                # Use a dummy destination to determine outbound interface IP
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
            except Exception:
                ip = "127.0.0.1"
        return ip
