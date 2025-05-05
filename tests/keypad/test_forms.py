import pytest
from ad2web.keypad.forms import KeypadButtonForm, SpecialButtonFormAdemco, SpecialButtonFormDSC

@pytest.mark.parametrize("field,data_key", [("text", "code"), ("code", "text")])
def test_keypad_button_form_missing_required(field, data_key):
    """KeypadButtonForm requires both label (text) and code."""
    data = {"text": "Label123", "code": "ABC"}
    data[field] = ""  # omit one field
    form = KeypadButtonForm(data=data)
    assert not form.validate()
    # The omitted field should have a validation error for DataRequired
    assert field in form.errors
    assert any("required" in msg.lower() for msg in form.errors[field])
    # The other field being present should not cause error on that field
    assert data_key not in form.errors

def test_keypad_button_form_max_length():
    """KeypadButtonForm enforces max length of 32 for label and code."""
    long_str = "X" * 33  # 33 chars, exceeds max 32
    form = KeypadButtonForm(data={"text": long_str, "code": long_str})
    assert not form.validate()
    # Both fields should have length errors
    assert "text" in form.errors and any("32" in msg for msg in form.errors["text"])
    assert "code" in form.errors and any("32" in msg for msg in form.errors["code"])
    # Boundary: exactly 32 chars is allowed
    ok_str = "Y" * 32
    form2 = KeypadButtonForm(data={"text": ok_str, "code": ok_str})
    assert form2.validate()

def test_special_button_forms_optional_key():
    """SpecialButtonForm fields accept empty 'Key Code' when not needed and enforce max length."""
    # Ademco form: leave special_1_key empty (Optional allowed)
    form_ademco = SpecialButtonFormAdemco(data={
        "special_1": 0, "special_1_key": "",  # blank key is allowed (Optional)
        "special_2": 3, "special_2_key": ""   # Panel Default selected, blank allowed
    })
    assert form_ademco.validate()
    # DSC form: key fields blank allowed as well
    form_dsc = SpecialButtonFormDSC(data={
        "special_1": 0, "special_1_key": "",
        "special_4": 6, "special_4_key": ""   # STAY selected, blank allowed
    })
    assert form_dsc.validate()
    # Test max length = 5 for key fields
    long_key = "123456"  # 6 chars
    form_dsc2 = SpecialButtonFormDSC(data={
        "special_1": 0, "special_1_key": long_key,
        "special_4": 5, "special_4_key": long_key
    })
    assert not form_dsc2.validate()
    assert "special_1_key" in form_dsc2.errors and any("5" in msg for msg in form_dsc2.errors["special_1_key"])
    assert "special_4_key" in form_dsc2.errors and any("5" in msg for msg in form_dsc2.errors["special_4_key"])
