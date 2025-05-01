# ad2web/discovery.py

from flask import current_app
from services.discovery_service import DiscoveryService

_discovery_service = None

def init_discovery(app):
    """
    Initialize the global DiscoveryService for the given Flask app.
    Call this once after app creation.
    """
    global _discovery_service
    if _discovery_service is None:
        _discovery_service = DiscoveryService(app)
        app.logger.info("DiscoveryService instance created.")

def start_discovery():
    """
    Start the discovery service thread.
    Requires `init_discovery(app)` to have been called first.
    """
    if _discovery_service:
        _discovery_service.start()
    else:
        current_app.logger.error("DiscoveryService not initialized. Call init_discovery(app) first.")

def stop_discovery():
    """
    Stop the discovery service thread.
    """
    if _discovery_service:
        _discovery_service.stop()
    else:
        current_app.logger.error("DiscoveryService not initialized. Nothing to stop.")
