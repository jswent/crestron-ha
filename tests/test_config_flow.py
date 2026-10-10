"""Exercise the real config-entry/subentry flow managers."""

import json
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "crestron"


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Main",
        data={"name": "Main", "host": "127.0.0.1", "port": 16384},
    )
    entry.add_to_hass(hass)
    return entry


async def test_create_bridge_and_duplicate_port(hass):
    with patch("custom_components.crestron.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"name": "Lighting", "host": "127.0.0.1", "port": 16384}
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        duplicate = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_USER},
            data={"name": "Audio", "host": "127.0.0.1", "port": 16384},
        )
        assert duplicate["errors"] == {"base": "port_in_use"}
        second = await hass.config_entries.flow.async_configure(
            duplicate["flow_id"], {"name": "Audio", "host": "127.0.0.1", "port": 16385}
        )
        assert second["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_invalid_bind_address(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={"name": "Main", "host": "not-an-ip", "port": 16384},
    )
    assert result["errors"] == {"base": "invalid_config"}


async def test_reconfigure_preserves_entry(hass, entry):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        data={"name": "Renamed", "host": "127.0.0.1", "port": 17000},
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.title == "Renamed"
    assert entry.data["port"] == 17000
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_entity_add_invalid_then_reconfigure(hass, entry):
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "entity"),
        context={"source": SOURCE_USER},
        data={"platform": "light"},
    )
    assert result["step_id"] == "mapping"
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {"name": "Lamp", "brightness_join": 1025}
        )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Lamp", "brightness_join": 2}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "entity"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
        data={"name": "Desk Lamp", "brightness_join": 3},
    )
    assert result["type"] == FlowResultType.ABORT
    assert entry.subentries[subentry.subentry_id].data["brightness_join"] == 3
    assert len(entry.subentries) == 1


async def test_export_validation_and_duplicate(hass, entry):
    data = {
        "name": "State",
        "join_type": "digital",
        "join": 1,
        "value_template": "{{ is_state('light.lamp', 'on') }}",
    }
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "export"), context={"source": SOURCE_USER}, data=data
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "export"), context={"source": SOURCE_USER}, data=data
    )
    assert result["errors"] == {"base": "invalid_mapping"}
    with pytest.raises(InvalidData):
        await hass.config_entries.subentries.async_configure(
            result["flow_id"], {**data, "join": 2, "value_template": "{{ invalid"}
        )


async def test_action_stores_json_not_template_objects(hass, entry):
    data = {
        "name": "Panel",
        "join_type": "analog",
        "join": 2,
        "actions": [{"event": "test_action", "event_data": {"value": "{{ value }}"}}],
        "ignore_release": True,
    }
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "action"), context={"source": SOURCE_USER}, data=data
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    stored = dict(next(iter(entry.subentries.values())).data)
    assert json.loads(json.dumps(stored))["actions"] == data["actions"]


def test_forms_serialize_for_frontend():
    """A schema accepted by a flow must also serialize over the UI's REST API."""
    from homeassistant.helpers import config_validation as cv
    from probatio.codecs.fields import to_field_list

    from custom_components.crestron.config_flow import bridge_form, mapping_form
    from custom_components.crestron.schema import PLATFORM_NAMES, entity_form

    forms = [bridge_form(), mapping_form("action"), mapping_form("export")]
    forms.extend(entity_form(platform) for platform in PLATFORM_NAMES)
    for form in forms:
        json.dumps(to_field_list(form, custom_serializer=cv.custom_serializer))


async def test_reconfigure_can_change_source_method(hass, entry):
    """Optional fields can be removed; old analog maps must not reappear as defaults."""
    initial = {
        "name": "Audio",
        "power_on_join": 1,
        "power_off_join": 2,
        "mute_join": 3,
        "volume_join": 4,
        "source_number_join": 5,
        "sources": {"1": "TV"},
    }
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "entity"),
        context={"source": SOURCE_USER},
        data={"platform": "media_player"},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], initial
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    subentry = next(iter(entry.subentries.values()))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "entity"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry.subentry_id},
    )
    data = {
        k: v for k, v in initial.items() if k not in ("sources", "source_number_join")
    }
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**data, "source_digital_joins": {"20": "Music"}}
    )
    assert result["type"] == FlowResultType.ABORT
    assert "sources" not in entry.subentries[subentry.subentry_id].data
