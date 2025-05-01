# fabfile.py  (Fabric 2.x)

import os
from invoke import run as local
from fabric import task

from ad2web import create_app
from ad2web.extensions import db
from ad2web.utils import INSTANCE_FOLDER_PATH
from ad2web.settings.models import Setting
from ad2web.certificate.models import Certificate
from ad2web.certificate.constants import ACTIVE, CA, SERVER, INTERNAL, CLIENT
from ad2web.ser2sock import ser2sock

# Virtualenv and script paths
VENV_DIR    = "env"
PYTHON      = os.path.join(VENV_DIR, "bin", "python")
MANAGE_PY   = "manage.py"

@task
def reset(c):
    """
    Reset the local debug environment (clears instance folder + reinitializes the DB).
    """
    print("--- Resetting local environment ---")
    local(f"rm -rf {INSTANCE_FOLDER_PATH}")
    local(f"mkdir -p {INSTANCE_FOLDER_PATH}")
    local(f"{PYTHON} {MANAGE_PY} initdb", pty=True)
    print("--- Reset complete ---")

@task
def setup(c):
    """
    Create a Python3 venv (if needed), install the project, then reset DB.
    """
    print("--- Setting up virtual environment ---")
    if not os.path.isdir(VENV_DIR):
        local("python3 -m venv env")
        print("  Created virtualenv 'env'.")
    else:
        print("  Virtualenv 'env' already exists.")

    print("--- Installing dependencies ---")
    local(f"{PYTHON} -m pip install --upgrade pip", pty=True)
    local(f"{PYTHON} -m pip install -e .", pty=True)

    # Initialize DB after install
    reset(c)
    print("--- Setup complete ---")

@task(help={'skip-reset': "Skip the database reset before running the dev server"})
def d(c, skip_reset=False):
    """
    Run the development server. Resets DB by default unless --skip-reset is given.
    """
    print("--- Starting development server ---")
    if not skip_reset:
        reset(c)
    local(f"{PYTHON} {MANAGE_PY} run", pty=True)

@task
def certs(c):
    """
    Generate default certificates (CA, server, internal, two clients) and export them.
    """
    print("--- Generating default certificates ---")
    reset(c)

    app, socketio = create_app()
    with app.app_context():
        # Ensure the ser2sock config path is set in the DB
        setting = Setting.query.filter_by(name="ser2sock_config_path").first()
        if not setting:
            setting = Setting(name="ser2sock_config_path", value="/etc/ser2sock")
            db.session.add(setting)
            db.session.commit()
            print(f"  Created setting ser2sock_config_path = {setting.value}")
        else:
            print(f"  Using existing ser2sock_config_path = {setting.value}")

        export_path = os.path.join(setting.value, "certs")
        os.makedirs(export_path, exist_ok=True)
        print(f"  Export path: {export_path}")

        # Build certificates
        ca = Certificate(name="AlarmDecoder CA", status=ACTIVE,   type=CA)
        ca.generate(ca.name)
        server = Certificate(name="AlarmDecoder Server", status=ACTIVE, type=SERVER)
        server.generate(server.name, parent=ca)
        internal = Certificate(name="AlarmDecoder Internal", status=ACTIVE, type=INTERNAL)
        internal.generate(internal.name, parent=ca)
        test1 = Certificate(name="Test #1", status=ACTIVE, type=CLIENT)
        test1.generate(test1.name, parent=ca)
        test2 = Certificate(name="Test #2", status=ACTIVE, type=CLIENT)
        test2.generate(test2.name, parent=ca)

        db.session.add_all([ca, server, internal, test1, test2])
        db.session.commit()

        # Export to disk and update CRLs
        for cert in (ca, server, internal, test1, test2):
            cert.export(export_path)
        Certificate.save_certificate_index()
        Certificate.save_revocation_list()
        ser2sock.hup()
        print("  Certificates generated, exported, and ser2sock reloaded.")

    print("--- Certificate generation complete ---")

@task(help={'name': "Common Name (CN) of the certificate to revoke"})
def revoke_cert(c, name):
    """
    Revoke a client certificate by name and reload ser2sock.
    """
    if not name:
        print("Error: --name is required")
        return

    print(f"--- Revoking certificate: {name} ---")
    app, socketio = create_app()
    with app.app_context():
        cert = Certificate.query.filter_by(name=name).first()
        if not cert:
            print(f"Error: Certificate '{name}' not found.")
            return
        if cert.is_revoked:
            print(f"Certificate '{name}' is already revoked.")
            return

        cert.revoke()
        Certificate.save_certificate_index()
        Certificate.save_revocation_list()
        db.session.commit()
        ser2sock.hup()
        print(f"Certificate '{name}' successfully revoked.")

    print("--- Revocation complete ---")

@task
def babel(c):
    """
    Compile message catalogs via Babel (example: Chinese locale).
    """
    print("--- Compiling Babel translations ---")
    local(
        f"{PYTHON} setup.py compile_catalog "
        f"--directory `find . -name translations` --locale zh -f",
        pty=True
    )
    print("--- Babel compilation complete ---")
