# File: tests/test_mdns_service.py
import socket
import types
import sys
import time
import pytest

from ad2web.services import mdns_service

def test_mdns_start_and_stop(monkeypatch):
    """MDNSService.start should spawn a daemon thread, and stop should signal and join the thread."""
    app = types.SimpleNamespace(logger=types.SimpleNamespace(info=lambda *args, **kwargs: None))
    service = mdns_service.MDNSService(app)
    # Monkeypatch threading.Thread to intercept thread creation
    started = {"target_called": False, "daemon_flag": None, "joined": False}
    def dummy_thread(target, daemon):
        # Return an object that mimics Thread: stores target and daemon and has start/join methods
        started["daemon_flag"] = daemon
        started["target"] = target
        def start():
            # Simulate starting the thread by calling target immediately
            started["target_called"] = True
            # We won't actually loop here because we won't call _run in this dummy thread
        def join(timeout=None):
            started["joined"] = True
        return types.SimpleNamespace(start=start, join=join, daemon=daemon)
    monkeypatch.setattr(mdns_service.threading, "Thread", dummy_thread)
    # Call start() and then stop() on the service
    service.start()
    # After start, _running should be True and a thread created with daemon=True
    assert service._running is True
    assert started["daemon_flag"] is True, "Thread should be daemon"
    assert started["target_called"] is True, "Thread target (MDNSService._run) should be invoked"
    # Now stop the service
    service.stop()
    assert service._running is False
    assert started["joined"] is True, "Thread.join should be called to wait for thread to finish"

def test_mdns_get_local_ip_with_netifaces(monkeypatch):
    """_get_local_ip should use netifaces when available to get a non-loopback IP."""
    app = types.SimpleNamespace(logger=types.SimpleNamespace(debug=lambda *args, **kwargs: None))
    service = mdns_service.MDNSService(app)
    # Create dummy netifaces module and data
    class DummyNetifaces:
        AF_INET = socket.AF_INET
        def gateways(self):
            return {"default": {DummyNetifaces.AF_INET: ("192.168.1.1", "eth0")}}
        def ifaddresses(self, ifname):
            if ifname == "eth0":
                return {DummyNetifaces.AF_INET: [ {"addr": "192.168.1.100"} ]}
            return {}
    # Monkeypatch import of netifaces inside _get_local_ip
    monkeypatch.setitem(sys.modules, "netifaces", DummyNetifaces())
    ip = service._get_local_ip()
    # Should pick up the address from dummy netifaces
    assert ip == "192.168.1.100"

def test_mdns_get_local_ip_no_netifaces(monkeypatch):
    """_get_local_ip should fall back to socket method if netifaces is not available or yields no IP."""
    app = types.SimpleNamespace(logger=types.SimpleNamespace(debug=lambda *args, **kwargs: None))
    service = mdns_service.MDNSService(app)
    # Ensure 'netifaces' is not in sys.modules to simulate not installed
    sys.modules.pop("netifaces", None)
    # Monkeypatch socket.socket connect and getsockname to simulate obtaining local IP
    dummy_ip = "10.0.0.50"
    class DummySock:
        def connect(self, addr):
            # pretend to connect (do nothing)
            return None
        def getsockname(self):
            # return dummy local IP and an arbitrary port
            return (dummy_ip, 12345)
        def close(self):
            pass
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: DummySock())
    ip = service._get_local_ip()
    # The fallback should return the dummy_ip (non-loopback)
    assert ip == dummy_ip

def test_mdns_run_registration_and_cleanup(monkeypatch):
    """MDNSService._run should register the service with Zeroconf and then unregister/close on exit."""
    # Prepare MDNSService with deterministic environment
    hostname = "testdevice"
    test_ip = "192.168.1.123"
    test_port = 5000
    # Monkeypatch environment: hostname, port, local IP, and Zeroconf classes
    monkeypatch.setattr(socket, "gethostname", lambda: hostname)
    monkeypatch.setenv("AD_LISTENER_PORT", str(test_port))
    service = mdns_service.MDNSService(types.SimpleNamespace(logger=types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None
    )))
    # Patch _get_local_ip to return our test IP
    monkeypatch.setattr(service, "_get_local_ip", lambda: test_ip)
    # Dummy Zeroconf and ServiceInfo classes to capture calls
    registered = {"service_info": None, "unregistered": False, "closed": False}
    class DummyZeroconf:
        def __init__(self):
            pass
        def register_service(self, info):
            registered["service_info"] = info  # capture the ServiceInfo object
        def unregister_service(self, info):
            registered["unregistered"] = True
        def close(self):
            registered["closed"] = True
    class DummyServiceInfo:
        def __init__(self, service_type, name, addresses, port, properties, server):
            # Store parameters for validation
            self.service_type = service_type
            self.name = name
            self.addresses = addresses
            self.port = port
            self.properties = properties
            self.server = server
    monkeypatch.setattr(mdns_service, "Zeroconf", DummyZeroconf)
    monkeypatch.setattr(mdns_service, "ServiceInfo", DummyServiceInfo)
    # Monkeypatch time.sleep to break out of the loop after one iteration
    def fake_sleep(seconds):
        # After first sleep, stop the loop
        service._running = False
    monkeypatch.setattr(time, "sleep", lambda s: fake_sleep(s) if s == 1.0 else None)
    # Start with _running True and call _run directly (simulate thread execution)
    service._running = True
    service._run()
    # After _run exits, it should have registered and then unregistered the service
    assert isinstance(registered["service_info"], DummyServiceInfo), "ServiceInfo not created or captured"
    info = registered["service_info"]
    # Validate that ServiceInfo was created with correct parameters
    expected_fullname = f"{hostname}.{mdns_service.MDNSService.SERVICE_TYPE}"
    assert info.service_type == mdns_service.MDNSService.SERVICE_TYPE
    assert info.name == expected_fullname
    # addresses should contain the test IP in byte form
    assert info.addresses == [socket.inet_aton(test_ip)]
    assert info.port == test_port
    assert info.properties == {}
    assert info.server == f"{hostname}.local."
    # Zeroconf.unregister_service and close should have been called in finally block
    assert registered["unregistered"] is True
    assert registered["closed"] is True

def test_mdns_run_registration_failure(monkeypatch):
    """If Zeroconf registration fails, MDNSService._run should log an error and stop running."""
    # Set up MDNSService with dummy logger to capture error
    error_logged = {"called": False}
    app = types.SimpleNamespace(logger=types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: error_logged.update(called=True)
    ))
    service = mdns_service.MDNSService(app)
    # Monkeypatch environment and functions for deterministic behavior
    monkeypatch.setattr(socket, "gethostname", lambda: "testhost")
    monkeypatch.setattr(service, "_get_local_ip", lambda: "127.0.0.1")
    monkeypatch.setenv("AD_LISTENER_PORT", "5000")
    # Dummy Zeroconf that raises exception on register_service
    class FailingZeroconf:
        def __init__(self):
            pass
        def register_service(self, info):
            raise Exception("Registration failure")
        def unregister_service(self, info):
            # Should not be called in this scenario
            error_logged["unregister_called"] = True
        def close(self):
            error_logged["close_called"] = True
    monkeypatch.setattr(mdns_service, "Zeroconf", FailingZeroconf)
    monkeypatch.setattr(mdns_service, "ServiceInfo", lambda *args, **kwargs: types.SimpleNamespace())  # dummy info
    # Run the service (should catch the exception and return)
    service._running = True
    service._run()
    # After failure, _running should be set to False and error logged
    assert service._running is False
    assert error_logged["called"] is True, "Error should be logged on registration failure"
    # ensure unregister/close were NOT called since we returned early on failure
    assert "unregister_called" not in error_logged and "close_called" not in error_logged
