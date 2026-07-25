"""Unit tests for the pure ghost-slot availability decision helper.

Pure logic, no Home Assistant import, so this runs on the default Windows
``python -m pytest`` suite.
"""
from custom_components.bluesight.availability import is_device_alive


def test_none_is_alive():
    # Device not found in the registry -> conservative: don't flag it.
    assert is_device_alive(None) is True


def test_empty_is_alive():
    # Device found but has no entities -> conservative: alive.
    assert is_device_alive([]) is True


def test_single_concrete_state_is_alive():
    assert is_device_alive(["off"]) is True


def test_single_unavailable_is_dead():
    assert is_device_alive(["unavailable"]) is False


def test_unavailable_plus_unknown_is_alive():
    # 'unknown' must NOT count as dead (e.g. button entities).
    assert is_device_alive(["unavailable", "unknown"]) is True


def test_concrete_plus_unavailable_is_alive():
    assert is_device_alive(["off", "unavailable"]) is True


def test_all_unavailable_is_dead():
    assert is_device_alive(["unavailable", "unavailable"]) is False
