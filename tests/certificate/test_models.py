import os
from ad2web.certificate.models import Certificate
from ad2web.certificate.constants import ACTIVE, REVOKED, CA, CLIENT

def test_certificate_model_revoke():
    """Certificate.revoke() method should update status and set revoked_on timestamp."""
    cert = Certificate(name="Temp Cert", description="temp", status=ACTIVE, type=CLIENT)
    cert.revoke()
    assert cert.status == REVOKED
    assert cert.revoked_on is not None

def test_certificate_model_generate_and_serial(db_session):
    """Certificate.generate() should populate key and certificate fields and assign serial numbers."""
    # Create a CA certificate
    ca = Certificate(name="GenCA", description="Gen CA", status=ACTIVE, type=CA)
    ca.generate(common_name="GenCA")
    db_session.add(ca)
    db_session.commit()
    # Create two client certificates to test serial number increment
    cert1 = Certificate(name="GenClient1", description="Gen client1", status=ACTIVE, type=CLIENT, ca_id=ca.id)
    cert1.generate(common_name="GenClient1", parent=ca)
    cert2 = Certificate(name="GenClient2", description="Gen client2", status=ACTIVE, type=CLIENT, ca_id=ca.id)
    cert2.generate(common_name="GenClient2", parent=ca)
    # After generation, each cert should have certificate/key data and unique serials
    assert cert1.certificate is not None and cert1.key is not None
    assert cert2.certificate is not None and cert2.key is not None
    assert cert1.serial_number != cert2.serial_number

def test_certificate_model_export(tmp_path):
    """Certificate.export() should write the key and certificate PEM files to the given path."""
    cert = Certificate(name="ExportTest", description="For export test", status=ACTIVE, type=CLIENT)
    # Simulate certificate content (as strings for test)
    cert.key = "KEYDATA"
    cert.certificate = "CERTDATA"
    export_dir = tmp_path / "export_dir"
    os.makedirs(export_dir, exist_ok=True)
    cert.export(str(export_dir))
    # Verify that the files were created with correct contents
    key_path = export_dir / f"{cert.name}.key"
    pem_path = export_dir / f"{cert.name}.pem"
    assert key_path.exists() and pem_path.exists()
    assert key_path.read_text() == "KEYDATA"
    assert pem_path.read_text() == "CERTDATA"
