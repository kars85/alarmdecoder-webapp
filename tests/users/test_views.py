# tests/users/test_views.py
import json
from ad2web.user.models import User

def test_login_required_for_admin_pages(client, normal_user):
    # Without login, accessing admin users page should redirect to login
    res = client.get("/settings/admin/users", follow_redirects=False)
    assert res.status_code == 302
    assert "/login" in res.headers.get("Location", "")
    # Login as normal (non-admin) user
    client.post("/login", data={"login": normal_user.email, "password": "password"})
    # Now accessing an admin-only page should be forbidden (403)
    res2 = client.get("/settings/admin/users")
    assert res2.status_code == 403

def test_admin_list_users_page(admin_client):
    # Logged in as admin, should get the users list page
    res = admin_client.get("/settings/admin/users")
    assert res.status_code == 200
    html = res.data.decode()
    # Check that table headers are present (page structure is correct)
    assert "Username" in html and "Status" in html and "Role" in html

def test_admin_users_data_endpoint(admin_client):
    # Fetch the user list data (JSON) via AJAX endpoint
    res = admin_client.get("/settings/admin/users/data")
    assert res.status_code == 200
    payload = res.get_json() or json.loads(res.data.decode())
    # The response JSON contains a 'data' list of users
    assert "data" in payload
    users_list = payload["data"]
    # Verify the admin user appears in the data
    assert any(u["email"] == "admin@example.com" for u in users_list)

def test_admin_create_user_via_api(admin_client):
    # Post a new user via the admin create API
    new_user_data = {
        "name": "newuser",
        "email": "newuser@example.com",
        "password": "abc123",
        "password_again": "abc123",
        "role_code": "1",   # normal user role
        "status_code": "2"  # active status
    }
    res = admin_client.post("/settings/admin/users/create", data=new_user_data)
    assert res.status_code == 200
    result = res.get_json()
    # The response should indicate success and return the new user's ID
    assert result["success"] is True
    new_id = result["user_id"]
    # Verify the new user was added to the database
    created = User.query.filter_by(email="newuser@example.com").first()
    assert created is not None and created.id == new_id

def test_admin_create_user_duplicate(admin_client):
    # Try to create a user with a duplicate username (normal_user fixture created "user")
    dup_data = {
        "name": "user",  # already exists
        "email": "user2@example.com",
        "password": "pass123",
        "password_again": "pass123",
        "role_code": "1",
        "status_code": "2"
    }
    res = admin_client.post("/settings/admin/users/create", data=dup_data)
    # Should return Conflict for duplicate username
    assert res.status_code == 409
    result = res.get_json()
    assert result["success"] is False
    # The error message should indicate the duplicate issue (contains "exists")
    assert "exists" in result.get("error", "").lower()

def test_admin_edit_user_via_api(admin_client):
    # Get an existing user (normal_user from fixture) to edit
    target = User.query.filter_by(email="user@example.com").first()
    assert target is not None
    update_data = {
        "name": "user_edit",
        "email": "user_edit@example.com",
        "password": "",  # leave password unchanged
        "password_again": "",
        "role_code": "1",
        "status_code": "2"
    }
    url = f"/settings/admin/users/{target.id}/edit"
    res = admin_client.post(url, data=update_data)
    assert res.status_code == 200
    result = res.get_json()
    assert result["success"] is True
    # Verify the user's info was updated in the database
    updated = User.query.get(target.id)
    assert updated.name == "user_edit"
    assert updated.email == "user_edit@example.com"

def test_admin_edit_user_conflict(admin_client, db_session):
    # Create two users directly
    u1 = User(name="conflict1", email="conf1@example.com", password="123456", role_code=1, status_code=2)
    u2 = User(name="conflict2", email="conf2@example.com", password="abcdef", role_code=1, status_code=2)
    db_session.session.add_all([u1, u2])
    db_session.session.commit()
    # Attempt to update u1 to have u2's email (duplicate) via API
    res = admin_client.post(f"/settings/admin/users/{u1.id}/edit", data={
        "name": "conflict1",
        "email": "conf2@example.com",  # email already taken by u2
        "password": "",
        "password_again": "",
        "role_code": "1",
        "status_code": "2"
    })
    # Expect a 409 Conflict due to duplicate email
    assert res.status_code == 409
    result = res.get_json()
    assert result["success"] is False

def test_admin_delete_user_via_api(admin_client, db_session):
    # Create a new user to delete
    u = User(name="todelete", email="todelete@example.com", password="xxx", role_code=1, status_code=2)
    db_session.session.add(u)
    db_session.session.commit()
    # Attempt to delete the primary admin (id=1) – should fail with success=False
    res1 = admin_client.post("/settings/admin/users/1/delete")
    assert res1.status_code == 400
    assert res1.get_json()["success"] is False
    # Now delete the newly created user – should succeed
    res2 = admin_client.post(f"/settings/admin/users/{u.id}/delete")
    assert res2.status_code == 200
    assert res2.get_json()["success"] is True
    # Verify the user is gone from the database
    assert User.query.filter_by(email="todelete@example.com").first() is None

def test_failed_logins_page_and_data(admin_client, db_session):
    # Load the failed logins page (GET)
    page_res = admin_client.get("/settings/admin/failed_logins")
    assert page_res.status_code == 200
    # Insert a failed login attempt into the DB
    from ad2web.user.models import FailedLogin
    fl = FailedLogin(name="intruder", ip_address="10.0.0.1", user_agent_string="TestBrowser")
    db_session.session.add(fl)
    db_session.session.commit()
    # Fetch failed logins data (JSON)
    data_res = admin_client.get("/settings/admin/failed_logins/data")
    assert data_res.status_code == 200
    payload = data_res.get_json()
    records = payload["data"]
    # The inserted attempt should appear in the data
    assert any(rec["name"] == "intruder" and rec["ip_address"] == "10.0.0.1" for rec in records)
