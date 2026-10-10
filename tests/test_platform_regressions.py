"""Regression tests for platform validation and switch command semantics."""

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from custom_components.crestron.schema import validate_entity
from custom_components.crestron.switch import CrestronSwitch

from .test_platforms import FakeBridge, make_entity


@pytest.mark.parametrize("requested", [False, True])
async def test_pulsed_switch_does_not_toggle_already_matching_feedback(requested):
    """Repeated on/off services must not reverse an already satisfied request."""
    bridge = FakeBridge()
    bridge.digital[1] = requested
    entity = make_entity(
        bridge, CrestronSwitch, "switch", {"switch_join": 1, "pulsed": True}
    )

    if requested:
        await entity.async_turn_on()
    else:
        await entity.async_turn_off()

    bridge.pulse_digital.assert_not_awaited()


@pytest.mark.parametrize("requested", [False, True])
async def test_pulsed_switch_rejects_unknown_feedback(requested):
    """Without feedback, a toggle cannot safely realize an on/off request."""
    bridge = FakeBridge()
    entity = make_entity(
        bridge, CrestronSwitch, "switch", {"switch_join": 1, "pulsed": True}
    )

    with pytest.raises(ServiceValidationError, match="feedback is unknown"):
        if requested:
            await entity.async_turn_on()
        else:
            await entity.async_turn_off()

    bridge.pulse_digital.assert_not_awaited()


@pytest.mark.parametrize("device_class", ["date", "timestamp", "uptime", "enum"])
def test_analog_sensor_rejects_non_numeric_device_classes(device_class):
    """A scaled 16-bit analog cannot provide HA date, datetime or enum values."""
    with pytest.raises(vol.Invalid):
        validate_entity(
            "sensor",
            {"name": "Invalid class", "value_join": 1, "device_class": device_class},
        )


@pytest.mark.parametrize("unit", [None, "degrees", "W"])
def test_analog_sensor_rejects_missing_or_mismatched_class_units(unit):
    """Accept only units HA can interpret for the configured numeric class."""
    data = {"name": "Temperature", "value_join": 1, "device_class": "temperature"}
    if unit is not None:
        data["unit_of_measurement"] = unit
    with pytest.raises(vol.Invalid):
        validate_entity("sensor", data)


@pytest.mark.parametrize(
    ("device_class", "unit"),
    [
        ("temperature", "°F"),
        ("temperature", "°C"),
        ("battery", "%"),
        ("aqi", None),
        ("power_factor", None),
        ("power_factor", "%"),
        ("monetary", "USD"),
    ],
)
def test_numeric_sensor_preserves_supported_units(device_class, unit):
    """Unitless numeric classes remain usable alongside convertible readings."""
    data = {"name": "Reading", "value_join": 1, "device_class": device_class}
    if unit is not None:
        data["unit_of_measurement"] = unit
    result = validate_entity("sensor", data)
    assert result.get("unit_of_measurement") == unit


@pytest.mark.parametrize("unit", [None, "dollars", "°F"])
def test_monetary_sensor_requires_currency(unit):
    """HA monetary readings use currency codes rather than arbitrary units."""
    data = {"name": "Cost", "value_join": 1, "device_class": "monetary"}
    if unit is not None:
        data["unit_of_measurement"] = unit
    with pytest.raises(vol.Invalid):
        validate_entity("sensor", data)


async def test_temperature_sensor_uses_ha_native_unit_conversion(hass):
    """Real HA converts wire Fahrenheit to the user's configured Celsius units."""
    from unittest.mock import patch

    from homeassistant.helpers import entity_registry as er
    from homeassistant.util.unit_system import METRIC_SYSTEM
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    hass.config.units = METRIC_SYSTEM
    bridge = FakeBridge()
    entry = MockConfigEntry(
        domain="crestron",
        title="Sensors",
        data={"host": "127.0.0.1", "port": 16384},
        subentries_data=[
            {
                "subentry_id": "temperature",
                "subentry_type": "entity",
                "title": "Temperature",
                "unique_id": None,
                "data": validate_entity(
                    "sensor",
                    {
                        "name": "Temperature",
                        "value_join": 1,
                        "divisor": 10,
                        "device_class": "temperature",
                        "unit_of_measurement": "°F",
                    },
                ),
            }
        ],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.crestron.Bridge", return_value=bridge):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", "crestron", f"{entry.entry_id}_temperature"
    )
    bridge.feedback("analog", 1, 725)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert float(state.state) == 22.5
    assert state.attributes["unit_of_measurement"] == "°C"
    assert await hass.config_entries.async_unload(entry.entry_id)
