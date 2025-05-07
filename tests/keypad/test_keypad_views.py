import pytest
from ad2web.keypad.models import KeypadButton

def test_list_buttons_access_and_content(normal_client, admin_client, db_session):
    """Normal user sees their custom buttons; admin sees all users' buttons on /keypad/buttons."""
    # Create a button for normal user
    resp = normal_client.post("/keypad/buttons/new", data={"text": "UserButton", "code": "1234"})
    assert resp.status_code == 302 and "/keypad/buttons" in resp.location  # redirected to list
    # Create a button for admin user
    resp2 = admin_client.post("/keypad/buttons/new", data={"text": "AdminButton", "code": "9999"})
    assert resp2.status_code == 302
    # Normal user list view (should see only "UserButton")
    resp3 = normal_client.get("/keypad/buttons")
    assert resp3.status_code == 200
    content = resp3.data.decode()
    assert "UserButton" in content
    assert "AdminButton" not in content  # normal user should not see admin's button
    # Admin user list view (should see both buttons)
    resp4 = admin_client.get("/keypad/buttons")
    assert resp4.status_code == 200
    content_admin = resp4.data.decode()
    assert "UserButton" in content_admin and "AdminButton" in content_admin

def test_create_button_validation(normal_client):
    """Creating a custom button with missing data should show errors; valid data redirects to list."""
    # GET create page
    get_resp = normal_client.get("/keypad/buttons/new")
    assert get_resp.status_code == 200
    # POST missing fields
    resp = normal_client.post("/keypad/buttons/new", data={"text": "", "code": ""})
    # Should not redirect, should return form with errors (status 200 for form re-render)
    assert resp.status_code == 200
    page = resp.data.decode()
    assert "This field is required" in page or "required" in page
    # POST valid data
    resp2 = normal_client.post("/keypad/buttons/new", data={"text": "NewButton", "code": "****"})
    # Should redirect to list on success
    assert resp2.status_code == 302 and "/keypad/buttons" in resp2.location
    # Confirm the new button appears in the list page
    list_page = normal_client.get("/keypad/buttons")
    assert "NewButton" in list_page.data.decode()

def test_edit_button_flow(normal_client, admin_client, db_session):
    """User can edit their own button; others cannot edit it."""
    # Create a button for normal user to edit
    resp = normal_client.post("/keypad/buttons/new", data={"text": "EditMe", "code": "0000"})
    assert resp.status_code == 302
    # Find the created button's ID (last in DB for that user)
    button = KeypadButton.query.filter_by(label="EditMe").first()
    assert button is not None
    edit_url = f"/keypad/buttons/{button.button_id}/edit"
    # GET edit page as owner
    get_resp = normal_client.get(edit_url)
    assert get_resp.status_code == 200
    # Submit edit as owner
    resp2 = normal_client.post(edit_url, data={"text": "EditedLabel", "code": "1111"})
    assert resp2.status_code == 302 and "/keypad/buttons" in resp2.location
    # Verify changes on list
    page = normal_client.get("/keypad/buttons")
    content = page.data.decode()
    assert "EditedLabel" in content and "1111" in content
    # Another user (admin) attempting to edit normal user's button -> should get 403
    resp_forbidden = admin_client.get(edit_url)
    assert resp_forbidden.status_code == 403

def test_delete_button_permissions(normal_client, admin_client, db_session):
    """User can delete their button; other users cannot delete it."""
    # Create a button to delete
    resp = normal_client.post("/keypad/buttons/new", data={"text": "DelButton", "code": "2222"})
    button = KeypadButton.query.filter_by(label="DelButton").first()
    delete_url = f"/keypad/buttons/{button.button_id}/delete"
    # Delete as owner (via POST)
    resp2 = normal_client.post(delete_url)
    # Should redirect to list
    assert resp2.status_code == 302 and "/keypad/buttons" in resp2.location
    # The button should no longer exist in DB
    assert KeypadButton.query.get(button.button_id) is None
    # Re-create as admin for forbidden test
    resp3 = admin_client.post("/keypad/buttons/new", data={"text": "AdminDel", "code": "3333"})
    admin_button = KeypadButton.query.filter_by(label="AdminDel").first()
    # Attempt delete by normal user on admin's button
    resp_forbidden = normal_client.post(f"/keypad/buttons/{admin_button.button_id}/delete")
    assert resp_forbidden.status_code == 403 or resp_forbidden.status_code == 404

def test_keypad_ui_page_access(normal_client, client):
    """The main keypad UI page (/keypad/) requires login and loads successfully for logged-in user."""
    # Logged-out user should be redirected to login
    resp = client.get("/keypad/")
    assert resp.status_code in (302, 401)  # 302 redirect to login (flask-login), or 401 if API mode
    # Logged-in normal user should get 200
    resp2 = normal_client.get("/keypad/")
    assert resp2.status_code == 200
    page_html = resp2.data.decode()
    # The page should contain elements of the keypad UI, e.g., special button names or input fields
    assert "Keypad" in page_html or "Fire" in page_html or "Submit" in page_html  # simple check for content
