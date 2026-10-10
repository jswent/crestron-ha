"""Inventory imports and physical-device modeling using actual HA registries."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron.inventory import (
    apply_import,
    entity_mappings,
    export_inventory,
    prepare_import,
    validate_device,
)

from .test_platforms import FakeBridge


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain="crestron",
        title="Main",
        minor_version=2,
        data={"name": "Main", "host": "127.0.0.1", "port": 16384},
    )
    entry.add_to_hass(hass)
    return entry


def device(name="Thermostat"):
    return {
        "key": "thermostat",
        "name": name,
        "manufacturer": "Example Controls",
        "model": "Room station",
        "entities": [
            {
                "id": "temperature",
                "platform": "sensor",
                "name": "Temperature",
                "value_join": 1,
                "divisor": 10,
                "device_class": "temperature",
                "unit_of_measurement": "°C",
            },
            {
                "id": "enabled",
                "platform": "switch",
                "name": "Enabled",
                "switch_join": 2,
            },
        ],
    }


def sample_inventory():
    return {
        "entities": [
            {
                "key": "lamp",
                "platform": "light",
                "name": "Desk lamp",
                "brightness_join": 3,
            }
        ],
        "devices": [device()],
        "exports": [
            {
                "key": "occupancy",
                "name": "Occupancy",
                "join_type": "digital",
                "join": 4,
                "value_template": "{{ is_state('binary_sensor.occupancy', 'on') }}",
            }
        ],
        "actions": [
            {
                "key": "panel",
                "name": "Panel",
                "join_type": "digital",
                "join": 5,
                "actions": [
                    {"event": "panel_event", "event_data": {"value": "{{ value }}"}}
                ],
            }
        ],
    }


def by_key(entry):
    return {item.unique_id: item for item in entry.subentries.values()}


def test_import_upserts_stable_keys_without_mutating_input(hass, entry):
    configuration = sample_inventory()
    original = deepcopy(configuration)
    plan = prepare_import(hass, entry, configuration)
    assert len(plan) == 4
    assert not entry.subentries
    assert configuration == original
    apply_import(hass, entry, plan)
    before = by_key(entry)
    assert set(before) == {
        "entity:lamp",
        "device:thermostat",
        "export:occupancy",
        "action:panel",
    }

    configuration["entities"][0].update(name="Renamed lamp", brightness_join=20)
    configuration["devices"][0]["name"] = "Renamed thermostat"
    configuration["devices"][0]["entities"][0].update(
        name="Room reading", value_join=21
    )
    apply_import(hass, entry, prepare_import(hass, entry, configuration))
    after = by_key(entry)
    assert len(after) == 4
    assert {key: item.subentry_id for key, item in after.items()} == {
        key: item.subentry_id for key, item in before.items()
    }
    assert after["entity:lamp"].data["brightness_join"] == 20
    assert after["device:thermostat"].data["entities"][0]["id"] == "temperature"
    assert (
        after["action:panel"].data["actions"][0]["event_data"]["value"] == "{{ value }}"
    )


def test_import_preserves_omitted_mappings_and_device_features(hass, entry):
    apply_import(hass, entry, prepare_import(hass, entry, sample_inventory()))
    thermostat = device("Updated station")
    thermostat["entities"] = [thermostat["entities"][0]]
    thermostat["entities"][0]["value_join"] = 8
    apply_import(hass, entry, prepare_import(hass, entry, {"devices": [thermostat]}))
    assert len(entry.subentries) == 4
    updated = by_key(entry)["device:thermostat"]
    assert {child["id"] for child in updated.data["entities"]} == {
        "temperature",
        "enabled",
    }
    assert updated.data["entities"][0]["value_join"] == 8


@pytest.mark.parametrize(
    "invalid",
    [
        {
            "entities": [
                {"key": "bad", "platform": "light", "name": "Bad", "brightness_join": 0}
            ]
        },
        {"entities": [{"platform": "light", "name": "Bad", "brightness_join": 1}]},
        {"entities": ["not an object"]},
        {"devices": [{"key": "empty", "name": "No features", "entities": []}]},
        {
            "exports": [
                {
                    "key": "bad",
                    "name": "Bad",
                    "join_type": "analog",
                    "join": 1025,
                    "value_template": "1",
                }
            ]
        },
        {
            "actions": [
                {
                    "key": "bad",
                    "name": "Bad",
                    "join_type": "digital",
                    "join": 1,
                    "actions": [{"unknown_action_type": "bad"}],
                }
            ]
        },
    ],
)
def test_invalid_inventory_never_partially_applies(hass, entry, invalid):
    configuration = sample_inventory()
    for collection, items in invalid.items():
        configuration[collection].extend(items)
    with pytest.raises(vol.Invalid):
        prepare_import(hass, entry, configuration)
    assert not entry.subentries


def test_duplicate_mapping_keys_and_export_joins_are_rejected(hass, entry):
    configuration = sample_inventory()
    configuration["entities"].append(dict(configuration["entities"][0]))
    with pytest.raises(vol.Invalid, match="Duplicate mapping key"):
        prepare_import(hass, entry, configuration)
    assert not entry.subentries

    configuration = sample_inventory()
    configuration["exports"].append({**configuration["exports"][0], "key": "other"})
    with pytest.raises(vol.Invalid, match="only one export"):
        prepare_import(hass, entry, configuration)
    apply_import(hass, entry, prepare_import(hass, entry, sample_inventory()))
    before = export_inventory(entry)
    with pytest.raises(vol.Invalid, match="only one export"):
        prepare_import(hass, entry, {"exports": [configuration["exports"][1]]})
    assert export_inventory(entry) == before


def test_export_roundtrip_preserves_identity(hass, entry):
    apply_import(hass, entry, prepare_import(hass, entry, sample_inventory()))
    exported = export_inventory(entry)
    identities = {key: item.subentry_id for key, item in by_key(entry).items()}
    apply_import(hass, entry, prepare_import(hass, entry, exported))
    assert export_inventory(entry) == exported
    assert {key: item.subentry_id for key, item in by_key(entry).items()} == identities


def test_device_feature_platform_is_immutable(hass, entry):
    apply_import(hass, entry, prepare_import(hass, entry, {"devices": [device()]}))
    changed = device()
    changed["entities"][0] = {
        "id": "temperature",
        "platform": "light",
        "name": "Now a light",
        "brightness_join": 1,
    }
    with pytest.raises(vol.Invalid, match="platform unchanged"):
        prepare_import(hass, entry, {"devices": [changed]})
    assert (
        by_key(entry)["device:thermostat"].data["entities"][0]["platform"] == "sensor"
    )


def test_standalone_platform_is_immutable(hass, entry):
    apply_import(hass, entry, prepare_import(hass, entry, sample_inventory()))
    with pytest.raises(vol.Invalid, match="cannot change platform"):
        prepare_import(
            hass,
            entry,
            {
                "entities": [
                    {
                        "key": "lamp",
                        "platform": "switch",
                        "name": "Wrong type",
                        "switch_join": 3,
                    }
                ]
            },
        )


def test_duplicate_device_feature_ids_are_rejected():
    data = device()
    data.pop("key")
    data["entities"][1]["id"] = "temperature"
    with pytest.raises(vol.Invalid, match="Duplicate entity id"):
        validate_device(data)


def test_400_entity_import_is_idempotent(hass, entry):
    configuration = {
        "entities": [
            {
                "key": f"signal_{number}",
                "platform": "binary_sensor",
                "name": f"Signal {number}",
                "is_on_join": number,
            }
            for number in range(1, 401)
        ]
    }
    apply_import(hass, entry, prepare_import(hass, entry, configuration))
    before = {key: item.subentry_id for key, item in by_key(entry).items()}
    apply_import(hass, entry, prepare_import(hass, entry, configuration))
    assert len(entry.subentries) == 400
    assert {key: item.subentry_id for key, item in by_key(entry).items()} == before
    assert len(list(entity_mappings(entry))) == 400


async def test_options_flow_validates_before_committing_inventory(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    from homeassistant.helpers import config_validation as cv
    from probatio.codecs.fields import to_field_list

    json.dumps(
        to_field_list(result["data_schema"], custom_serializer=cv.custom_serializer)
    )
    invalid = sample_inventory()
    invalid["entities"][0]["brightness_join"] = 1025
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"configuration": invalid}
    )
    assert result["errors"] == {"base": "invalid_inventory"}
    assert not entry.subentries
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"configuration": sample_inventory()}
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert len(entry.subentries) == 4


async def test_device_flow_add_and_reconfigure_preserves_child_ids(hass, entry):
    data = device()
    data.pop("key")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "device"), context={"source": SOURCE_USER}, data=data
    )
    assert result["type"] == FlowResultType.CREATE_ENTRY
    item = next(iter(entry.subentries.values()))
    before = {mapping.suffix for mapping in entity_mappings(entry)}
    data["name"] = "Renamed station"
    data.pop("manufacturer")
    data.pop("model")
    data["entities"][0].update(name="Ambient", value_join=22)
    data["entities"].reverse()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "device"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": item.subentry_id},
        data=data,
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert {mapping.suffix for mapping in entity_mappings(entry)} == before
    assert "manufacturer" not in entry.subentries[item.subentry_id].data
    assert "model" not in entry.subentries[item.subentry_id].data


def test_device_form_suggestions_allow_removing_optional_metadata():
    from homeassistant.helpers import config_validation as cv
    from probatio.codecs.fields import to_field_list

    from custom_components.crestron.config_flow import device_form

    defaults = device()
    defaults.pop("key")
    form = device_form(defaults)
    serialized = to_field_list(form, custom_serializer=cv.custom_serializer)
    json.dumps(serialized)
    submitted = form({"name": defaults["name"], "entities": defaults["entities"]})
    assert "manufacturer" not in submitted
    assert "model" not in submitted


async def test_cross_platform_features_share_device_standalone_has_none(hass, entry):
    configuration = sample_inventory()
    configuration.pop("exports")
    configuration.pop("actions")
    apply_import(hass, entry, prepare_import(hass, entry, configuration))
    identities = by_key(entry)
    bridge = FakeBridge()
    with patch("custom_components.crestron.Bridge", return_value=bridge):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    standalone_id = registry.async_get_entity_id(
        "light", "crestron", f"{entry.entry_id}_{identities['entity:lamp'].subentry_id}"
    )
    sensor_id = registry.async_get_entity_id(
        "sensor",
        "crestron",
        f"{entry.entry_id}_{identities['device:thermostat'].subentry_id}_temperature",
    )
    switch_id = registry.async_get_entity_id(
        "switch",
        "crestron",
        f"{entry.entry_id}_{identities['device:thermostat'].subentry_id}_enabled",
    )
    assert standalone_id is not None
    assert sensor_id is not None
    assert switch_id is not None
    assert registry.async_get(standalone_id).device_id is None
    assert (
        registry.async_get(sensor_id).device_id
        == registry.async_get(switch_id).device_id
    )
    grouped = dr.async_get(hass).async_get(registry.async_get(sensor_id).device_id)
    connection = dr.async_get(hass).async_get_device_by_identifier(
        ("crestron", entry.entry_id), config_entry_id=entry.entry_id
    )
    assert grouped.name == "Thermostat"
    assert grouped.manufacturer == "Example Controls"
    assert grouped.model == "Room station"
    assert grouped.via_device_id == connection.id
    assert connection.entry_type == dr.DeviceEntryType.SERVICE
    assert connection.model == "XSIG connection"
    assert len(dr.async_get(hass).devices) == 2
    assert (
        registry.async_get(sensor_id).config_subentry_id
        == identities["device:thermostat"].subentry_id
    )
    bridge.feedback("analog", 1, 225)
    bridge.feedback("digital", 2, True)
    await hass.async_block_till_done()
    assert float(hass.states.get(sensor_id).state) == 22.5
    assert hass.states.get(switch_id).state == "on"

    updated = device("Renamed station")
    for key in ("key", "manufacturer", "model"):
        updated.pop(key)
    updated["entities"][0]["name"] = "Ambient"
    updated["entities"].reverse()
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "device"),
        context={
            "source": SOURCE_RECONFIGURE,
            "subentry_id": identities["device:thermostat"].subentry_id,
        },
        data=updated,
    )
    assert result["type"] == FlowResultType.ABORT
    await hass.async_block_till_done()
    assert registry.async_get(sensor_id).device_id == grouped.id
    assert registry.async_get(switch_id).device_id == grouped.id
    assert len(registry.entities) == 3
    updated_device = dr.async_get(hass).async_get(grouped.id)
    assert updated_device.name == "Renamed station"
    assert updated_device.manufacturer is None
    assert updated_device.model is None
    assert float(hass.states.get(sensor_id).state) == 22.5
    assert hass.states.get(switch_id).state == "on"
    bridge.start.assert_awaited_once()
    bridge.stop.assert_not_awaited()
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("disabled_by", [None, er.RegistryEntryDisabler.USER])
async def test_legacy_entity_migration_preserves_device_and_entity_ids(
    hass, disabled_by
):
    entry = MockConfigEntry(
        domain="crestron",
        title="Old bridge",
        minor_version=1,
        data={"name": "Old bridge", "host": "127.0.0.1", "port": 16384},
        subentries_data=[
            {
                "subentry_id": "old-lamp",
                "subentry_type": "entity",
                "title": "Old lamp",
                "unique_id": None,
                "data": {"platform": "light", "name": "Old lamp", "brightness_join": 3},
            }
        ],
    )
    entry.add_to_hass(hass)
    old_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id="old-lamp",
        identifiers={("crestron", "old-lamp")},
        name="Old lamp",
    )
    old_entity = er.async_get(hass).async_get_or_create(
        "light",
        "crestron",
        f"{entry.entry_id}_old-lamp",
        config_entry=entry,
        config_subentry_id="old-lamp",
        device_id=old_device.id,
        suggested_object_id="retained_lamp",
    )
    device_area = ar.async_get(hass).async_create("Office")
    entity_area = ar.async_get(hass).async_create("Reading nook")
    label = lr.async_get(hass).async_create("Evening lighting")
    dr.async_get(hass).async_update_device(
        old_device.id,
        name_by_user="Desk uplight",
        area_id=device_area.id,
        labels={label.label_id},
    )
    old_entity = er.async_get(hass).async_update_entity(
        old_entity.entity_id,
        new_entity_id="light.custom_reading_lamp",
        name="Task lighting",
        aliases=["Reading light"],
        area_id=entity_area.id,
        labels={label.label_id},
        hidden_by=er.RegistryEntryHider.USER,
        disabled_by=disabled_by,
    )
    er.async_get(hass).async_update_entity_options(
        old_entity.entity_id, "conversation", {"should_expose": False}
    )

    def assert_preserved_metadata():
        entity_record = er.async_get(hass).async_get("light.custom_reading_lamp")
        assert entity_record.device_id == old_device.id
        assert entity_record.unique_id == f"{entry.entry_id}_old-lamp"
        assert entity_record.name == "Task lighting"
        assert entity_record.aliases == ["Reading light"]
        assert entity_record.area_id == entity_area.id
        assert entity_record.labels == {label.label_id}
        assert entity_record.hidden_by == er.RegistryEntryHider.USER
        assert entity_record.disabled_by == disabled_by
        assert entity_record.options["conversation"] == {"should_expose": False}
        device_record = dr.async_get(hass).async_get(old_device.id)
        assert device_record.name_by_user == "Desk uplight"
        assert device_record.area_id == device_area.id
        assert device_record.labels == {label.label_id}
        assert (hass.states.get(old_entity.entity_id) is None) == (
            disabled_by is not None
        )

    bridge = FakeBridge()
    with patch("custom_components.crestron.Bridge", return_value=bridge):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.minor_version == 2
    assert entry.subentries["old-lamp"].data["legacy_device"] is True
    assert_preserved_metadata()
    assert len(er.async_get(hass).entities) == 1
    exported = export_inventory(entry)
    assert "legacy_device" not in exported["entities"][0]
    exported["entities"][0]["name"] = "Updated mapping name"
    apply_import(hass, entry, prepare_import(hass, entry, exported))
    assert entry.subentries["old-lamp"].data["legacy_device"] is True
    await hass.async_block_till_done()
    assert_preserved_metadata()
    assert len(er.async_get(hass).entities) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("include_empty_children", [False, True])
def test_existing_device_metadata_only_import_preserves_all_features(
    hass, entry, include_empty_children
):
    """Omitting every feature from an update still cannot delete any feature."""
    apply_import(hass, entry, prepare_import(hass, entry, {"devices": [device()]}))
    before = by_key(entry)["device:thermostat"]
    update = {"key": "thermostat", "name": "Renamed station"}
    if include_empty_children:
        update["entities"] = []
    configuration = {"devices": [update]}
    original = deepcopy(configuration)
    apply_import(hass, entry, prepare_import(hass, entry, configuration))
    after = by_key(entry)["device:thermostat"]
    assert after.subentry_id == before.subentry_id
    assert after.data["name"] == "Renamed station"
    assert after.data["entities"] == before.data["entities"]
    assert configuration == original


@pytest.mark.parametrize(
    "children",
    [
        {},
        "invalid",
        None,
        ["invalid"],
        [None],
        [{"id": [], "platform": "light", "name": "Invalid", "brightness_join": 1}],
    ],
)
def test_malformed_existing_device_children_do_not_partially_apply(
    hass, entry, children
):
    """Validate additive updates before either new or existing records change."""
    apply_import(hass, entry, prepare_import(hass, entry, {"devices": [device()]}))
    before = export_inventory(entry)
    configuration = {
        "entities": sample_inventory()["entities"],
        "devices": [
            {"key": "thermostat", "name": "Invalid update", "entities": children}
        ],
    }
    original = deepcopy(configuration)
    with pytest.raises(vol.Invalid):
        prepare_import(hass, entry, configuration)
    assert export_inventory(entry) == before
    assert configuration == original


@pytest.mark.parametrize("location", ["key", "feature_id"])
def test_inventory_rejects_trailing_newline_in_stable_keys(hass, entry, location):
    """A trailing newline must not create visually indistinguishable identities."""
    configuration = sample_inventory()
    if location == "key":
        configuration["devices"][0]["key"] += "\n"
    else:
        configuration["devices"][0]["entities"][0]["id"] += "\n"
    with pytest.raises(vol.Invalid):
        prepare_import(hass, entry, configuration)
    assert not entry.subentries
