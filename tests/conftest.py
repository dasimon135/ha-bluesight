"""Pytest configuration for BlueSight tests.

The foundation tests (model + detectors) are pure Python and do not import
Home Assistant, so they run under plain pytest without any HASS fixture.
Later, HA-dependent tests can opt into the
``pytest-homeassistant-custom-component`` plugin.
"""
