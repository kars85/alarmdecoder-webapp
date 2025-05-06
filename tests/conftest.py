# tests/conftest.py
import pytest
from ad2web import create_app
from ad2web.config import TestConfig
from ad2web.extensions import db
from ad2web.user.models import User
from ad2web.user.constants import ADMIN, USER, ACTIVE

# Patch background threads to prevent them from running during tests
try:
    import ad2web.decoder as _dec
    setattr(_dec.DecoderService, "start", lambda self: None)
    setattr(_dec.DecoderService, "init", lambda self: None)
except ImportError:
    pass
try:
    import ad2web.discovery as _disc
    setattr(_disc, "start_discovery", lambda: None)
except ImportError:
    pass

@pytest.fixture(scope="session")
def app():
    """Create Flask app with testing config once for all tests."""
    app = create_app(TestConfig)  # uses in-memory DB, TESTING=True
    return app

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
