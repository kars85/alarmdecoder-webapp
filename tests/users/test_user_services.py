# tests/users/test_keypad_services.py
import pytest
from ad2web.services.user_service import UserService, DuplicateUserError
from ad2web.user.models import User
from ad2web.user.constants import ACTIVE, INACTIVE, NEW

def test_list_and_get_user(db_session, admin_user, normal_user):
    # admin_user and normal_user fixtures add two users
    users = UserService.list_users()
    assert isinstance(users, list)
    # Verify both created users are present in the list
    emails = {u.email for u in users}
    assert {"admin@example.com", "user@example.com"} <= emails
    # get_user should retrieve an existing user and return None for non-existent
    fetched = UserService.get_user(admin_user.id)
    assert fetched is not None and fetched.name == admin_user.name
    assert UserService.get_user(99999) is None

def test_create_user_success_and_duplicate(db_session):
    # Create a new user with unique name/email
    user = UserService.create_user(name="testuser", email="test@example.com",
                                   password="pass123", role_code=1, status_code=ACTIVE)
    assert isinstance(user, User)
    # Creating another user with the same username should raise DuplicateUserError&#8203;:contentReference[oaicite:17]{index=17}
    with pytest.raises(DuplicateUserError):
        UserService.create_user(name="testuser", email="other@example.com",
                                password="pass456", role_code=1, status_code=ACTIVE)
    # Creating another user with the same email should also raise DuplicateUserError
    with pytest.raises(DuplicateUserError):
        UserService.create_user(name="otheruser", email="test@example.com",
                                password="pass789", role_code=1, status_code=ACTIVE)

def test_update_user_success_and_conflict(db_session):
    # Create two users to test updates
    u1 = UserService.create_user(name="userA", email="userA@example.com",
                                 password="123456", role_code=1, status_code=ACTIVE)
    u2 = UserService.create_user(name="userB", email="userB@example.com",
                                 password="abcdef", role_code=1, status_code=ACTIVE)
    # Successful update of u1's fields (name, email, password)
    updated = UserService.update_user(u1.id, name="userA_new", email="userA_new@example.com",
                                      password="newpass", role_code=1, status_code=ACTIVE)
    assert updated is not None
    assert updated.name == "userA_new"
    # Password should have been updated (check new password works)
    assert updated.check_password("newpass") is True
    # Attempt to update u1 to an email that already exists (u2's email) -> expect DuplicateUserError&#8203;:contentReference[oaicite:18]{index=18}
    with pytest.raises(DuplicateUserError):
        UserService.update_user(u1.id, name="userA_new2", email="userB@example.com",
                                password="", role_code=1, status_code=ACTIVE)
    # Updating a non-existent user ID should return None
    assert UserService.update_user(99999, name="no", email="no@none.com",
                                   password="no", role_code=1, status_code=ACTIVE) is None

def test_delete_user_and_protect_admin(db_session, admin_user):
    # admin_user fixture provides an admin (id=1)
    admin = UserService.get_user(admin_user.id)
    # Create another user to delete
    other = UserService.create_user(name="temp", email="temp@example.com",
                                    password="temp123", role_code=1, status_code=ACTIVE)
    # Attempt to delete the primary admin (ID 1) -> should be prevented&#8203;:contentReference[oaicite:19]{index=19}
    assert UserService.delete_user(admin.id) is False
    assert UserService.get_user(admin.id) is not None  # admin still exists
    # Delete the other user -> should succeed
    assert UserService.delete_user(other.id) is True
    assert UserService.get_user(other.id) is None

@pytest.mark.parametrize("initial_status, expected_status", [
    (ACTIVE, INACTIVE),   # active -> becomes inactive
    (INACTIVE, ACTIVE),   # inactive -> becomes active
    (NEW, ACTIVE),        # new -> becomes active
])
def test_toggle_user_status(initial_status, expected_status, db_session):
    # Create a user with given initial status
    u = UserService.create_user(name=f"user{initial_status}", email=f"user{initial_status}@example.com",
                                password="abc123", role_code=1, status_code=initial_status)
    updated = UserService.toggle_user_status(u.id)
    assert updated is not None
    # Status code should toggle to expected value&#8203;:contentReference[oaicite:20]{index=20}
    assert updated.status_code == expected_status
    # Toggling again should flip it back (except ACTIVE goes to INACTIVE as above)
    updated_again = UserService.toggle_user_status(u.id)
    if initial_status == ACTIVE:
        assert updated_again.status_code == INACTIVE  # was active, now inactive
    else:
        assert updated_again.status_code == ACTIVE   # was inactive/new, now active

def test_list_failed_logins(db_session):
    # Initially no failed login attempts
    assert UserService.list_failed_logins() == []
    # Insert a fake failed login record
    from ad2web.user.models import FailedLogin
    fl = FailedLogin(name="unknown", ip_address="127.0.0.1", user_agent_string="TestAgent")
    db_session.session.add(fl)
    db_session.session.commit()
    attempts = UserService.list_failed_logins()
    assert len(attempts) == 1
    assert attempts[0].name == "unknown"
    assert attempts[0].ip_address == "127.0.0.1"
