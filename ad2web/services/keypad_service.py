from ..extensions import db
from ..settings.models import Setting
from ..keypad.models import KeypadButton
from alarmdecoder.panels import ADEMCO, DSC
from alarmdecoder import AlarmDecoder
from ad2web.keypad.constants import (
    FIRE, POLICE, MEDICAL, SPECIAL_4, SPECIAL_CUSTOM,
    STAY, AWAY, CHIME, RESET, EXIT, SPECIAL_KEY_MAP
)

def get_panel_mode():
    """Retrieve the current alarm panel mode (ADEMCO or DSC)."""
    return Setting.get_by_name('panel_mode').value

def get_custom_buttons(user):
    """Fetch all custom keypad buttons for the given user."""
    # If user roles are defined, admins could fetch all users' buttons.
    query = KeypadButton.query
    if hasattr(user, "is_admin") and user.is_admin:
        buttons = query.all()
    else:
        buttons = query.filter_by(user_id=user.id).all()
    return buttons

def get_special_buttons():
    """Retrieve current special button type selections and key codes from settings."""
    special_buttons = {}
    panel_mode = get_panel_mode()
    # Base three special function buttons (Fire, Police, Medical)
    special_buttons['special_1'] = Setting.get_by_name('special_1', default=FIRE).value
    special_buttons['special_1_key'] = Setting.get_by_name(
        'special_1_key', default=SPECIAL_KEY_MAP[FIRE]).value
    special_buttons['special_2'] = Setting.get_by_name('special_2', default=POLICE).value
    special_buttons['special_2_key'] = Setting.get_by_name(
        'special_2_key', default=SPECIAL_KEY_MAP[POLICE]).value
    special_buttons['special_3'] = Setting.get_by_name('special_3', default=MEDICAL).value
    special_buttons['special_3_key'] = Setting.get_by_name(
        'special_3_key', default=SPECIAL_KEY_MAP[MEDICAL]).value
    # Panel-specific additional function buttons
    if panel_mode == ADEMCO or panel_mode is None:
        # Ademco supports a fourth special button (Panel Default or Custom)
        special_buttons['special_4'] = Setting.get_by_name('special_4', default=SPECIAL_4).value
        special_buttons['special_4_key'] = Setting.get_by_name(
            'special_4_key', default=SPECIAL_KEY_MAP[SPECIAL_4]).value
    else:
        # DSC panels support special buttons 4–8 (Stay, Away, Chime, Reset, Exit)
        special_buttons['special_4'] = Setting.get_by_name('special_4', default=STAY).value
        special_buttons['special_4_key'] = Setting.get_by_name(
            'special_4_key', default=SPECIAL_KEY_MAP[STAY]).value
        special_buttons['special_5'] = Setting.get_by_name('special_5', default=AWAY).value
        special_buttons['special_5_key'] = Setting.get_by_name(
            'special_5_key', default=SPECIAL_KEY_MAP[AWAY]).value
        special_buttons['special_6'] = Setting.get_by_name('special_6', default=CHIME).value
        special_buttons['special_6_key'] = Setting.get_by_name(
            'special_6_key', default=SPECIAL_KEY_MAP[CHIME]).value
        special_buttons['special_7'] = Setting.get_by_name('special_7', default=RESET).value
        special_buttons['special_7_key'] = Setting.get_by_name(
            'special_7_key', default=SPECIAL_KEY_MAP[RESET]).value
        special_buttons['special_8'] = Setting.get_by_name('special_8', default=EXIT).value
        special_buttons['special_8_key'] = Setting.get_by_name(
            'special_8_key', default=SPECIAL_KEY_MAP[EXIT]).value
    return special_buttons

def interpret_key(button_data):
    """
    Interpret a special key placeholder or sequence into the actual key code.
    For example, "<S1>" becomes AlarmDecoder.KEY_F1, and "<S5>" becomes chr(5)*3.
    """
    five = chr(5) + chr(5) + chr(5)
    six = chr(6) + chr(6) + chr(6)
    seven = chr(7) + chr(7) + chr(7)
    eight = chr(8) + chr(8) + chr(8)
    # Map placeholders to actual key sequences
    if button_data == "<S1>" or button_data == AlarmDecoder.KEY_F1:
        return AlarmDecoder.KEY_F1
    if button_data == "<S2>" or button_data == AlarmDecoder.KEY_F2:
        return AlarmDecoder.KEY_F2
    if button_data == "<S3>" or button_data == AlarmDecoder.KEY_F3:
        return AlarmDecoder.KEY_F3
    if button_data == "<S4>" or button_data == AlarmDecoder.KEY_F4:
        return AlarmDecoder.KEY_F4
    if button_data == "<S5>" or button_data == five:
        return five
    if button_data == "<S6>" or button_data == six:
        return six
    if button_data == "<S7>" or button_data == seven:
        return seven
    if button_data == "<S8>" or button_data == eight:
        return eight
    # Return as-is for any other code (regular numeric sequences)
    return button_data

def fill_special_form(form, panel_mode):
    """Populate the special buttons form with current setting values."""
    buttons = get_special_buttons()
    # Set common fields for special_1 through special_4
    form.special_1.data = buttons['special_1']; form.special_1_key.data = buttons['special_1_key']
    form.special_2.data = buttons['special_2']; form.special_2_key.data = buttons['special_2_key']
    form.special_3.data = buttons['special_3']; form.special_3_key.data = buttons['special_3_key']
    form.special_4.data = buttons.get('special_4', SPECIAL_4)
    form.special_4_key.data = buttons.get('special_4_key', SPECIAL_KEY_MAP.get(SPECIAL_4))
    if panel_mode == DSC:
        # Only DSC panels have special_5 through special_8
        form.special_5.data = buttons['special_5']; form.special_5_key.data = buttons['special_5_key']
        form.special_6.data = buttons['special_6']; form.special_6_key.data = buttons['special_6_key']
        form.special_7.data = buttons['special_7']; form.special_7_key.data = buttons['special_7_key']
        form.special_8.data = buttons['special_8']; form.special_8_key.data = buttons['special_8_key']

def update_special_settings(form, panel_mode):
    """
    Save the special button selections from the form into Setting entries.
    Handles both Ademco (special_1–4) and DSC (special_1–8) panels.
    """
    max_index = 4 if panel_mode == ADEMCO or panel_mode is None else 8
    for i in range(1, max_index + 1):
        # Retrieve or create the setting records by key name
        setting = Setting.get_by_name(f'special_{i}')
        setting.value = getattr(form, f'special_{i}').data
        setting_key = Setting.get_by_name(f'special_{i}_key')
        # If not a custom code, use the preset key mapping; if custom, use provided code
        if setting.value != SPECIAL_CUSTOM:
            setting_key.value = SPECIAL_KEY_MAP.get(setting.value, setting_key.value)
        else:
            setting_key.value = interpret_key(getattr(form, f'special_{i}_key').data)
        db.session.add(setting)
        db.session.add(setting_key)
    db.session.commit()

def create_custom_button(user, form):
    """Create a new custom keypad button for the user using form data."""
    new_button = KeypadButton(user_id=user.id, label=form.text.data, code=form.code.data)
    db.session.add(new_button)
    db.session.commit()
    return new_button

def update_custom_button(button, form):
    """Update an existing custom button with form data."""
    button.label = form.text.data
    button.code = form.code.data
    db.session.commit()
    return button

def delete_custom_button(button):
    """Delete a custom keypad button."""
    db.session.delete(button)
    db.session.commit()
