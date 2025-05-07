import pytest
from flask import url_for, Flask
from werkzeug.exceptions import Forbidden
from ad2web.settings import views  # assuming the module path for settings views
from ad2web.settings.models import Setting

@pytest.mark.parametrize("url", [
    "/settings/host",
    "/settings/hostname",
    "/settings/diagnostics",
    "/settings/configure_updater",
    "/settings/configure_system_email",
    "/settings/configure_exports",
    "/settings/network/testif"
])
def test_admin_routes_require_admin(normal_client, url):
    """Normal user (non-admin) should get 403 Forbidden on admin-only routes."""
    resp = normal_client.get(url)
    assert resp.status_code == 403

@pytest.mark.parametrize("url", [
    "/settings/host",
    "/settings/hostname",
    "/settings/diagnostics",
    "/settings/configure_updater",
    "/settings/configure_system_email",
    "/settings/configure_exports"
])
def test_login_required_for_settings(client, url):
    """Unauthenticated users should be redirected to login page for any settings route."""
    resp = client.get(url)
    # Flask-Login redirects to login page (assuming endpoint name 'auth.login' or similar)
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")  # redirected to login page

def test_host_page_non_linux_shows_warning(admin_client, monkeypatch):
    """If host settings accessed on non-Linux, a warning flash is shown."""
    monkeypatch.setattr(views.platform, "system", lambda: "Windows")
    resp = admin_client.get("/settings/host")
    # Should still return 200 with basic info, plus a warning flash in content
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "only supported on Linux" in text  # warning message present for non-Linux

def test_host_page_no_netifaces_shows_warning(admin_client, monkeypatch):
    """If netifaces is not installed on Linux, should flash a warning."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", False)
    resp = admin_client.get("/settings/host")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "netifaces module not found" in text  # warning message about netifaces

def test_host_page_no_interfaces_found(admin_client, monkeypatch):
    """If no network interfaces are found, should flash an info message."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    monkeypatch.setattr(views, "_list_network_interfaces", lambda: [])
    resp = admin_client.get("/settings/host")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "No configurable network interfaces found." in text

def test_host_page_shows_interface_options(admin_client, monkeypatch):
    """If interfaces exist, the form should list them (excluding loopback and Docker)."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    monkeypatch.setattr(views, "_list_network_interfaces", lambda: ["lo", "eth0", "docker0"])
    resp = admin_client.get("/settings/host")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    # "eth0" should appear as an option in the form, but "lo" and "docker0" filtered out
    assert "eth0" in text
    assert "docker0" not in text

def test_host_page_post_selects_interface(admin_client, monkeypatch):
    """Posting a selected interface should redirect to its configuration page."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    monkeypatch.setattr(views, "_list_network_interfaces", lambda: ["eth0", "wlan0"])
    # Prepare the form choices by first loading the page (to set up the form in session)
    _ = admin_client.get("/settings/host")
    # Now submit the form with an interface selection
    resp = admin_client.post("/settings/host", data={"ethernet_devices": "eth0", "submit": "Configure"})
    # Should redirect to /settings/network/eth0
    assert resp.status_code == 302
    assert "/settings/network/eth0" in resp.headers["Location"]

def test_configure_device_non_linux_redirects(admin_client, monkeypatch):
    """Non-Linux systems cannot configure network device, expect redirect to host with error."""
    monkeypatch.setattr(views.platform, "system", lambda: "Darwin")
    resp = admin_client.get("/settings/network/eth0")
    # Should redirect to host settings with an error flash
    assert resp.status_code == 302
    assert url_for("settings.host", _external=False) in resp.headers["Location"]

def test_configure_device_no_netifaces_redirects(admin_client, monkeypatch):
    """If netifaces not available, redirect to host with error flash."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", False)
    resp = admin_client.get("/settings/network/eth0")
    assert resp.status_code == 302
    assert url_for("settings.host", _external=False) in resp.headers["Location"]
    # Expect error flash about netifaces
    follow = admin_client.get("/settings/host")
    assert "netifaces module not found" in follow.get_data(as_text=True)

def test_configure_device_invalid_name(admin_client, monkeypatch):
    """Invalid device name or loopback triggers warning and redirect back to host."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    # Device 'lo' (loopback) should be unsupported
    resp = admin_client.get("/settings/network/lo")
    assert resp.status_code == 302
    # Check that the redirect is to host page
    assert url_for("settings.host", _external=False) in resp.headers["Location"]
    # Also test an invalid name with special char
    resp2 = admin_client.get("/settings/network/eth0$")
    assert resp2.status_code == 302
    assert url_for("settings.host", _external=False) in resp2.headers["Location"]

def test_configure_device_get_prefills_dhcp(admin_client, monkeypatch):
    """GET on configure_device for an interface with DHCP config populates form fields accordingly."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    # Simulate /etc/network/interfaces content showing eth0 configured for DHCP
    dummy_map = [f"iface eth0 inet dhcp\n"]
    monkeypatch.setattr(views, "_parse_network_file", lambda: dummy_map)
    # Simulate netifaces reporting a live IP (though DHCP, should still show blank or live values)
    class DummyNetIf:
        AF_INET = 2
        @staticmethod
        def ifaddresses(dev):
            if dev == "eth0":
                return {DummyNetIf.AF_INET: [ {"addr": "192.168.1.100", "netmask": "255.255.255.0"} ]}
            return {}
        @staticmethod
        def gateways():
            return {'default': {DummyNetIf.AF_INET: ("192.168.1.1", "eth0")}}
    monkeypatch.setattr(views, "netifaces", DummyNetIf)
    resp = admin_client.get("/settings/network/eth0")
    data = resp.get_data(as_text=True)
    # Should default to DHCP selection (connection_type=dhcp) and not show static IP fields filled
    assert 'value="dhcp"' in data and 'checked' in data  # DHCP radio selected
    # IP fields should either be empty or reflect live values, but not static preset
    assert 'value="192.168.1.100"' in data or 'name="ip_address" value=""' in data

def test_configure_device_get_prefills_static(admin_client, monkeypatch):
    """GET on configure_device for static config interface populates form with current static IP settings."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    # Simulate config file showing eth0 static configuration
    dummy_map = [
        "auto eth0\n",
        "iface eth0 inet static\naddress 10.0.0.2\nnetmask 255.255.255.0\ngateway 10.0.0.1\n"
    ]
    monkeypatch.setattr(views, "_parse_network_file", lambda: dummy_map)
    # netifaces returns nothing or irrelevant (we want to rely on config file values)
    class DummyNetIf:
        AF_INET = 2
        @staticmethod
        def ifaddresses(dev): return {}
        @staticmethod
        def gateways(): return {}
    monkeypatch.setattr(views, "netifaces", DummyNetIf)
    resp = admin_client.get("/settings/network/eth0")
    text = resp.get_data(as_text=True)
    # The form fields should be pre-filled with the static config from dummy_map
    assert 'value="10.0.0.2"' in text  # IP address field
    assert 'value="255.255.255.0"' in text  # netmask field
    assert 'value="10.0.0.1"' in text  # gateway field

def test_configure_device_post_static_missing_fields(admin_client, monkeypatch):
    """Submitting static configuration without IP or netmask yields error and stays on page."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    # Provide a minimal parse so device_map is not None
    monkeypatch.setattr(views, "_parse_network_file", lambda: ["iface eth0 inet dhcp\n"])
    # Monkeypatch write function to avoid actual file writes
    monkeypatch.setattr(views, "_write_network_file", lambda config: True)
    # Submit form with static selected but blank IP/netmask
    form_data = {
        "ethernet_device": "eth0",
        "connection_type": "static",
        "ip_address": "",         # missing IP
        "netmask": "",            # missing netmask
        "gateway": "10.0.0.1"
    }
    resp = admin_client.post("/settings/network/eth0", data=form_data)
    # Should not redirect on validation error, status 200 and show error message
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "IP Address and Netmask are required for static configuration." in text

def test_configure_device_post_success(admin_client, monkeypatch):
    """Submitting valid changes should write config and attempt interface restart, resulting in success redirect."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    # Provide an existing config map for eth0
    monkeypatch.setattr(views, "_parse_network_file", lambda: ["iface eth0 inet dhcp\n"])
    # Simulate successful file write
    monkeypatch.setattr(views, "_write_network_file", lambda new_map: True)
    # Simulate successful ifdown/ifup commands via sh (no exception raised)
    class DummySh:
        class ErrorReturnCode(Exception):
            pass
        class CommandNotFound(Exception):
            pass
        @staticmethod
        def ifdown(dev, _ok_code=None): return 0
        @staticmethod
        def ifup(dev): return 0
    monkeypatch.setattr(views, "sh", DummySh)
    form_data = {
        "ethernet_device": "eth0",
        "connection_type": "dhcp",
        "ip_address": "",
        "netmask": "",
        "gateway": ""
    }
    resp = admin_client.post("/settings/network/eth0", data=form_data, follow_redirects=False)
    # Should redirect to host page on success
    assert resp.status_code == 302
    assert url_for("settings.host", _external=False) in resp.headers["Location"]
    # Check that a success flash is queued (by inspecting the next page content)
    follow = admin_client.get("/settings/host")
    assert 'Network interface "eth0" restarted successfully.' in follow.get_data(as_text=True)

def test_configure_device_post_restart_failure(admin_client, monkeypatch):
    """If interface restart fails after saving, user sees warning but still redirected to host."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    monkeypatch.setattr(views, "_parse_network_file", lambda: ["iface eth0 inet dhcp\n"])
    monkeypatch.setattr(views, "_write_network_file", lambda new_map: True)
    # Simulate ifdown raising an error code (to test error handling)
    class DummySh:
        class ErrorReturnCode(Exception):
            def __init__(self, stderr=b"failed", stdout=b""):
                self.stderr = stderr; self.stdout = stdout
        class CommandNotFound(Exception):
            pass
        @staticmethod
        def ifdown(dev, _ok_code=None):
            # Simulate command that returns error
            raise DummySh.ErrorReturnCode()
        @staticmethod
        def ifup(dev):
            return 0
    monkeypatch.setattr(views, "sh", DummySh)
    form_data = {
        "ethernet_device": "eth0",
        "connection_type": "dhcp"
    }
    resp = admin_client.post("/settings/network/eth0", data=form_data)
    # Should redirect to host even if restart failed
    assert resp.status_code == 302
    follow = admin_client.get("/settings/host")
    text = follow.get_data(as_text=True)
    # Expect a warning that configuration saved but not applied automatically
    assert "Configuration saved, but failed to apply changes" in text

def test_configure_device_post_write_failure(admin_client, monkeypatch):
    """If writing config file fails, user remains on config page with error."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views, "hasnetifaces", True)
    monkeypatch.setattr(views, "_parse_network_file", lambda: ["iface eth0 inet dhcp\n"])
    # Simulate _write_network_file raising an exception to trigger error
    def fail_write(_):
        raise Exception("write error")
    monkeypatch.setattr(views, "_write_network_file", fail_write)
    # Patch sh to avoid further errors (not used if write fails)
    monkeypatch.setattr(views, "sh", type("DummySh", (), {"ErrorReturnCode": Exception, "CommandNotFound": Exception}))
    form_data = {
        "ethernet_device": "eth0",
        "connection_type": "dhcp"
    }
    resp = admin_client.post("/settings/network/eth0", data=form_data)
    # Should not redirect, remain on page with error flash about writing
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Error writing network configuration" in text or "Cannot save changes" in text

def test_hostname_page_non_linux(admin_client, monkeypatch):
    """Changing hostname on non-Linux triggers error flash and redirects to host page."""
    monkeypatch.setattr(views.platform, "system", lambda: "Darwin")
    resp = admin_client.post("/settings/hostname", data={"hostname": "newname"})
    assert resp.status_code == 302
    # Should redirect to /settings/host
    assert url_for("settings.host", _external=False) in resp.headers["Location"]
    # Confirm error flash
    follow = admin_client.get("/settings/host")
    assert "Hostname changes are only supported on Linux." in follow.get_data(as_text=True)

def test_hostname_get_prefills_current(admin_client, monkeypatch):
    """GET hostname page should pre-fill the current hostname in the form."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "current-name")
    resp = admin_client.get("/settings/hostname")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The form input should contain the current hostname
    assert 'value="current-name"' in html

def test_hostname_post_empty_name(admin_client, monkeypatch):
    """Submitting empty hostname should show error and stay on the page."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "currname")
    data = {"hostname": "   "}  # only whitespace
    resp = admin_client.post("/settings/hostname", data=data)
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Hostname cannot be empty." in text

def test_hostname_post_same_name(admin_client, monkeypatch):
    """Submitting the same hostname should inform user and redirect to host (no change)."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "SAME-NAME")
    data = {"hostname": "SAME-NAME"}
    resp = admin_client.post("/settings/hostname", data=data)
    # Should redirect to host with info flash
    assert resp.status_code == 302
    follow = admin_client.get("/settings/host")
    assert "Hostname is already set to this value." in follow.get_data(as_text=True)

def test_hostname_post_invalid_format(admin_client, monkeypatch):
    """Invalid hostname formats (bad chars or punctuation at ends) show error and do not redirect."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "goodname")
    # Name with invalid char (space)
    resp = admin_client.post("/settings/hostname", data={"hostname": "bad name"})
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    # Could be caught by form validation or additional regex in view
    assert "Invalid hostname format" in text or "Invalid characters found" in text
    # Name starting with hyphen
    resp2 = admin_client.post("/settings/hostname", data={"hostname": "-startdash"})
    assert resp2.status_code == 200
    text2 = resp2.get_data(as_text=True)
    assert "Invalid hostname format" in text2

def test_hostname_post_permission_failure(admin_client, monkeypatch):
    """If config files are not writable, flash errors and remain on page."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "oldname")
    # Simulate no write permission on hosts/hostname files
    monkeypatch.setattr(views.os, "access", lambda path, mode: False)
    data = {"hostname": "newname"}
    resp = admin_client.post("/settings/hostname", data=data)
    assert resp.status_code == 200  # stays on page
    text = resp.get_data(as_text=True)
    # Both hosts and hostname file unwritable
    assert "Cannot write to" in text

def test_hostname_post_success(admin_client, monkeypatch):
    """Successful hostname change (files updated and command succeeds) should flash success and redirect to host."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "oldname")
    # Simulate files are writable
    monkeypatch.setattr(views.os, "access", lambda path, mode: True)
    # Monkeypatch _sethostname to simulate updating at least one file
    monkeypatch.setattr(views, "_sethostname", lambda file, old, new: True)
    # Simulate hostnamectl command success
    class DummySh:
        class CommandNotFound(Exception):
            pass
        class ErrorReturnCode(Exception):
            stderr = b""
            stdout = b""
    dummy_sh = DummySh()
    def dummy_hostnamectl(cmd, new_name):
        return 0  # simulate success
    monkeypatch.setattr(views.sh, "hostnamectl", dummy_hostnamectl)
    monkeypatch.setattr(views, "sh_service", lambda svc, action: 0)  # simulate avahi restart success
    data = {"hostname": "NEWNAME"}
    resp = admin_client.post("/settings/hostname", data=data)
    # Should redirect to host on success
    assert resp.status_code == 302
    follow = admin_client.get("/settings/host")
    text = follow.get_data(as_text=True)
    assert 'Hostname changed to "NEWNAME"' in text

def test_hostname_post_command_failure(admin_client, monkeypatch):
    """If system commands to change hostname fail, appropriate error or warning is flashed."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "oldname")
    monkeypatch.setattr(views.os, "access", lambda path, mode: True)
    monkeypatch.setattr(views, "_sethostname", lambda file, old, new: True)
    # Simulate hostnamectl not found, hostname command also not found
    class DummySh:
        class CommandNotFound(Exception):
            pass
        class ErrorReturnCode(Exception):
            stderr = b"failure"
            stdout = b""
    def raise_cmd_not_found(*args, **kwargs):
        raise DummySh.CommandNotFound()
    monkeypatch.setattr(views.sh, "hostnamectl", raise_cmd_not_found)
    monkeypatch.setattr(views.sh, "hostname", raise_cmd_not_found)
    monkeypatch.setattr(views, "sh_service", lambda svc, action: 0)
    data = {"hostname": "anothername"}
    resp = admin_client.post("/settings/hostname", data=data)
    # Command not found should flash an error and remain on page (no redirect, because no success or file-only change)
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "could not find system command to set hostname" in text or "ifdown or ifup command not found" in text

def test_hostname_post_files_updated_but_command_failed(admin_client, monkeypatch):
    """If files updated but hostname command fails, flash warning and redirect to host (reboot needed)."""
    monkeypatch.setattr(views.platform, "system", lambda: "Linux")
    monkeypatch.setattr(views.socket, "getfqdn", lambda: "oldname")
    monkeypatch.setattr(views.os, "access", lambda path, mode: True)
    # _sethostname returns True (files updated), simulate command fails with ErrorReturnCode
    monkeypatch.setattr(views, "_sethostname", lambda file, old, new: True)
    class DummyError(Exception):
        def __init__(self):
            self.stderr, self.stdout = b"", b""
    def raise_hostnamectl_fail(*args, **kwargs):
        raise DummyError()
    monkeypatch.setattr(views.sh, "hostnamectl", raise_hostnamectl_fail)
    monkeypatch.setattr(views.sh, "hostname", raise_hostnamectl_fail)
    data = {"hostname": "somehost"}
    resp = admin_client.post("/settings/hostname", data=data)
    # Should redirect to host since files were updated even though command failed
    assert resp.status_code == 302
    follow = admin_client.get("/settings/host")
    text = follow.get_data(as_text=True)
    assert "Hostname updated in configuration files, but failed to apply change" in text

def test_configure_updater_get_populates_form(admin_client, db_session):
    """GET update checker config should show current settings values in the form."""
    # Ensure default Setting values exist in DB
    Setting.set_value("version_checker_timeout", 7200)  # 2 hours
    Setting.set_value("version_checker_disable", True)
    db_session.commit()
    resp = admin_client.get("/settings/configure_updater")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The form fields should reflect the values set above
    assert 'value="7200"' in html  # timeout field
    # The disable checkbox should be checked if True
    assert '<input id="version_checker_disable" type="checkbox" checked' in html

def test_configure_updater_post_success(admin_client, db_session):
    """Posting valid updater config saves to DB, flashes success, and redirects to settings index."""
    data = {
        "version_checker_timeout": 1800,
        "version_checker_disable": ""  # not checked (Flask will not send value if unchecked)
    }
    resp = admin_client.post("/settings/configure_updater", data=data)
    # Successful save should redirect to settings index
    assert resp.status_code == 302
    assert url_for("settings.index", _external=False) in resp.headers["Location"]
    # Flash message for success
    follow = admin_client.get("/settings/")
    assert "Update checker settings updated." in follow.get_data(as_text=True)
    # Verify DB persistence
    timeout_setting = Setting.get_by_name("version_checker_timeout")
    disable_setting = Setting.get_by_name("version_checker_disable")
    assert timeout_setting.value == 1800
    assert disable_setting.value in (False, "False", 0)  # disabled unchecked -> False

def test_configure_updater_post_db_error(admin_client, monkeypatch):
    """Simulate database error on saving updater settings to ensure error flash."""
    # Monkeypatch session.commit to throw SQLAlchemyError
    monkeypatch.setattr(views.db.session, "commit", lambda: (_ for _ in ()).throw(views.SQLAlchemyError("DB error")))
    data = {"version_checker_timeout": 600, "version_checker_disable": "y"}
    resp = admin_client.post("/settings/configure_updater", data=data)
    # Should stay on page (no redirect) and flash an error
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "Error saving update checker settings to database." in text

def test_configure_updater_post_unexpected_error(admin_client, monkeypatch):
    """Simulate an unexpected exception when saving updater config."""
    # Monkeypatch Setting.set_value to raise generic exception
    def fail_set_value(name, value):
        raise Exception("Unexpected failure")
    monkeypatch.setattr(Setting, "set_value", fail_set_value)
    data = {"version_checker_timeout": 1000, "version_checker_disable": ""}
    resp = admin_client.post("/settings/configure_updater", data=data)
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "An unexpected error occurred" in text

def test_configure_email_get_populates_form(admin_client, db_session):
    """GET system email config should populate form with current settings (except password)."""
    # Set some system_email settings in DB
    Setting.set_value("system_email_server", "smtp.example.com")
    Setting.set_value("system_email_port", 2525)
    Setting.set_value("system_email_tls", True)
    Setting.set_value("system_email_auth", True)
    Setting.set_value("system_email_username", "user@example.com")
    Setting.set_value("system_email_from", "noreply@example.com")
    # Note: we don't set system_email_password for security (should remain blank in form)
    db_session.commit()
    resp = admin_client.get("/settings/configure_system_email")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # Form fields should show above values
    assert 'value="smtp.example.com"' in html
    assert 'value="2525"' in html
    # TLS and Auth checkboxes should be checked
    assert 'name="tls" checked' in html
    assert 'name="auth_required" checked' in html
    # Username and default sender fields
    assert 'value="user@example.com"' in html
    assert 'value="noreply@example.com"' in html
    # Password field should be blank (not pre-filled)
    # (We expect an empty value attribute for password)
    assert 'name="password"' in html and 'value=""' in html

def test_configure_email_post_success(admin_client, db_session):
    """Posting valid email settings updates the Setting entries and flashes success."""
    data = {
        "mail_server": "mail.example.com",
        "port": 587,
        "tls": "y",  # checkbox checked
        "auth_required": "y",  # checkbox checked
        "username": "newuser",
        "password": "newpass123",
        "default_sender": "alarms@example.com"
    }
    resp = admin_client.post("/settings/configure_system_email", data=data)
    # Should redirect to settings index on success
    assert resp.status_code == 302
    follow = admin_client.get("/settings/")
    body = follow.get_data(as_text=True)
    assert "System Email settings updated successfully." in body
    # Verify saved to DB
    s_server = Setting.get_by_name("system_email_server")
    s_port = Setting.get_by_name("system_email_port")
    s_tls = Setting.get_by_name("system_email_tls")
    s_auth = Setting.get_by_name("system_email_auth")
    s_user = Setting.get_by_name("system_email_username")
    s_pass = Setting.get_by_name("system_email_password")
    s_from = Setting.get_by_name("system_email_from")
    assert s_server.value == "mail.example.com"
    assert s_port.value == 587
    assert s_tls.value in (True, "True", 1)
    assert s_auth.value in (True, "True", 1)
    assert s_user.value == "newuser"
    assert s_pass.value == "newpass123"
    assert s_from.value == "alarms@example.com"

def test_configure_email_post_auth_without_credentials(admin_client):
    """If SMTP auth is required but username/password are missing, validation error occurs and form reloads."""
    data = {
        "mail_server": "localhost",
        "port": 25,
        "tls": "",  # not checked
        "auth_required": "y",  # checked, but will omit user/pass
        "username": "",
        "password": "",
        "default_sender": "root@alarmdecoder"
    }
    resp = admin_client.post("/settings/configure_system_email", data=data)
    # Should not redirect, remain on page with errors
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    # Expect error flash about needing username/password
    assert "Username and Password are required when SMTP authentication is enabled." in text
    # Also the form fields should show field-specific errors
    assert "Password is required when SMTP authentication is enabled." in text

def test_configure_email_post_db_error(admin_client, monkeypatch):
    """Simulate DB error when saving email settings."""
    monkeypatch.setattr(views.db.session, "commit", lambda: (_ for _ in ()).throw(views.SQLAlchemyError("DB err")))
    data = {
        "mail_server": "x", "port": 25, "tls": "", "auth_required": "",
        "username": "", "password": "", "default_sender": "x@x"
    }
    resp = admin_client.post("/settings/configure_system_email", data=data)
    assert resp.status_code == 200
    assert "Error saving system email settings to database." in resp.get_data(as_text=True)

def test_configure_email_post_unexpected_error(admin_client, monkeypatch):
    """Simulate unexpected exception on saving email settings (e.g., during Setting.set_value)."""
    def fail_set_value(name, value):
        if name == "system_email_server":
            raise Exception("Unexpected error")
        return Setting.set_value(name, value)
    monkeypatch.setattr(Setting, "set_value", fail_set_value)
    data = {
        "mail_server": "localhost",
        "port": 25,
        "tls": "",
        "auth_required": "",
        "username": "",
        "password": "",
        "default_sender": "root@alarmdecoder"
    }
    resp = admin_client.post("/settings/configure_system_email", data=data)
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "An unexpected error occurred" in text

def test_password_page_loads(admin_client):
    """Ensure the password change page loads for a logged-in user."""
    resp = admin_client.get("/settings/password")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The form fields for current and new password should be present
    assert 'name="password"' in html and 'name="new_password"' in html

def test_password_change_success(admin_client, normal_user):
    """Submitting correct current password and valid new password updates the password."""
    # Assume admin_client is logged in as an admin user; we'll use normal_user to test password change
    client = admin_client  # can use the admin_client but target a normal user if needed
    # We need a logged-in user context for password change. If admin_client is logged in as admin,
    # perhaps use normal_client if it's logged in as normal_user. Here, ensure we have normal_client:
    # normal_user fixture is assumed to be a User object, and normal_client logged in as that user.
    # For demonstration, let's use normal_client for actual operation:
    resp = client.post("/settings/password", data={
        "password": normal_user.raw_password,    # current password (assuming fixture provides raw_password attribute)
        "new_password": "NewPass123!",
        "password_again": "NewPass123!"
    }, follow_redirects=True)
    html = resp.get_data(as_text=True)
    # Expect success message
    assert "Password updated successfully." in html
    # The user's password in the database should be updated (check via login or direct check)
    db_user = normal_user  # if fixture yields user model object
    db_user = db_user.query.get(normal_user.id)  # re-fetch user
    assert db_user.check_password("NewPass123!")  # new password should be set

def test_password_change_wrong_current(admin_client, monkeypatch):
    """If current password is wrong, form should not validate and page should show error."""
    # Monkeypatch User.check_password to always return False to simulate wrong current password
    monkeypatch.setattr("ad2web.user.User.check_password", lambda self, pwd: False)
    resp = admin_client.post("/settings/password", data={
        "password": "wrongpass",
        "new_password": "SomeNewPass1",
        "password_again": "SomeNewPass1"
    })
    # Should not redirect, and should show validation error on page
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Password is wrong." in html

def test_password_change_mismatched_confirm(admin_client):
    """If new password and confirmation do not match, validation error is shown."""
    resp = admin_client.post("/settings/password", data={
        "password": "irrelevant",  # current password (we won't reach check if confirm fails)
        "new_password": "NewPass123",
        "password_again": "OtherPass456"
    })
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    # WTForms EqualTo should produce an error message for password_again field
    assert "must be equal to new_password" in text or "Passwords must match" in text

def test_diagnostics_no_device(admin_client):
    """If no AlarmDecoder device is connected, a warning flash is shown."""
    app = admin_client.application
    # Ensure app.decoder or app.decoder.device is None
    if hasattr(app, "decoder"):
        app.decoder.device = None
    else:
        app.decoder = type("D", (), {"device": None})()
    resp = admin_client.get("/settings/diagnostics")
    assert resp.status_code == 200
    text = resp.get_data(as_text=True)
    assert "device not connected or initialized" in text.lower()

def test_diagnostics_shows_device_info(admin_client, monkeypatch):
    """If device is connected, diagnostics page displays its settings."""
    app = admin_client.application
    # Create a dummy device with expected attributes
    class DummyDevice:
        address = 18
        configbits = 0x01
        address_mask = 0x7F
        version_number = "1.2.3"
        serial_number = "abc123"
        mode = 0  # assuming 0 corresponds to ADEMCO
        emulate_zone = True
        emulate_relay = False
        emulate_lrr = True
        deduplicate = False
        version_flags = 0x0003  # example flags
    dummy = DummyDevice()
    # Provide ADEMCO and DSC constants if needed (the code uses ADEMCO, DSC variables)
    monkeypatch.setattr(views, "ADEMCO", 0, raising=False)
    monkeypatch.setattr(views, "DSC", 1, raising=False)
    # Attach dummy device to app.decoder
    app.decoder = type("Dec", (), {})()
    app.decoder.device = dummy
    resp = admin_client.get("/settings/diagnostics")
    html = resp.get_data(as_text=True)
    # Check some of the dummy device info present
    assert "0x007F" in html or "0x7F" in html  # address_mask in hex
    assert "ADEMCO/Honeywell" in html  # mode interpreted as ADEMCO
    assert "Yes" in html and "No" in html  # presence of Yes/No for emulate options

def test_advanced_page_access(admin_client, normal_client):
    """Advanced settings page is accessible to admin and forbidden to normal user."""
    resp_admin = admin_client.get("/settings/advanced")
    assert resp_admin.status_code == 200
    resp_user = normal_client.get("/settings/advanced")
    assert resp_user.status_code == 403
    # Content check for admin
    assert "Advanced Settings" in resp_admin.get_data(as_text=True)
