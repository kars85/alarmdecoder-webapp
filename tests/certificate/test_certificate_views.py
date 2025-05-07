import pytest
from ad2web.certificate.models import Certificate
from ad2web.certificate.constants import CA, CLIENT, SERVER, INTERNAL, ACTIVE, REVOKED
from ad2web.user.models import User
from ad2web.settings.models import Setting
from ad2web.services import certificate_service  # If needed for certain checks
from ad2web.ser2sock import ser2sock
from werkzeug.security import generate_password_hash

# Patch filesystem interactions and external calls in all tests
@pytest.fixture(autouse=True)
def patch_filesystem(monkeypatch):
    # Avoid actual file writes for certificate index/CRL and certificate export
    monkeypatch.setattr(Certificate, "save_certificate_index", classmethod(lambda cls: None))
    monkeypatch.setattr(Certificate, "save_revocation_list", classmethod(lambda cls: None))
    monkeypatch.setattr(Certificate, "export", lambda self, path: None)
    # Prevent sending actual signals or updating external config
    monkeypatch.setattr(ser2sock, "hup", lambda: None)
    monkeypatch.setattr(ser2sock, "update_config", lambda *args, **kwargs: None)
    yield

# Fixture to populate the database with a CA, server, internal, and client certificates for an admin and a normal user.
@pytest.fixture
def setup_certs(db_session):
    # Ensure an admin user exists (admin_client fixture should have created one, but create if not)
    admin_user = db_session.query(User).filter_by(role_code=0).first()
    if admin_user is None:
        admin_user = User(
            name="admin", email="admin@example.com",
            password=generate_password_hash("password"),
            role_code=0, status_code=2  # ADMIN=0, ACTIVE=2
        )
        db_session.add(admin_user)
    # Create a normal user
    normal_user = User(
        name="normaluser", email="normal@example.com",
        password=generate_password_hash("password"),
        role_code=1, status_code=2  # USER=1, ACTIVE=2
    )
    db_session.add(normal_user)
    # Set use_ssl setting to True to enable certificate features
    ssl_setting = Setting.get_by_name("use_ssl", default=True)
    ssl_setting.value = True
    db_session.add(ssl_setting)
    db_session.commit()

    # Create CA certificate (self-signed)
    ca_cert = Certificate(name="Test CA", description="CA cert", status=ACTIVE, type=CA)
    ca_cert.generate(common_name="Test CA")
    db_session.add(ca_cert)
    db_session.commit()  # commit CA to assign ID

    # Create a Server certificate signed by CA
    server_cert = Certificate(name="Test Server", description="Server cert", status=ACTIVE, type=SERVER, ca_id=ca_cert.id)
    server_cert.generate(common_name="Test Server", parent=ca_cert)
    # Create an Internal certificate signed by CA
    internal_cert = Certificate(name="Test Internal", description="Internal cert", status=ACTIVE, type=INTERNAL, ca_id=ca_cert.id)
    internal_cert.generate(common_name="Test Internal", parent=ca_cert)
    # Create a client certificate for the admin user, signed by CA
    admin_client_cert = Certificate(name="Admin Client", description="Admin user client cert", status=ACTIVE, type=CLIENT, ca_id=ca_cert.id)
    admin_client_cert.user = admin_user
    admin_client_cert.generate(common_name="Admin Client", parent=ca_cert)
    # Create a client certificate for the normal user, signed by CA
    user_client_cert = Certificate(name="Normal Client", description="Normal user client cert", status=ACTIVE, type=CLIENT, ca_id=ca_cert.id)
    user_client_cert.user = normal_user
    user_client_cert.generate(common_name="Normal Client", parent=ca_cert)
    # Add and commit all new certificates
    db_session.add_all([server_cert, internal_cert, admin_client_cert, user_client_cert])
    db_session.commit()

    return {
        "admin_user": admin_user,
        "normal_user": normal_user,
        "ca_cert": ca_cert,
        "server_cert": server_cert,
        "internal_cert": internal_cert,
        "admin_client_cert": admin_client_cert,
        "normal_client_cert": user_client_cert,
    }

def test_certificate_index_requires_ssl_off(admin_client, db_session):
    """If SSL is not enabled, accessing the certificate index should return 404."""
    # Ensure no 'use_ssl' setting or explicitly set to False
    Setting.set_value("use_ssl", False)
    db_session.commit()
    resp = admin_client.get("/settings/certificates/")
    assert resp.status_code == 404

def test_certificate_index_admin_shows_all_certs(admin_client, setup_certs):
    """Admin user should see all certificates listed on the index page."""
    resp = admin_client.get("/settings/certificates/")
    assert resp.status_code == 200
    # All certificates (CA, Server, Internal, and two clients) should be present
    content = resp.get_data(as_text=True)
    assert "Test CA" in content and "Test Server" in content and "Test Internal" in content
    assert "Admin Client" in content and "Normal Client" in content

def test_certificate_index_user_shows_only_own(admin_client, setup_certs, db_session):
    """Normal user should see only their own certificates on the index page."""
    normal_user = setup_certs["normal_user"]
    # Log out admin and log in as normal user
    admin_client.get("/logout")
    login_resp = admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    }, follow_redirects=True)
    assert login_resp.status_code == 200  # Logged in as normal user
    resp = admin_client.get("/settings/certificates/")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    # Normal user should see their certificate but not the admin's or CA/Server/Internal
    assert "Normal Client" in content
    assert "Admin Client" not in content
    assert "Test Server" not in content
    assert "Test CA" not in content

def test_certificate_generate_page_loads(admin_client, setup_certs):
    """The certificate generation page should load and contain the form."""
    resp = admin_client.get("/settings/certificates/generate")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    # The page should contain form fields for Name and Description
    assert '<form' in content
    assert 'name="name"' in content and 'name="description"' in content

def test_certificate_generate_creates_new_cert(admin_client, setup_certs, db_session):
    """Posting valid data to generate should create a new client certificate."""
    form_data = {"name": "NewCert", "description": "Created via test"}
    resp = admin_client.post("/settings/certificates/generate", data=form_data, follow_redirects=False)
    # After creation, should redirect (likely to detail page or index)
    assert resp.status_code == 302
    # New certificate should be in the database
    new_cert = db_session.query(Certificate).filter_by(name="NewCert").first()
    assert new_cert is not None
    assert new_cert.type == CLIENT
    assert new_cert.status == ACTIVE
    # The certificate should be associated with the admin user (current_user in this context)
    admin_user = setup_certs["admin_user"]
    assert new_cert.user_id == admin_user.id

def test_certificate_generate_form_validation(admin_client, setup_certs, db_session):
    """Submitting invalid data (e.g., missing name) should not create a certificate."""
    # Name is required, so omit it to simulate validation failure
    form_data = {"name": "", "description": "No name"}
    resp = admin_client.post("/settings/certificates/generate", data=form_data)
    # Should return 200 with form errors (no redirect, certificate not created)
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    # Check that an error message is present (WTForms "This field is required." or similar)
    assert "This field is required" in content or "required" in content
    # Verify no certificate was added
    assert db_session.query(Certificate).filter_by(description="No name").first() is None

def test_certificate_view_admin_access(admin_client, setup_certs):
    """Admin can view any certificate details, including CA or other users' certificates."""
    # Admin views the CA certificate detail
    ca = setup_certs["ca_cert"]
    resp1 = admin_client.get(f"/settings/certificates/{ca.id}")
    assert resp1.status_code == 200
    content1 = resp1.get_data(as_text=True)
    assert "Test CA" in content1 and "CA certificate" in content1  # CA details present
    # Admin views another user's client certificate
    user_cert = setup_certs["normal_client_cert"]
    resp2 = admin_client.get(f"/settings/certificates/{user_cert.id}")
    assert resp2.status_code == 200
    content2 = resp2.get_data(as_text=True)
    assert "Normal Client" in content2 and "Client certificate" in content2

def test_certificate_view_user_own_access(admin_client, setup_certs):
    """Normal user can view their own client certificate details."""
    normal_user = setup_certs["normal_user"]
    # Log in as normal user
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    }, follow_redirects=True)
    user_cert = setup_certs["normal_client_cert"]
    resp = admin_client.get(f"/settings/certificates/{user_cert.id}")
    assert resp.status_code == 200
    content = resp.get_data(as_text=True)
    # Page should show the certificate's name and details
    assert "Normal Client" in content and "Client certificate" in content

def test_certificate_view_forbidden_non_owner(admin_client, setup_certs):
    """Normal user cannot view another user's certificate or non-client certificates."""
    normal_user = setup_certs["normal_user"]
    admin_cert = setup_certs["admin_client_cert"]  # Certificate owned by admin
    server_cert = setup_certs["server_cert"]       # Non-client certificate
    # Log in as normal user
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    })
    # Attempt to view admin's client cert (not owned by normal user)
    resp1 = admin_client.get(f"/settings/certificates/{admin_cert.id}")
    assert resp1.status_code in (403, 404)  # Should be forbidden (403). (If not revealing existence, could return 404)
    # Attempt to view a server certificate (type != CLIENT)
    resp2 = admin_client.get(f"/settings/certificates/{server_cert.id}")
    assert resp2.status_code in (403, 404)  # Normal user should not access server cert

def test_certificate_view_not_found(admin_client, setup_certs):
    """Requesting a non-existent certificate ID should return 404."""
    non_id = 99999  # an ID that is not used
    resp = admin_client.get(f"/settings/certificates/{non_id}")
    assert resp.status_code == 404

def test_certificate_download_allows_authorized(admin_client, setup_certs):
    """Certificate download should succeed for an authorized user or admin."""
    # Test as admin downloading another user's certificate
    cert = setup_certs["normal_client_cert"]
    resp = admin_client.get(f"/settings/certificates/{cert.id}/download/tgz")
    # Should return the certificate package as an attachment
    assert resp.status_code == 200
    assert resp.headers.get("Content-Type") == "application/x-gzip"
    content_disp = resp.headers.get("Content-Disposition", "")
    assert f"filename={cert.name}.tar.gz" in content_disp
    # The response data should not be empty (contains archive bytes)
    data = resp.data
    assert data and len(data) > 0

    # Test as normal user downloading their own certificate
    normal_user = setup_certs["normal_user"]
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    })
    cert_user = setup_certs["normal_client_cert"]
    resp2 = admin_client.get(f"/settings/certificates/{cert_user.id}/download/pkcs12")
    assert resp2.status_code == 200
    assert resp2.headers.get("Content-Type") == "application/x-pkcs12"
    content_disp2 = resp2.headers.get("Content-Disposition", "")
    assert f"filename={cert_user.name}.p12" in content_disp2

def test_certificate_download_invalid_type(admin_client, setup_certs):
    """Downloading with an invalid package type key should return 404."""
    cert = setup_certs["admin_client_cert"]
    resp = admin_client.get(f"/settings/certificates/{cert.id}/download/notatype")
    assert resp.status_code == 404

def test_certificate_download_forbidden(admin_client, setup_certs):
    """Normal user cannot download a certificate they do not own."""
    normal_user = setup_certs["normal_user"]
    admin_cert = setup_certs["admin_client_cert"]
    # Log in as normal user
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    })
    resp = admin_client.get(f"/settings/certificates/{admin_cert.id}/download/tgz")
    # Should be forbidden for normal user (their certificate_id not owned by them)
    assert resp.status_code in (403, 404)

def test_certificate_revoke_admin_revokes_any(admin_client, setup_certs, db_session):
    """Admin can revoke any certificate (status changes to Revoked)."""
    cert = setup_certs["normal_client_cert"]  # admin revokes normal user's cert
    resp = admin_client.get(f"/settings/certificates/{cert.id}/revoke")
    assert resp.status_code == 200
    # Reload certificate from DB and verify status is now REVOKED
    cert_db = db_session.query(Certificate).get(cert.id)
    assert cert_db.status == REVOKED
    assert cert_db.revoked_on is not None
    # The response page should show a success flash and updated status
    content = resp.get_data(as_text=True)
    assert "has been revoked" in content
    assert "Status: Revoked" in content or "Revoked" in content

def test_certificate_revoke_user_self(admin_client, setup_certs, db_session):
    """A normal user can revoke their own certificate."""
    normal_user = setup_certs["normal_user"]
    user_cert = setup_certs["normal_client_cert"]
    # Log in as normal user
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    })
    resp = admin_client.get(f"/settings/certificates/{user_cert.id}/revoke")
    # After revocation, should return the certificate view page (200)
    assert resp.status_code == 200
    cert_db = db_session.query(Certificate).get(user_cert.id)
    assert cert_db.status == REVOKED
    # The page content should reflect revocation
    content = resp.get_data(as_text=True)
    assert "has been revoked" in content

def test_certificate_revoke_forbidden(admin_client, setup_certs, db_session):
    """Normal user cannot revoke a certificate they do not own."""
    normal_user = setup_certs["normal_user"]
    admin_cert = setup_certs["admin_client_cert"]  # certificate owned by admin
    # Log in as normal user
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    })
    resp = admin_client.get(f"/settings/certificates/{admin_cert.id}/revoke")
    # Should be forbidden (403) or not found (404) for unauthorized revoke
    assert resp.status_code in (403, 404)
    # Certificate should remain Active
    cert_db = db_session.query(Certificate).get(admin_cert.id)
    assert cert_db.status == ACTIVE

def test_certificate_generate_ca_admin(admin_client, db_session):
    """Admin can generate a new CA along with server and internal certificates."""
    # Ensure no CA exists currently by cleaning up any existing ones
    db_session.query(Certificate).delete()
    db_session.commit()
    resp = admin_client.get("/settings/certificates/generateCA", follow_redirects=False)
    # After generation, should redirect to the certificates index
    assert resp.status_code in (302, 303)
    # Verify that a CA, server, and internal certificate were created in the database
    ca_cert = db_session.query(Certificate).filter_by(type=CA).first()
    server_cert = db_session.query(Certificate).filter_by(type=SERVER).first()
    internal_cert = db_session.query(Certificate).filter_by(type=INTERNAL).first()
    assert ca_cert is not None and server_cert is not None and internal_cert is not None
    # The server and internal certs should reference the CA
    assert server_cert.ca_id == ca_cert.id
    assert internal_cert.ca_id == ca_cert.id

def test_certificate_generate_ca_requires_admin(admin_client, setup_certs):
    """Non-admin users should not be allowed to generate a CA."""
    normal_user = setup_certs["normal_user"]
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    })
    resp = admin_client.get("/settings/certificates/generateCA")
    # Non-admin should get forbidden
    assert resp.status_code == 403 or resp.status_code == 404

def test_certificate_revoke_ca_admin(admin_client, setup_certs, db_session):
    """Admin can revoke the CA and all certificates signed by it (all certs removed)."""
    resp = admin_client.get("/settings/certificates/revokeCA", follow_redirects=False)
    # Should redirect to index after revocation
    assert resp.status_code in (302, 303)
    # All certificates should be removed from the database
    remaining = db_session.query(Certificate).count()
    assert remaining == 0

def test_certificate_revoke_ca_requires_admin(admin_client, setup_certs, db_session):
    """Non-admin users cannot revoke the CA certificate."""
    normal_user = setup_certs["normal_user"]
    admin_client.get("/logout")
    admin_client.post("/login", data={
        "login": normal_user.email, "password": "password"
    })
    resp = admin_client.get("/settings/certificates/revokeCA")
    assert resp.status_code == 403 or resp.status_code == 404
    # Ensure certificates are still intact
    ca_exists = db_session.query(Certificate).filter_by(type=CA).first() is not None
    assert ca_exists
