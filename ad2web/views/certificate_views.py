from flask import Blueprint, render_template, request, flash, abort, redirect, url_for, Response
from flask_login import login_required, current_user
from ad2web.decorators import admin_required
from ad2web.forms.certificate_form import GenerateCertificateForm
from ad2web.services import certificate_service
from ad2web.certificate.constants import CERTIFICATE_TYPES, CERTIFICATE_STATUS, CLIENT, CA, PACKAGE_TYPE_LOOKUP

certificate = Blueprint('certificate', __name__, url_prefix='/settings/certificates')

@certificate.context_processor
def certificate_context_processor():
    """Inject certificate type and status mappings into template context."""
    return {'TYPES': CERTIFICATE_TYPES, 'STATUS': CERTIFICATE_STATUS}

@certificate.route('/', methods=['GET'])
@login_required
def index():
    # Ensure SSL feature is enabled
    if not certificate_service.is_ssl_enabled():
        abort(404)
    # Retrieve certificates list and CA certificate
    certificates = certificate_service.list_certificates(current_user)
    ca_cert = certificate_service.get_ca_certificate()
    return render_template('certificate/index.html',
                           certificates=certificates, ca_cert=ca_cert,
                           active='certificates', ssl=True)

@certificate.route('/generate', methods=['GET', 'POST'])
@login_required
def generate():
    if not certificate_service.is_ssl_enabled():
        abort(404)
    form = GenerateCertificateForm()
    if form.validate_on_submit():
        # Create a new certificate via service
        new_cert = certificate_service.generate_certificate(form.name.data, form.description.data, current_user)
        flash('Certificate created successfully.', 'success')
        # Redirect to detail view of the newly created certificate for download links
        return redirect(url_for('certificate.view', certificate_id=new_cert.id))
    return render_template('certificate/generate.html', form=form, active='certificates', ssl=True)

@certificate.route('/<int:certificate_id>')
@login_required
def view(certificate_id):
    if not certificate_service.is_ssl_enabled():
        abort(404)
    cert = certificate_service.get_certificate(certificate_id)
    if not cert:
        abort(404)
    # Only admins can view CA/Server/Internal certs; non-admins can only view their own client certs
    if cert.type != CLIENT and not current_user.is_admin:
        abort(403)
    if cert.user_id is not None and cert.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    ca_cert = certificate_service.get_ca_certificate()
    return render_template('certificate/view.html', certificate=cert, ca_cert=ca_cert,
                           current_user=current_user, active='certificates', ssl=True)

@certificate.route('/<int:certificate_id>/download/<download_type>')
@login_required
def download(certificate_id, download_type):
    # Only allow known package types (tgz, pkcs12, bks)
    if download_type not in PACKAGE_TYPE_LOOKUP:
        abort(404)
    if not certificate_service.is_ssl_enabled():
        abort(404)
    cert = certificate_service.get_certificate(certificate_id)
    if not cert:
        abort(404)
    # Only owner or admin can download this certificate
    if cert.user_id is not None and cert.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    # Prepare the certificate package for download
    result = certificate_service.create_certificate_package(certificate_id, download_type)
    if result is None:
        abort(404)  # invalid certificate or package type
    mime_type, filename, data = result
    return Response(data, mimetype=mime_type,
                    headers={
                        'Content-Type': mime_type,
                        'Content-Disposition': f'attachment; filename={filename}'
                    })

@certificate.route('/<int:certificate_id>/revoke')
@login_required
def revoke(certificate_id):
    if not certificate_service.is_ssl_enabled():
        abort(404)
    cert = certificate_service.get_certificate(certificate_id)
    if not cert:
        abort(404)
    # Only owner or admin can revoke (and typically only client certs have owners)
    if cert.user_id is not None and cert.user_id != current_user.id and not current_user.is_admin:
        abort(403)
    certificate_service.revoke_certificate(certificate_id)
    flash('The certificate has been revoked.', 'success')
    # Show the updated certificate details (status should now be "Revoked")
    return render_template('certificate/view.html', certificate=cert,
                           ssl=True, current_user=current_user)

@certificate.route('/generateCA')
@login_required
@admin_required
def generateCA():
    if not certificate_service.is_ssl_enabled():
        abort(404)
    # Create CA, server, and internal certificates (admin only)
    certificate_service.generate_ca_certificates()
    # Redirect to certificate list (SSL will be in use with new CA)
    return redirect(url_for('certificate.index'))

@certificate.route('/revokeCA')
@login_required
@admin_required
def revokeCA():
    if not certificate_service.is_ssl_enabled():
        abort(404)
    certificate_service.revoke_ca_certificates()
    return redirect(url_for('certificate.index'))
