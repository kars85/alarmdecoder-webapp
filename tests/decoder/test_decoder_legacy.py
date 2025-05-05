import types
import pytest
from ad2web.api import _build_alarmdecoder_configuration_data
from ad2web.api import ADEMCO, DSC

def test_build_configuration_data_with_device():
    """_build_alarmdecoder_configuration_data returns correct dict for a given device."""
    dummy_device = types.SimpleNamespace(
        mode=ADEMCO,
        address=5,
        configbits=255,
        address_mask=0xFFFF,
        emulate_zone=True,
        emulate_relay=False,
        emulate_lrr=True,
        deduplicate=False
    )
    result = _build_alarmdecoder_configuration_data(dummy_device)
    assert isinstance(result, dict)
    assert result["address"] == 5
    assert result["config_bits"] == 255
    assert result["address_mask"] == 0xFFFF
    assert result["emulate_zone"] is True
    assert result["emulate_relay"] is False
    assert result["emulate_lrr"] is True
    assert result["deduplicate"] is False
    assert result["mode"] == "ADEMCO"

def test_build_configuration_data_unknown_mode():
    """_build_alarmdecoder_configuration_data returns 'UNKNOWN' for unrecognized mode."""
    dummy_device = types.SimpleNamespace(
        mode=99,  # not ADEMCO or DSC
        address=1, configbits=0, address_mask=0,
        emulate_zone=False, emulate_relay=False, emulate_lrr=False, deduplicate=False
    )
    result = _build_alarmdecoder_configuration_data(dummy_device)
    assert isinstance(result, dict)
    assert result["mode"] == "UNKNOWN"

def test_build_configuration_data_no_device():
    """_build_alarmdecoder_configuration_data returns None if device is None."""
    result = _build_alarmdecoder_configuration_data(None)
    assert result is None
