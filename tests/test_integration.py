"""Real Home Assistant plus loopback Crestron processors."""

import asyncio
import json

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron.diagnostics import async_get_config_entry_diagnostics
from custom_components.crestron.xsig import (
    encode_analog,
    encode_digital,
    encode_serial,
    read_message,
)


def subentry(kind, data, title="Mapping"):
    return {"subentry_type": kind, "title": title, "unique_id": None, "data": data}


async def setup_bridge(hass, port, name="Main", mappings=()):
    entry = MockConfigEntry(
        domain="crestron",
        title=name,
        data={"name": name, "host": "127.0.0.1", "port": port},
        subentries_data=mappings,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def peer(entry):
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", entry.runtime_data.bound_port
    )
    assert await asyncio.wait_for(reader.readexactly(1), 2) == b"\xfd"
    return reader, writer


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_two_bridges_isolated_services_and_unload(hass, unused_tcp_port_factory):
    mapping = subentry(
        "entity", {"platform": "light", "name": "Lamp", "brightness_join": 1}
    )
    first = await setup_bridge(hass, unused_tcp_port_factory(), "Lighting", [mapping])
    second = await setup_bridge(hass, unused_tcp_port_factory(), "Audio", [mapping])
    r1, w1 = await peer(first)
    r2, w2 = await peer(second)
    w1.write(encode_analog(1, 32768))
    await w1.drain()
    w2.write(encode_analog(1, 65535))
    await w2.drain()
    await eventually(
        lambda: first.runtime_data.get_analog(1) == 32768
        and second.runtime_data.get_analog(1) == 65535
    )
    result = await hass.services.async_call(
        "crestron",
        "get_join",
        {"entry_id": first.entry_id, "join_type": "analog", "join": 1},
        blocking=True,
        return_response=True,
    )
    assert result == {"value": 32768, "available": True}
    await hass.services.async_call(
        "crestron",
        "set_join",
        {"entry_id": second.entry_id, "join_type": "analog", "join": 1, "value": 12345},
        blocking=True,
    )
    assert await asyncio.wait_for(read_message(r2), 2) == ("analog", 1, 12345)
    assert first.runtime_data.get_analog(1) == 32768
    registry = er.async_get(hass)
    a = er.async_entries_for_config_entry(registry, first.entry_id)[0]
    b = er.async_entries_for_config_entry(registry, second.entry_id)[0]
    assert a.unique_id != b.unique_id
    assert await hass.config_entries.async_unload(first.entry_id)
    assert await asyncio.wait_for(r1.read(), 2) == b""
    assert second.runtime_data.available
    replacement = await asyncio.start_server(
        lambda r, w: w.close(), "127.0.0.1", first.data["port"]
    )
    replacement.close()
    await replacement.wait_closed()
    w1.close()
    w2.close()
    await w1.wait_closed()
    await w2.wait_closed()
    await hass.config_entries.async_unload(second.entry_id)


async def test_unconnected_and_invalid_service_calls(hass, unused_tcp_port):
    entry = await setup_bridge(hass, unused_tcp_port)
    with pytest.raises(HomeAssistantError, match="disconnected"):
        await hass.services.async_call(
            "crestron",
            "set_join",
            {"entry_id": entry.entry_id, "join_type": "analog", "join": 1, "value": 1},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "crestron",
            "get_join",
            {"entry_id": entry.entry_id, "join_type": "analog", "join": 1025},
            blocking=True,
            return_response=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "crestron",
            "get_join",
            {"entry_id": "missing", "join_type": "digital", "join": 1},
            blocking=True,
            return_response=True,
        )
    await hass.config_entries.async_unload(entry.entry_id)


async def test_port_conflict_retries(hass, unused_tcp_port):
    listener = await asyncio.start_server(
        lambda r, w: w.close(), "127.0.0.1", unused_tcp_port
    )
    entry = MockConfigEntry(
        domain="crestron",
        data={"name": "Main", "host": "127.0.0.1", "port": unused_tcp_port},
    )
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state == ConfigEntryState.SETUP_RETRY
    listener.close()
    await listener.wait_closed()
    await hass.config_entries.async_unload(entry.entry_id)


async def test_actions_templates_sync_and_private_diagnostics(hass, unused_tcp_port):
    hass.states.async_set("sensor.export", "314")
    mappings = [
        subentry(
            "export",
            {
                "name": "Export secret name",
                "join_type": "analog",
                "join": 8,
                "value_template": "{{ states('sensor.export') }}",
            },
        ),
        subentry(
            "action",
            {
                "name": "Action",
                "join_type": "digital",
                "join": 9,
                "ignore_release": True,
                "actions": [
                    {"event": "panel_pressed", "event_data": {"pressed": "{{ value }}"}}
                ],
            },
        ),
    ]
    entry = await setup_bridge(hass, unused_tcp_port, mappings=mappings)
    actions = []
    events = []
    unsub = hass.bus.async_listen("panel_pressed", actions.append)
    unsub2 = hass.bus.async_listen("crestron_join_changed", events.append)
    reader, writer = await peer(entry)
    assert await asyncio.wait_for(read_message(reader), 2) == ("analog", 8, 314)
    writer.write(
        encode_digital(9, True)
        + encode_digital(9, False)
        + encode_serial(1, "private serial")
    )
    await writer.drain()
    await eventually(lambda: len(actions) == 1 and len(events) == 3)
    assert actions[0].data["pressed"] is True
    assert events[0].data["entry_id"] == entry.entry_id
    hass.states.async_set("sensor.export", "315")
    assert await asyncio.wait_for(read_message(reader), 2) == ("analog", 8, 315)
    writer.write(b"\xfb")
    await writer.drain()
    assert await asyncio.wait_for(read_message(reader), 2) == ("analog", 8, 315)
    diagnostics = json.dumps(await async_get_config_entry_diagnostics(hass, entry))
    for secret in (
        "private serial",
        "127.0.0.1",
        "Export secret name",
        "sensor.export",
    ):
        assert secret not in diagnostics
    unsub()
    unsub2()
    bridge = entry.runtime_data
    await hass.config_entries.async_unload(entry.entry_id)
    assert not bridge._mapping_tasks
    writer.close()
    await writer.wait_closed()


async def test_mapping_remove_keeps_connection_and_removes_registry_entity(
    hass, unused_tcp_port
):
    entry = await setup_bridge(
        hass,
        unused_tcp_port,
        mappings=[
            subentry(
                "entity", {"platform": "switch", "name": "Relay", "switch_join": 2}
            )
        ],
    )
    registry = er.async_get(hass)
    entity = er.async_entries_for_config_entry(registry, entry.entry_id)[0]
    old_bridge = entry.runtime_data
    sid = next(iter(entry.subentries))
    hass.config_entries.async_remove_subentry(entry, sid)
    await hass.async_block_till_done()
    assert entry.runtime_data is old_bridge
    assert registry.async_get(entity.entity_id) is None
    assert entry.state == ConfigEntryState.LOADED
    await hass.config_entries.async_unload(entry.entry_id)


async def test_diagnostics_before_successful_setup(hass):
    """A bind/setup failure must still allow a useful diagnostic download."""
    entry = MockConfigEntry(
        domain="crestron", data={"host": "192.0.2.1", "port": 16384}
    )
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["transport"] == {"runtime_initialized": False}
    assert "192.0.2.1" not in json.dumps(result)
