import json
import pytest
from ad2web.user.models import User, FailedLogin

# --- Access Control Tests ---

@pytest.mark.parametrize("url", [
    "/settings/admin/",
    "/settings/admin/users",
    "/settings/admin/failed_logins"
])
def test_admin_pages_require_admin(normal_client, url):
    """Non-admin users should get 403 Forbidden for admin pages (admin_required)."""
    resp = normal_client.get(url)
    assert resp.status_code == 403  # @admin_required aborts with 403 for non-admin&#8203;:contentReference[oaicite:1]{index=1}


@pytest.mark.parametrize("url, element_snippet", [
    ("/settings/admin/", b"Admin Index Page"),
    ("/settings/admin/users", b'id="users-table"'),
    ("/settings/admin/failed_logins", b'id="failed-table"')
])
def test_admin_pages_accessible_to_admin(admin_client, url, element_snippet):
    """Admin user can access admin index, users list, and failed logins pages."""
    resp = admin_client.get(url)
    assert resp.status_code == 200
    # Verify key element in each page's HTML (e.g., table container or heading).
    assert element_snippet in resp.data  # e.g., users page contains the users table, failed_logins contains its table

# --- Data Listing Tests ---

def test_users_data_listing(admin_client, db_session):
    """GET /settings/admin/users/data returns all users in JSON format."""
    # Pre-create some users in the database (in addition to the admin user).
    initial_count = db_session.query(User).count()
    user1 = User(name="userA", email="userA@example.com", password="pass123", role_code=1, status_code=2)
    user2 = User(name="userB", email="userB@example.com", password="pass123", role_code=1, status_code=2)
    db_session.add_all([user1, user2])
    db_session.commit()
    resp = admin_client.get("/settings/admin/users/data")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    # Should return a list of all users (each with id, name, email, role, status).
    assert "data" in data
    users_list = data["data"]
    # The number of users returned should match the count in the database.
    assert len(users_list) == db_session.query(User).count()
    # The new users should appear in the JSON data by name.
    names = [u["name"] for u in users_list]
    assert "userA" in names and "userB" in names
    # Each entry should include the expected fields.
    sample = users_list[0]
    assert {"id", "name", "email", "role", "status"} <= sample.keys()


def test_failed_logins_data_listing(admin_client, db_session):
    """GET /settings/admin/failed_logins/data returns failed login attempts in JSON, most recent first."""
    # Create some FailedLogin records out of chronological order.
    older = FailedLogin(name="olduser", ip_address="5.5.5.5", user_agent_string="OldAgent", login_time="2025-01-01T12:00:00")
    newer = FailedLogin(name="newuser", ip_address="6.6.6.6", user_agent_string="NewAgent", login_time="2025-01-02T12:00:00")
    db_session.add_all([older, newer])
    db_session.commit()
    resp = admin_client.get("/settings/admin/failed_logins/data")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert "data" in data
    attempts = data["data"]
    # Should list attempts in descending order by time&#8203;:contentReference[oaicite:2]{index=2}.
    assert attempts[0]["name"] == "newuser"
    assert attempts[1]["name"] == "olduser"
    # Each record should contain the expected fields.
    assert {"name", "ip_address", "user_agent", "timestamp"} <= attempts[0].keys()
    # Verify the content matches what was stored.
    assert attempts[0]["ip_address"] == "6.6.6.6" and attempts[0]["user_agent"] == "NewAgent"


def test_failed_logins_data_forbidden_for_non_admin(normal_client):
    """Non-admin users cannot retrieve failed logins data."""
    resp = normal_client.get("/settings/admin/failed_logins/data")
    assert resp.status_code == 403  # admin_required should block this as well

# --- User Creation Tests ---

def test_create_user_success(admin_client, db_session):
    """POST /settings/admin/users/create with valid data should create a new user."""
    new_user_data = {
        "name": "testuser",
        "email": "testuser@example.com",
        "password": "password123",
        "password_again": "password123",
        "role_code": "1",    # normal user role
        "status_code": "2"   # active status
    }
    resp = admin_client.post("/settings/admin/users/create", data=new_user_data)
    # Successful creation returns JSON success True with new user ID&#8203;:contentReference[oaicite:3]{index=3}.
    assert resp.status_code == 200
    result = json.loads(resp.data)
    assert result.get("success") is True
    assert isinstance(result.get("user_id"), int)
    # Verify the user was created in the database with the correct attributes.
    user = db_session.query(User).filter_by(name="testuser").one()
    assert user.email == "testuser@example.com"
    assert user.role_code == 1 and user.status_code == 2
    # Password should be set (hashed) and check_password should validate the raw password.
    assert user.check_password("password123")


def test_create_user_validation_errors(admin_client):
    """POST /settings/admin/users/create with invalid data should return errors and 400 status."""
    # Prepare data with multiple validation issues:
    data = {
        "name": "",               # missing username (required)
        "email": "not-an-email",  # invalid email format (no Email validator in admin form)
        "password": "123",        # too short (below min length 6)
        "password_again": "456",  # does not match password
        "role_code": "1",
        "status_code": "2"
    }
    resp = admin_client.post("/settings/admin/users/create", data=data)
    # Validation failures return 400 with success False and errors dict&#8203;:contentReference[oaicite:4]{index=4}.
    assert resp.status_code == 400
    result = json.loads(resp.data)
    assert result.get("success") is False
    errors = result.get("errors")
    # Expect errors for required fields and mismatched passwords.
    assert "name" in errors  # name required
    assert "password" in errors  # password too short or mismatch triggers error on password field
    assert any("match" in msg or "match" in str(msg) for msg in errors.get("password", []))  # "Passwords must match" expected
    # Since admin form did not enforce email format, 'email' might not have an error for format (only would if blank).
    # If email were blank, InputRequired would trigger. Here it's non-blank but invalid format; admin form might not catch it.

def test_create_user_duplicate(admin_client, db_session):
    """Creating a user with a duplicate username or email should fail with 409 conflict."""
    # Add an existing user to conflict with.
    existing = User(name="duplicate", email="dup@example.com", password="abc123", role_code=1, status_code=2)
    db_session.add(existing)
    db_session.commit()
    # Attempt to create another user with the same username.
    dup_data = {
        "name": "duplicate",               # same name as existing user
        "email": "other@example.com",
        "password": "password123",
        "password_again": "password123",
        "role_code": "1",
        "status_code": "2"
    }
    resp = admin_client.post("/settings/admin/users/create", data=dup_data)
    # The service should raise DuplicateUserError&#8203;:contentReference[oaicite:5]{index=5}, causing a 409 response.
    assert resp.status_code == 409
    result = json.loads(resp.data)
    assert result.get("success") is False
    # Error message should indicate duplicate username/email.
    assert "error" in result and "already exists" in result["error"]
    # Ensure no duplicate user was added to the database.
    count = db_session.query(User).filter(User.name == "duplicate").count()
    assert count == 1  # still only the original user exists

# --- User Editing/Updating Tests ---

def test_edit_user_success(admin_client, db_session):
    """POST /settings/admin/users/<id>/edit with valid data (no password change) updates the user."""
    # Create a user to edit.
    user = User(name="editme", email="editme@example.com", password="oldpass1", role_code=1, status_code=2)
    db_session.add(user)
    db_session.commit()
    original_id = user.id
    original_pw_hash = user.password  # hashed password before edit
    # Prepare update data: change name, email, role, status; leave password blank to keep old password.
    update_data = {
        "name": "editeduser",
        "email": "edited@example.com",
        "password": "",              # no new password provided
        "password_again": "",        # no new password confirmation
        "role_code": "0",            # change role to admin
        "status_code": "0"           # change status to inactive
    }
    resp = admin_client.post(f"/settings/admin/users/{original_id}/edit", data=update_data)
    assert resp.status_code == 200
    result = json.loads(resp.data)
    assert result.get("success") is True
    # Verify changes in the database.
    edited = db_session.query(User).get(original_id)
    assert edited is not None
    assert edited.name == "editeduser"
    assert edited.email == "edited@example.com"
    assert edited.role_code == 0 and edited.status_code == 0  # now an admin, inactive
    # Password should remain unchanged (empty input should not override existing password)&#8203;:contentReference[oaicite:6]{index=6}.
    assert edited.password == original_pw_hash
    assert edited.check_password("oldpass1")  # old password still valid


def test_edit_user_change_password(admin_client, db_session):
    """Editing a user with a new password should update the password."""
    user = User(name="pwduser", email="pwduser@example.com", password="initialPwd", role_code=1, status_code=2)
    db_session.add(user)
    db_session.commit()
    user_id = user.id
    assert user.check_password("initialPwd")
    update_data = {
        "name": "pwduser",  # keep same name/email
        "email": "pwduser@example.com",
        "password": "NewPassword1",
        "password_again": "NewPassword1",
        "role_code": "1",
        "status_code": "2"
    }
    resp = admin_client.post(f"/settings/admin/users/{user_id}/edit", data=update_data)
    assert resp.status_code == 200
    result = json.loads(resp.data)
    assert result.get("success") is True
    # Fetch the user and verify password changed.
    updated = db_session.query(User).get(user_id)
    assert updated.check_password("NewPassword1")
    assert not updated.check_password("initialPwd")


def test_edit_user_duplicate(admin_client, db_session):
    """Updating a user to a username/email that already exists should yield a conflict."""
    # Create two users.
    user1 = User(name="user1", email="u1@example.com", password="pass123", role_code=1, status_code=2)
    user2 = User(name="user2", email="u2@example.com", password="pass123", role_code=1, status_code=2)
    db_session.add_all([user1, user2])
    db_session.commit()
    # Try to update user2 to have user1's username.
    dup_data = {
        "name": "user1",               # name already taken by user1
        "email": "u2@example.com",
        "password": "", "password_again": "",
        "role_code": "1",
        "status_code": "2"
    }
    resp = admin_client.post(f"/settings/admin/users/{user2.id}/edit", data=dup_data)
    assert resp.status_code == 409
    result = json.loads(resp.data)
    assert result.get("success") is False and "error" in result
    assert "already exists" in result["error"]  # expecting duplicate user error message
    # Ensure user2's data was not altered (operation rolled back on error&#8203;:contentReference[oaicite:7]{index=7}).
    unchanged = db_session.query(User).get(user2.id)
    assert unchanged.name == "user2" and unchanged.email == "u2@example.com"


def test_edit_user_not_found(admin_client):
    """Editing a non-existent user ID should return 404."""
    # Use a high ID that doesn't exist in DB.
    data = {
        "name": "nouser",
        "email": "no@user.com",
        "password": "irrelevant",
        "password_again": "irrelevant",
        "role_code": "1",
        "status_code": "2"
    }
    resp = admin_client.post("/settings/admin/users/9999/edit", data=data)
    # The view should abort with 404 if the user isn't found&#8203;:contentReference[oaicite:8]{index=8}&#8203;:contentReference[oaicite:9]{index=9}.
    assert resp.status_code == 404

# --- User Deletion Tests ---

def test_delete_user_success(admin_client, db_session):
    """POST /settings/admin/users/<id>/delete should delete the specified user (if not primary admin)."""
    user = User(name="todelete", email="todelete@example.com", password="pass123", role_code=1, status_code=2)
    db_session.add(user)
    db_session.commit()
    user_id = user.id
    resp = admin_client.post(f"/settings/admin/users/{user_id}/delete")
    # Successful deletion returns success True&#8203;:contentReference[oaicite:10]{index=10}.
    assert resp.status_code == 200
    result = json.loads(resp.data)
    assert result.get("success") is True
    # Verify the user is removed from the database.
    assert db_session.query(User).get(user_id) is None


def test_delete_primary_admin_forbidden(admin_client, db_session):
    """Attempting to delete the primary admin user should be prevented."""
    # Assuming the primary admin user has ID 1 (by design)&#8203;:contentReference[oaicite:11]{index=11}.
    admin_user = db_session.query(User).get(1)
    assert admin_user is not None and admin_user.role_code == 0  # ensure user 1 is admin
    resp = admin_client.post("/settings/admin/users/1/delete")
    # Should return 400 with success False (not allowed to delete primary admin)&#8203;:contentReference[oaicite:12]{index=12}.
    assert resp.status_code == 400
    result = json.loads(resp.data)
    assert result.get("success") is False
    # Primary admin should still exist in DB.
    assert db_session.query(User).get(1) is not None


def test_delete_user_not_found(admin_client):
    """Deleting a non-existent user ID should return a failure (400)."""
    resp = admin_client.post("/settings/admin/users/9999/delete")
    # Service returns False for not found, leading to 400 response&#8203;:contentReference[oaicite:13]{index=13}.
    assert resp.status_code == 400
    result = json.loads(resp.data)
    assert result.get("success") is False
