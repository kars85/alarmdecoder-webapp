import pytest
from ad2web.services import keypad_service
from ad2web.keypad.models import KeypadButton
from ad2web.user.models import User
from ad2web.settings.models import Setting
from ad2web.keypad.constants import FIRE, POLICE, MEDICAL, SPECIAL_4, SPECIAL_CUSTOM, STAY, AWAY, CHIME, RESET, EXIT

def test_get_custom_buttons_filters_by_user(db_session):
    """get_custom_buttons returns only current user's buttons (all if admin)."""
    # Create two users if not already present (admin and normal)
    admin_user = User.query.filter_by(id=1).first() or User(name="admin", email="admin@example.com", password="x", role_code=0)
    normal_user = User.query.filter(User.id != 1).first()
    if normal_user is None:
        normal_user = User(name="normal", email="normal@example.com", password="x", role_code=1)
        db_session.add(normal_user)
    db_session.add(admin_user)
    db_session.commit()
    # Create one button for each user
    btn1 = KeypadButton(user_id=admin_user.id, label="AdminBtn", code="AAA")
    btn2 = KeypadButton(user_id=normal_user.id, label="UserBtn", code="BBB")
    db_session.add_all([btn1, btn2]); db_session.commit()
    # Normal user should only get their own button
    user_buttons = keypad_service.get_custom_buttons(normal_user)
    assert all(b.user_id == normal_user.id for b in user_buttons) and any(b.label == "UserBtn" for b in user_buttons)
    assert all(b.label != "AdminBtn" for b in user_buttons)
    # Admin user should get all buttons
    admin_buttons = keypad_service.get_custom_buttons(admin_user)
    labels = {b.label for b in admin_buttons}
    assert "AdminBtn" in labels and "UserBtn" in labels  # admin sees both

def test_create_custom_button_and_update(db_session):
    """create_custom_button should persist a new button, update_custom_button should modify it."""
    # Ensure a user exists for ownership
    user = User.query.filter(User.name == "testuser").first()
    if not user:
        user = User(name="testuser", email="test@example.com", password="pass")
        db_session.add(user); db_session.commit()
    # Create a dummy form-like object for creation
    form = type("F", (), {})()
    form.text = type("F2", (), {"data": "My Button"})
    form.code = type("F2", (), {"data": "*123#"})
    new_button = keypad_service.create_custom_button(user, form)
    # Verify the new button is saved with correct fields
    assert new_button.button_id is not None
    saved = KeypadButton.query.get(new_button.button_id)
    assert saved is not None
    assert saved.label == "My Button" and saved.code == "*123#" and saved.user_id == user.id
    # Update the button using update_custom_button
    form.code.data = "#999"    # change code
    form.text.data = "New Label"
    updated = keypad_service.update_custom_button(saved, form)
    # Verify the button's fields are updated in the database
    refreshed = KeypadButton.query.get(new_button.button_id)
    assert refreshed.label == "New Label" and refreshed.code == "#999"
    # Clean up by deleting (to test delete as well)
    keypad_service.delete_custom_button(refreshed)
    assert KeypadButton.query.get(new_button.button_id) is None

def test_delete_custom_button_removes(db_session):
    """delete_custom_button should remove the button from the database."""
    user = User.query.first() or User(name="tmpuser", email="tmp@example.com", password="pass")
    db_session.add(user); db_session.commit()
    # Create a button to delete
    form = type("F", (), {})()
    form.text = type("F2", (), {"data": "TempBtn"})
    form.code = type("F2", (), {"data": "111"})
    btn = keypad_service.create_custom_button(user, form)
    btn_id = btn.button_id
    # Now delete it
    keypad_service.delete_custom_button(btn)
    assert KeypadButton.query.get(btn_id) is None

def test_interpret_key_placeholders():
    """interpret_key replaces special placeholders with correct sequences."""
    # <S1>, <S2>, <S3> correspond to AlarmDecoder KEY_F1, KEY_F2, KEY_F3 sequences
    s1 = keypad_service.interpret_key("<S1>")
    s2 = keypad_service.interpret_key("<S2>")
    s3 = keypad_service.interpret_key("<S3>")
    # Each should be a string of control characters (likely length 3)
    assert isinstance(s1, str) and isinstance(s2, str) and isinstance(s3, str)
    assert len(s1) <= 5 and len(s2) <= 5 and len(s3) <= 5
    # Check that each sequence is composed of repeated identical control char (e.g., \x05, \x06, \x07)
    assert len(set(s1)) == 1 and len(s1) >= 1 and ord(s1[0]) < 10
    assert len(set(s2)) == 1 and ord(s2[0]) < 10
    assert len(set(s3)) == 1 and ord(s3[0]) < 10
    # Mixed string with placeholders
    mixed = keypad_service.interpret_key("12<S2>34")
    # Expect the <S2> to be replaced by the same sequence as s2 above
    assert mixed.startswith("12") and mixed.endswith("34")
    middle = mixed[2:-2]
    assert middle == s2  # the placeholder <S2> got replaced
    # Multiple placeholders in one string
    combo = keypad_service.interpret_key("<S1><S2>")
    # Should equal concatenation of s1 + s2
    assert combo == s1 + s2
    # No placeholders: string remains unchanged
    plain = keypad_service.interpret_key("*#")
    assert plain == "*#"

def test_get_panel_mode(db_session):
    """get_panel_mode retrieves the alarm panel mode from settings (with default)."""
    # If no panel_mode set, should return None
    Setting.query.filter_by(name="panel_mode").delete()  # remove if exists
    db_session.commit()
    mode = keypad_service.get_panel_mode()
    assert mode is None
    # Set panel_mode to DSC and verify retrieval
    Setting.set_value("panel_mode", "DSC")
    db_session.commit()
    mode2 = keypad_service.get_panel_mode()
    assert mode2 == "DSC"
    # Set panel_mode to ADEMCO and verify
    Setting.set_value("panel_mode", "ADEMCO")
    db_session.commit()
    mode3 = keypad_service.get_panel_mode()
    assert mode3 == "ADEMCO"

def test_fill_special_form_ademco(db_session):
    """fill_special_form populates SpecialButtonFormAdemco fields from stored settings."""
    panel_mode = "ADEMCO"
    # Set some special button settings for Ademco panel
    Setting.set_value("special_1", FIRE); Setting.set_value("special_1_key", keypad_service.interpret_key("<S1>"))
    Setting.set_value("special_2", SPECIAL_CUSTOM); Setting.set_value("special_2_key", "*99#")  # custom code
    Setting.set_value("special_3", MEDICAL); Setting.set_value("special_3_key", keypad_service.interpret_key("<S3>"))
    # Intentionally leave special_4 not set to use default
    db_session.commit()
    # Create form and fill it
    form = keypad_service.SpecialButtonFormAdemco()
    keypad_service.fill_special_form(form, panel_mode)
    # special_1 (Fire) is preset: data should be FIRE (0), key should be AlarmDecoder.KEY_F1 (control sequence)
    assert form.special_1.data == FIRE
    assert isinstance(form.special_1_key.data, str) and len(form.special_1_key.data) <= 5
    assert form.special_1_key.data != "<S1>"  # should be actual code sequence, not placeholder
    # special_2 is custom: data 5, key "*99#"
    assert form.special_2.data == SPECIAL_CUSTOM
    assert form.special_2_key.data == "*99#"
    # special_3 is preset (Medical): data 2, key control sequence for F3
    assert form.special_3.data == MEDICAL
    assert isinstance(form.special_3_key.data, str) and form.special_3_key.data != "<S3>"
    # special_4 was not set (defaults to Panel Default/SPECIAL_4)
    assert form.special_4.data == SPECIAL_4
    # Its key default should come from SPECIAL_KEY_MAP[SPECIAL_4]
    assert isinstance(form.special_4_key.data, str) and len(form.special_4_key.data) <= 5

def test_fill_special_form_dsc(db_session):
    """fill_special_form populates SpecialButtonFormDSC fields for DSC panel mode."""
    panel_mode = "DSC"
    # Set some DSC special settings (special_5 custom, special_6 preset)
    Setting.set_value("special_5", SPECIAL_CUSTOM); Setting.set_value("special_5_key", "CUSTOM5")
    Setting.set_value("special_6", STAY)  # STAY preset
    # Do not set special_6_key to let default mapping apply
    db_session.commit()
    form = keypad_service.SpecialButtonFormDSC()
    keypad_service.fill_special_form(form, panel_mode)
    # special_5 custom
    assert form.special_5.data == SPECIAL_CUSTOM
    assert form.special_5_key.data == "CUSTOM5"
    # special_6 preset (Stay)
    assert form.special_6.data == STAY
    assert isinstance(form.special_6_key.data, str) and len(form.special_6_key.data) <= 5
    # The key data for Stay should be a control sequence (not placeholder)
    assert form.special_6_key.data != "<S6>"
    # special_7, special_8 should be set to defaults (RESET, EXIT) if not in DB
    assert form.special_7.data == RESET and form.special_8.data == EXIT

def test_update_special_settings(db_session):
    """update_special_settings saves special button selections and keys properly."""
    panel_mode = "ADEMCO"
    # Prepare form data: special_1 = Fire (preset), special_2 = Custom
    form = keypad_service.SpecialButtonFormAdemco()
    form.special_1.data = FIRE
    form.special_1_key.data = "<S1>"  # user input (will be ignored for preset)
    form.special_2.data = SPECIAL_CUSTOM
    form.special_2_key.data = "77"    # custom code
    form.special_3.data = POLICE
    form.special_3_key.data = "<S2>"  # will be ignored (preset)
    form.special_4.data = SPECIAL_CUSTOM
    form.special_4_key.data = "ABCD"  # custom code for special 4
    # Apply update
    keypad_service.update_special_settings(form, panel_mode)
    # Verify in DB:
    s1 = Setting.get_by_name("special_1").value
    s1_key = Setting.get_by_name("special_1_key").value
    s2 = Setting.get_by_name("special_2").value
    s2_key = Setting.get_by_name("special_2_key").value
    s4 = Setting.get_by_name("special_4").value
    s4_key = Setting.get_by_name("special_4_key").value
    # special_1 (Fire) should be stored as 0, and key should be mapped (not literally "<S1>")
    assert s1 == FIRE
    assert isinstance(s1_key, str) and s1_key != "<S1>"
    # It should be the AlarmDecoder.KEY_F1 sequence (control chars). We expect length ~3 and non-printable chars.
    assert len(s1_key) <= 5 and all(ord(ch) < 10 for ch in s1_key)
    # special_2 (Custom) stored as 5, key stored exactly as provided ("77")
    assert s2 == SPECIAL_CUSTOM
    assert s2_key == "77"
    # special_4 (Custom) stored as 5, key "ABCD"
    assert s4 == SPECIAL_CUSTOM
    assert s4_key == "ABCD"
    # special_3 (Police preset) would also be stored (just to double-check one more preset)
    s3_key = Setting.get_by_name("special_3_key").value
    assert isinstance(s3_key, str) and all(ord(c) < 10 for c in s3_key)
