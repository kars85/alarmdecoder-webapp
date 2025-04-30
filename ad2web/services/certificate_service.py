from ad2web.extensions import db
from ad2web.certificate.models import Certificate, CertificatePackage
from ad2web.certificate.constants import CA, CLIENT, SERVER, INTERNAL, ACTIVE, REVOKED, PACKAGE_TYPE_LOOKUP
from ad2web.settings.models import Setting
from ad2web.ser2sock import ser2sock

def is_ssl_enabled():
    """Check if SSL usage is enabled in settings."""
    value = Setting.get_by_name('use_ssl', default=False).value
    return True if value else False

def get_ca_certificate():
    """Retrieve the CA certificate (if any)."""
    return Certificate.query.filter_by(type=CA).first()

def list_certificates(user):
    """
    Retrieve certificates accessible to the given user.
    Admin users get all certificates; regular users get only their own.
    """
    if user.is_admin():
        return Certificate.query.all()
    # Non-admin: only certificates owned by this user
    return Certificate.query.filter_by(user_id=user.id).all()

def get_certificate(certificate_id):
    """Fetch a Certificate by ID, or return None if not found."""
    return Certificate.query.filter_by(id=certificate_id).first()

def generate_certificate(name, description, user):
    """
    Generate a new client certificate (or CA if none exists).
    Creates a Certificate record with key and certificate data.
    """
    parent_ca = get_ca_certificate()  # existing CA certificate if any
    cert = Certificate(name=name, description=description, status=ACTIVE)
    cert.user = user  # associate certificate with the requesting user
    if parent_ca is not None:
        cert.type = CLIENT
        cert.ca_id = parent_ca.id
    else:
        cert.type = CLIENT  # if no CA exists, this will be a self-signed certificate
    # Generate key and certificate using crypto library (via model method)
    cert.generate(common_name=name, parent=parent_ca)
    db.session.add(cert)
    db.session.commit()
    return cert

def generate_ca_certificates():
    """
    Initialize a new CA along with a server and internal certificate signed by the CA.
    Returns the created (ca_cert, server_cert, internal_cert).
    """
    # Create CA certificate (self-signed)
    ca_cert = Certificate(
        name="AlarmDecoder CA",
        description="CA certificate used for authenticating others.",
        status=ACTIVE,
        type=CA
    )
    ca_cert.generate(common_name="AlarmDecoder CA")
    db.session.add(ca_cert)
    db.session.flush()  # flush to assign ID to CA certificate

    # Create server certificate signed by the new CA
    server_cert = Certificate(
        name="AlarmDecoder Server",
        description="Server certificate used by ser2sock.",
        status=ACTIVE,
        type=SERVER,
        ca_id=ca_cert.id
    )
    server_cert.generate(common_name="AlarmDecoder Server", parent=ca_cert)
    db.session.add(server_cert)

    # Create internal certificate signed by the new CA
    internal_cert = Certificate(
        name="AlarmDecoder Internal",
        description="Internal certificate used to communicate with ser2sock.",
        status=ACTIVE,
        type=INTERNAL,
        ca_id=ca_cert.id
    )
    internal_cert.generate(common_name="AlarmDecoder Internal", parent=ca_cert)
    db.session.add(internal_cert)

    # Update certificate index, CRL, and ser2sock config if applicable
    config_path_setting = Setting.get_by_name('ser2sock_config_path')
    if config_path_setting:
        Certificate.save_certificate_index()
        Certificate.save_revocation_list()
        ser2sock.update_config(config_path_setting.value, ca_cert=ca_cert, server_cert=server_cert, use_ssl=True)

    db.session.commit()
    return ca_cert, server_cert, internal_cert

def revoke_certificate(certificate_id):
    """
    Revoke a certificate by ID: mark as revoked and update certificate index/CRL.
    Returns the revoked Certificate object, or None if not found.
    """
    cert = Certificate.query.filter_by(id=certificate_id).first()
    if not cert:
        return None
    cert.revoke()  # update status and revoked_on
    Certificate.save_certificate_index()
    Certificate.save_revocation_list()
    db.session.add(cert)  # ensure the object is marked in session
    db.session.commit()
    # Signal ser2sock to reload certificates (SIGHUP)
    ser2sock.hup()
    return cert

def revoke_ca_certificates():
    """
    Revoke (delete) the CA certificate and all certificates signed by it.
    This will remove all certificates from the database.
    """
    ca_cert = Certificate.query.filter_by(type=CA).first()
    if not ca_cert:
        return False  # no CA to revoke
    # Delete all non-CA certificates signed by this CA
    Certificate.query.filter_by(ca_id=ca_cert.id).delete()
    # Delete the CA certificate itself
    Certificate.query.filter_by(type=CA).delete()
    db.session.commit()
    return True

def create_certificate_package(certificate_id, package_type_key):
    """
    Create a downloadable certificate package for the given certificate.
    `package_type_key` should be one of 'tgz', 'pkcs12', or 'bks'.
    Returns a tuple of (mime_type, filename, data) for the package, or None if invalid.
    """
    if package_type_key not in PACKAGE_TYPE_LOOKUP:
        return None  # invalid package type
    cert = Certificate.query.filter_by(id=certificate_id).first()
    if not cert:
        return None
    ca_cert = get_ca_certificate()
    if not ca_cert:
        return None  # CA must exist to create a package
    package = CertificatePackage(cert, ca_cert)
    mime_type, filename, data = package.create(package_type=PACKAGE_TYPE_LOOKUP[package_type_key])
    return mime_type, filename, data
