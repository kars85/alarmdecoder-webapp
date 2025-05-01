# ad2web/discovery.py

from flask import current_app
from services.discovery_service import DiscoveryService
from services.mdns_service import MDNSService
_discovery_service = None
_mdns_service = None

def init_discovery(app):
    """
    Initialize the global DiscoveryService for the given Flask app.
    Call this once after app creation.
    """
    global _discovery_service, _mdns_service
    if _discovery_service is None:
        _discovery_service = DiscoveryService(app)
        _mdns_service = MDNSService(app)
        app.logger.info("DiscoveryService and MDNSService instances created.")

def start_discovery():
    if _discovery_service:
        _discovery_service.start()
    if _mdns_service:
        _mdns_service.start()
    else:
        current_app.logger.error("DiscoveryService not initialized. Call init_discovery(app) first.")


def stop_discovery():
    if _mdns_service:
        _mdns_service.stop()
    if _discovery_service:
        _discovery_service.stop()
    else:
        current_app.logger.error("DiscoveryService not initialized. Nothing to stop.")

