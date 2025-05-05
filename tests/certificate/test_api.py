import pytest
from ad2web.certificate.models import Certificate
from ad2web.certificate.constants import CA, CLIENT, SERVER, INTERNAL, ACTIVE, REVOKED
from ad2web.services import certificate_service

# Reuse the patch_filesystem fixture from test_views to avoid duplication
from tests.certificate.test_views import patch_filesystem, setup_certs

def test_list_certificates_service(setup_certs):
    """certificate_service.list_certificates returns all certs for admin and only own for normal user."""
    admin_user = setup_certs["admin_user"]
    normal_user = setup_certs["normal_user"]
    # Admin should get all certificates
    all_certs = certificate_service.list_certificates(admin_user)
    cert_names = {c.name for c in all_certs}
    # Expect at least the ones created in setup_certs
    assert {"Test CA", "Test Server", "Test Internal", "Admin Client", "Normal Client"} <= cert_names
    # Normal user should get only their certificate(s)
    user_certs = certificate_service.list_certificates(normal_user)
    assert all(cert.user_id == normal_user.id for cert in user_certs)
    user_cert_names = [cert.name for cert in user_certs]
    # The normal user in setup_certs has exactly one client certificate
    assert user_cert_names == ["Normal Client"]

def test_generate_certificate_service(setup_certs, db_session):
    """certificate_service.generate_certificate should create a new client certificate."""
    user = setup_certs["normal_user"]
    initial_count = db_session.query(Certificate).count()
    new_cert = certificate_service.generate_certificate("ServiceCert", "Created via service", user)
    # After generation, a new Certificate record is committed
    assert db_session.query(Certificate).count() == initial_count + 1
    assert new_cert.name == "ServiceCert"
    assert new_cert.status == ACTIVE
    assert new_cert.type == CLIENT
    # Certificate should be associated with the given user
    assert new_cert.user_id == user.id
    # The certificate data (key and cert) should be populated
    assert new_cert.certificate is not None and new_cert.key is not None

def test_revoke_certificate_service(setup_certs, db_session):
    """certificate_service.revoke_certificate should mark a certificate as revoked."""
    cert = setup_certs["admin_client_cert"]
    cert_id = cert.id
    # Ensure certificate is active initially
    assert cert.status == ACTIVE
    revoked = certificate_service.revoke_certificate(cert_id)
    # Should return the revoked Certificate object
    assert revoked is not None
    assert revoked.status == REVOKED
    assert revoked.revoked_on is not None
    # The change should be persisted in the database
    cert_db = db_session.query(Certificate).get(cert_id)
    assert cert_db.status == REVOKED
    # Revoking a non-existent certificate returns None
    assert certificate_service.revoke_certificate(99999) is None

def test_generate_ca_certificates_service(db_session):
    """certificate_service.generate_ca_certificates should create a CA, server, and internal cert."""
    # Start with no certificates in DB
    db_session.query(Certificate).delete()
    db_session.commit()
    ca_cert, server_cert, internal_cert = certificate_service.generate_ca_certificates()
    # All three returned objects should be not None
    assert ca_cert and server_cert and internal_cert
    assert ca_cert.type == CA
    assert server_cert.type == SERVER and server_cert.ca_id == ca_cert.id
    assert internal_cert.type == INTERNAL and internal_cert.ca_id == ca_cert.id
    # They should be committed to the database
    ca_db = Certificate.query.filter_by(type=CA).first()
    server_db = Certificate.query.filter_by(type=SERVER).first()
    internal_db = Certificate.query.filter_by(type=INTERNAL).first()
    assert ca_db is not None and server_db is not None and internal_db is not None

def test_revoke_ca_certificates_service(setup_certs, db_session):
    """certificate_service.revoke_ca_certificates should remove all certificates."""
    # Ensure a CA exists before revocation
    ca_before = db_session.query(Certificate).filter_by(type=CA).first()
    assert ca_before is not None
    result = certificate_service.revoke_ca_certificates()
    assert result is True
    # After revocation, there should be no certificates left
    remaining = db_session.query(Certificate).count()
    assert remaining == 0
    # Calling again when no CA exists should return False
    result2 = certificate_service.revoke_ca_certificates()
    assert result2 is False

def test_create_certificate_package_service(setup_certs):
    """certificate_service.create_certificate_package returns a tuple for valid input, or None for invalid cases."""
    cert = setup_certs["normal_client_cert"]
    # Valid package generation (tgz)
    result = certificate_service.create_certificate_package(cert.id, "tgz")
    assert result is not None
    mime_type, filename, data = result
    assert mime_type == "application/x-gzip"
    assert filename.endswith(".tar.gz")
    assert isinstance(data, (bytes, bytearray)) and len(data) > 0
    # Valid package generation (pkcs12)
    result2 = certificate_service.create_certificate_package(cert.id, "pkcs12")
    assert result2 is not None
    mime_type2, filename2, data2 = result2
    assert mime_type2 == "application/x-pkcs12"
    assert filename2.endswith(".p12")
    # Invalid package type
    assert certificate_service.create_certificate_package(cert.id, "invalid") is None
    # Invalid certificate ID
    assert certificate_service.create_certificate_package(99999, "tgz") is None
    # If no CA exists (e.g., after revoking CA), should return None
    certificate_service.revoke_ca_certificates()
    assert certificate_service.create_certificate_package(cert.id, "tgz") is None
