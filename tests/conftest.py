# tests/conftest.py
import pytest
from ad2web import create_app
from ad2web.config import TestConfig
from ad2web.extensions import db
from ad2web.user.models import User
from ad2web.user.constants import ADMIN, USER, ACTIVE
import os
import tempfile
import subprocess
# Patch background threads to prevent them from running during tests
try:
    import ad2web.services.decoder_service as _dec
    setattr(_dec.DecoderService, "start", lambda self: None)
    setattr(_dec.DecoderService, "init", lambda self: None)
except ImportError:
    pass
try:
    import ad2web.discovery as _disc
    setattr(_disc, "start_discovery", lambda: None)
except ImportError:
    pass

class TestConfig:
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False

    # override the instance folder for *tests* so it can be created under cwd
    INSTANCE_FOLDER_PATH = os.path.abspath(
        os.path.join(os.getcwd(), 'instance-test')
    )

    # also override any other file-system paths you write to:
    LOG_FOLDER      = os.path.join(INSTANCE_FOLDER_PATH, 'logs')
    UPLOAD_FOLDER   = os.path.join(INSTANCE_FOLDER_PATH, 'uploads')
    OPENID_FS_STORE_PATH = os.path.join(INSTANCE_FOLDER_PATH, 'openid_store')

@pytest.fixture(scope="session")
def app():
    # Tear down any leftover test instance folder, then create a fresh one.
    inst = TestConfig.INSTANCE_FOLDER_PATH
    if os.path.exists(inst):
        if os.name == 'nt':
            # Windows: rmdir /S /Q
            subprocess.run(
            ['cmd', '/c', 'rmdir', '/S', '/Q', inst],
                check = True,
                shell = False
            )
        else:
            # Unix-like: rm -rf
            subprocess.run(
                ['rm', '-rf', inst],
                check = True,
                shell = False
        )

    app = create_app(TestConfig)

    return app

@pytest.fixture(autouse=True)
def app_context(app):
    """
    Push a test_request_context around every test, so FlaskForm
    and current_app are always available.
    """
    with app.test_request_context():
        yield

@pytest.fixture
def client(app):
    """A test client for making requests."""
    return app.test_client()

@pytest.fixture
def runner(app):
    """A Click runner for invoking CLI commands."""
    return app.test_cli_runner()

@pytest.fixture(scope="function")
def db_session(app):
    """Yield a fresh database for each test function."""
    ctx = app.app_context()
    ctx.push()
    db.create_all()
    yield db  # provide the SQLAlchemy db object
    db.session.remove()
    db.drop_all()
    ctx.pop()

@pytest.fixture(scope="function")
def client(app, db_session):
    """Provide a test HTTP client. Database is set up via db_session."""
    return app.test_client()

@pytest.fixture(scope="function")
def admin_user(db_session):
    """Create an admin user in the test database."""
    admin = User(name="admin", email="admin@example.com", password="password",
                 role_code=ADMIN, status_code=ACTIVE)
    db.session.add(admin)
    db.session.commit()
    return admin

@pytest.fixture(scope="function")
def normal_user(db_session, admin_user):
    """Create a regular user in the test database (after admin exists)."""
    user = User(name="user", email="user@example.com", password="password",
                role_code=USER, status_code=ACTIVE)
    db.session.add(user)
    db.session.commit()
    return user

@pytest.fixture(scope="function")
def admin_client(client, admin_user):
    """Log in as the admin user and return a client with that session."""
    login_data = {"login": admin_user.email, "password": "password"}
    res = client.post("/login", data=login_data)
    # After a successful login, flask_login will redirect to the index
    assert res.status_code in (302, 200)
    return client

@pytest.fixture(scope="function")
def normal_client(client, normal_user):
    """Log in as a normal user and return a client with that session."""
    login_data = {"login": normal_user.email, "password": "password"}
    res = client.post("/login", data=login_data)
    assert res.status_code in (302, 200)
    return client
