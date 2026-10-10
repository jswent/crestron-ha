"""Validate user-defined mappings before they reach transport or persistence."""

import json
from copy import deepcopy

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.template import Template

from custom_components.crestron.schema import listen_host, validate_entity


def media_mapping(**updates):
    """Return an analog-source media mapping that can be varied per test."""
    return {
        "name": "Kitchen speakers",
        "power_on_join": 1,
        "power_off_join": 2,
        "mute_join": 3,
        "volume_join": 1,
        "source_number_join": 2,
        "sources": {1: "Streamer", "2": "Radio"},
        **updates,
    }


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "::1", "::"])
def test_literal_bind_addresses(host):
    """Listener setup accepts local-address literals without resolving DNS."""
    assert listen_host(host) == host


@pytest.mark.parametrize("host", ["localhost", "https://127.0.0.1", "999.2.3.4"])
def test_invalid_bind_addresses(host):
    with pytest.raises(ValueError):
        listen_host(host)


@pytest.mark.parametrize(
    ("platform", "data"),
    [
        ("light", {"name": "Lamp", "brightness_join": 0}),
        ("light", {"name": "Lamp", "brightness_join": 1025}),
        ("switch", {"name": "Relay", "switch_join": 4097}),
        ("sensor", {"name": "Temperature", "value_join": 1, "divisor": 0}),
        ("sensor", {"name": "Temperature", "value_join": 1, "divisor": -1}),
        ("sensor", {"name": "Temperature", "value_join": 1, "device_class": "invalid"}),
        (
            "binary_sensor",
            {"name": "Contact", "is_on_join": 1, "device_class": "invalid"},
        ),
        (
            "sensor",
            {
                "name": "Text",
                "value_join": 1,
                "join_type": "serial",
                "unit_of_measurement": "°F",
            },
        ),
        ("climate", {"name": "Thermostat", "heat_sp_join": 1}),
    ],
)
def test_invalid_entity_mapping(platform, data):
    with pytest.raises(vol.Invalid):
        validate_entity(platform, data)


def test_media_mapping_is_json_serializable():
    result = validate_entity("media_player", media_mapping(default_source=2))
    assert result["sources"] == {"1": "Streamer", "2": "Radio"}
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize(
    "updates",
    [
        {"source_digital_joins": {4: "Streamer"}},
        {"sources": {}},
        {"sources": {1: "Radio", 2: "Radio"}},
        {"sources": {1: "Radio", "01": "Streamer"}},
        {"sources": {0: "Radio"}},
        {"default_source": 10},
    ],
)
def test_ambiguous_or_invalid_media_sources(updates):
    with pytest.raises(vol.Invalid):
        validate_entity("media_player", media_mapping(**updates))


def test_digital_sources_have_independent_join_bounds():
    data = media_mapping()
    del data["sources"]
    del data["source_number_join"]
    data["source_digital_joins"] = {4096: "Radio"}
    result = validate_entity("media_player", data)
    assert result["source_digital_joins"] == {"4096": "Radio"}
    data["source_digital_joins"] = {4097: "Radio"}
    with pytest.raises(vol.Invalid):
        validate_entity("media_player", data)


async def test_action_validation_compiles_templates(hass: HomeAssistant):
    """Persist source actions: HA validation creates non-JSON runtime objects."""
    actions = [
        {
            "action": "input_text.set_value",
            "target": {"entity_id": "input_text.feedback"},
            "data": {"value": "{{ value }}"},
        }
    ]
    compiled = cv.SCRIPT_SCHEMA(deepcopy(actions))
    assert isinstance(compiled[0]["data"]["value"], Template)
    assert actions[0]["data"]["value"] == "{{ value }}"
    assert json.loads(json.dumps(actions)) == actions
