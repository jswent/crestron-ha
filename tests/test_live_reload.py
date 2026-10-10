"""Real HA registry, migration, and TCP coverage for live mapping changes."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron.const import PLATFORMS
from custom_components.crestron.xsig import encode_digital, read_message
from tests.test_integration import peer, subentry
from tests.test_xsig import wait_until


async def create_entry(hass, port, mappings, *, minor_version=2):
    entry = MockConfigEntry(
        domain="crestron",
        title="House",
        data={"name": "House", "host": "127.0.0.1", "port": port},
        version=1,
        minor_version=minor_version,
        subentries_data=mappings,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_batch_mapping_edits_keep_connection_feedback_and_entity_id(
    hass, unused_tcp_port
):
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "entity",
                {
                    "platform": "switch",
                    "name": "Relay",
                    "switch_join": 1,
                },
            )
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    registry = er.async_get(hass)
    entity = er.async_entries_for_config_entry(registry, entry.entry_id)[0]
    assert entity.device_id is None
    mapping = next(iter(entry.subentries.values()))
    try:
        writer.write(encode_digital(1, True))
        await writer.drain()
        await wait_until(lambda: hass.states.get(entity.entity_id).state == "on")
        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            wraps=hass.config_entries.async_forward_entry_setups,
        ) as forward:
            for index in range(100):
                hass.config_entries.async_update_subentry(
                    entry,
                    mapping,
                    data={**mapping.data, "name": f"Relay {index}"},
                    title=f"Relay {index}",
                )
            await hass.async_block_till_done()
            assert sum(len(call.args[1]) for call in forward.await_args_list) == len(
                PLATFORMS
            )
        assert entry.runtime_data is bridge
        assert bridge.available
        assert bridge.get_digital(1) is True
        assert hass.states.get(entity.entity_id).state == "on"
        assert (
            hass.states.get(entity.entity_id).attributes["friendly_name"] == "Relay 99"
        )
        assert registry.async_get(entity.entity_id).id == entity.id
        await bridge.set_digital(1, False)
        assert await read_message(reader) == ("digital", 1, False)
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        writer.close()
        await writer.wait_closed()


async def test_device_feature_removal_keeps_sibling_and_shared_device(
    hass, unused_tcp_port
):
    children = [
        {"id": "power", "platform": "switch", "name": "Power", "switch_join": 1},
        {
            "id": "status",
            "platform": "binary_sensor",
            "name": "Status",
            "is_on_join": 2,
        },
    ]
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "device",
                {
                    "name": "Receiver",
                    "entities": children,
                },
            )
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    registry = er.async_get(hass)
    registered = er.async_entries_for_config_entry(registry, entry.entry_id)
    power = next(item for item in registered if item.domain == "switch")
    status = next(item for item in registered if item.domain == "binary_sensor")
    assert power.device_id == status.device_id
    assert power.device_id is not None
    devices = dr.async_get(hass)
    assert devices.async_get(power.device_id).via_device_id == bridge.device_id
    assert devices.async_get(bridge.device_id).entry_type is dr.DeviceEntryType.SERVICE
    mapping = next(iter(entry.subentries.values()))
    try:
        hass.config_entries.async_update_subentry(
            entry,
            mapping,
            data={
                **mapping.data,
                "entities": [children[0]],
            },
        )
        await hass.async_block_till_done()
        assert entry.runtime_data is bridge
        assert bridge.available
        assert registry.async_get(status.entity_id) is None
        assert registry.async_get(power.entity_id).id == power.id
        assert registry.async_get(power.entity_id).device_id == power.device_id
        writer.write(encode_digital(1, True))
        await writer.drain()
        await wait_until(lambda: hass.states.get(power.entity_id).state == "on")
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()


async def test_listener_port_edit_replaces_runtime_and_disconnects_old_peer(
    hass, unused_tcp_port_factory
):
    first_port, second_port = unused_tcp_port_factory(), unused_tcp_port_factory()
    entry = await create_entry(hass, first_port, [])
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    try:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "port": second_port}
        )
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is not bridge
        assert entry.runtime_data.bound_port == second_port
        assert not bridge.available
        assert await asyncio.wait_for(reader.read(), 2) == b""
        new_reader, new_writer = await peer(entry)
        await entry.runtime_data.set_analog(1, 42)
        assert await read_message(new_reader) == ("analog", 1, 42)
        new_writer.close()
        await new_writer.wait_closed()
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        writer.close()
        await writer.wait_closed()


async def test_migration_preserves_existing_entity_and_device_registry_ids(
    hass, unused_tcp_port
):
    entry = MockConfigEntry(
        domain="crestron",
        title="Old bridge",
        version=1,
        minor_version=1,
        data={"name": "Old bridge", "host": "127.0.0.1", "port": unused_tcp_port},
        subentries_data=[
            {
                **subentry(
                    "entity",
                    {"platform": "switch", "name": "Old relay", "switch_join": 1},
                ),
                "subentry_id": "existing-mapping",
            }
        ],
    )
    entry.add_to_hass(hass)
    devices = dr.async_get(hass)
    old_device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id="existing-mapping",
        identifiers={("crestron", "existing-mapping")},
        name="Old relay",
        manufacturer="Crestron",
        model="XSig switch",
    )
    registry = er.async_get(hass)
    old_entity = registry.async_get_or_create(
        "switch",
        "crestron",
        f"{entry.entry_id}_existing-mapping",
        suggested_object_id="my_existing_relay",
        config_entry=entry,
        config_subentry_id="existing-mapping",
        device_id=old_device.id,
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        assert entry.minor_version == 2
        mapping = entry.subentries["existing-mapping"]
        assert mapping.data["legacy_device"] is True
        assert mapping.unique_id == "entity:existing-mapping"
        assert registry.async_get(old_entity.entity_id).id == old_entity.id
        assert registry.async_get(old_entity.entity_id).device_id == old_device.id
        assert (
            hass.states.get(old_entity.entity_id).attributes["friendly_name"]
            == "Old relay"
        )
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_serializes_with_live_refresh(hass, unused_tcp_port):
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "entity",
                {
                    "platform": "switch",
                    "name": "Relay",
                    "switch_join": 1,
                },
            )
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    started, release = asyncio.Event(), asyncio.Event()
    forward = hass.config_entries.async_forward_entry_setups

    async def blocked_forward(*args):
        started.set()
        await release.wait()
        await forward(*args)

    mapping = next(iter(entry.subentries.values()))
    try:
        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=blocked_forward,
        ):
            hass.config_entries.async_update_subentry(
                entry,
                mapping,
                title="Updated",
                data={**mapping.data, "name": "Updated"},
            )
            await asyncio.wait_for(started.wait(), 2)
            unload = asyncio.create_task(
                hass.config_entries.async_unload(entry.entry_id)
            )
            await asyncio.sleep(0)
            assert not unload.done()
            release.set()
            assert await asyncio.wait_for(unload, 2)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert not bridge._callbacks
        assert not bridge._join_callbacks
        assert not bridge._mapping_tasks
        assert await asyncio.wait_for(reader.read(), 2) == b""
    finally:
        release.set()
        writer.close()
        await writer.wait_closed()


async def test_refresh_failure_cleans_up_and_falls_back_to_full_reload(
    hass, unused_tcp_port
):
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "entity",
                {
                    "platform": "switch",
                    "name": "Relay",
                    "switch_join": 1,
                },
            )
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    forward = hass.config_entries.async_forward_entry_setups
    fail = True

    async def fail_once(*args):
        nonlocal fail
        if fail:
            fail = False
            raise ValueError("invalid transient mapping")
        await forward(*args)

    mapping = next(iter(entry.subentries.values()))
    try:
        with patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=fail_once
        ):
            hass.config_entries.async_update_subentry(
                entry,
                mapping,
                title="Updated",
                data={**mapping.data, "name": "Updated"},
            )
            await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is not bridge
        assert not bridge._mapping_tasks
        assert not bridge._callbacks
        assert await asyncio.wait_for(reader.read(), 2) == b""
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize(
    "boundary", ["async_forward_entry_setups", "async_unload_platforms"]
)
async def test_cancelled_refresh_recovers_without_partial_platforms(
    hass, unused_tcp_port, boundary
):
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "entity",
                {
                    "platform": "switch",
                    "name": "Relay",
                    "switch_join": 1,
                },
            )
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    started, release = asyncio.Event(), asyncio.Event()
    forward = getattr(hass.config_entries, boundary)

    async def blocked_forward(*args):
        started.set()
        await release.wait()
        return await forward(*args)

    mapping = next(iter(entry.subentries.values()))
    try:
        with patch.object(
            hass.config_entries,
            boundary,
            side_effect=blocked_forward,
        ):
            hass.config_entries.async_update_subentry(
                entry,
                mapping,
                title="Updated",
                data={**mapping.data, "name": "Updated"},
            )
            await asyncio.wait_for(started.wait(), 2)
            refresh = bridge.refresh_task
            refresh.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await refresh
            await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is not bridge
        assert not bridge.loaded_platforms
        assert not bridge._callbacks
        assert not bridge._mapping_tasks
        assert await asyncio.wait_for(reader.read(), 2) == b""
    finally:
        release.set()
        await hass.config_entries.async_unload(entry.entry_id)
        writer.close()
        await writer.wait_closed()


async def test_deleted_device_child_is_removed_when_entry_is_enabled_again(
    hass, unused_tcp_port
):
    children = [
        {"id": "one", "platform": "switch", "name": "One", "switch_join": 1},
        {"id": "two", "platform": "switch", "name": "Two", "switch_join": 2},
    ]
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "device",
                {
                    "name": "Relay pack",
                    "entities": children,
                },
            )
        ],
    )
    mapping = next(iter(entry.subentries.values()))
    registry = er.async_get(hass)
    removed = registry.async_get_entity_id(
        "switch", "crestron", f"{entry.entry_id}_{mapping.subentry_id}_two"
    )
    assert removed is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_subentry(
        entry, mapping, data={**mapping.data, "entities": [children[0]]}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        assert registry.async_get(removed) is None
        assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 1
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_update_arriving_during_platform_setup_is_not_lost(hass, unused_tcp_port):
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "entity",
                {
                    "platform": "switch",
                    "name": "Relay",
                    "switch_join": 1,
                },
            )
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    started, release = asyncio.Event(), asyncio.Event()
    forward = hass.config_entries.async_forward_entry_setups

    async def blocked_forward(*args):
        started.set()
        await release.wait()
        await forward(*args)

    mapping = next(iter(entry.subentries.values()))
    try:
        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=blocked_forward,
        ):
            hass.config_entries.async_update_subentry(
                entry, mapping, title="First", data={**mapping.data, "name": "First"}
            )
            await asyncio.wait_for(started.wait(), 2)
            hass.config_entries.async_update_subentry(
                entry, mapping, title="Final", data={**mapping.data, "name": "Final"}
            )
            release.set()
            await hass.async_block_till_done()
        assert entry.runtime_data is bridge
        assert bridge.available
        registry = er.async_get(hass)
        entity = er.async_entries_for_config_entry(registry, entry.entry_id)[0]
        assert hass.states.get(entity.entity_id).attributes["friendly_name"] == "Final"
        assert (
            bridge.applied_config["subentries"][mapping.subentry_id]["title"] == "Final"
        )
    finally:
        release.set()
        await hass.config_entries.async_unload(entry.entry_id)
        writer.close()
        await writer.wait_closed()


async def test_queued_listener_after_unload_is_harmless(hass, unused_tcp_port):
    from custom_components.crestron import async_reload_entry

    entry = await create_entry(hass, unused_tcp_port, [])
    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_reload_entry(hass, entry)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_edit_during_initial_setup_is_applied_after_setup_completes(
    hass, unused_tcp_port
):
    entry = MockConfigEntry(
        domain="crestron",
        title="House",
        version=1,
        minor_version=2,
        data={"host": "127.0.0.1", "port": unused_tcp_port},
        subentries_data=[
            subentry(
                "entity",
                {
                    "platform": "switch",
                    "name": "Relay",
                    "switch_join": 1,
                },
            )
        ],
    )
    entry.add_to_hass(hass)
    started, release = asyncio.Event(), asyncio.Event()
    forward = hass.config_entries.async_forward_entry_setups

    async def blocked_forward(*args):
        started.set()
        await release.wait()
        await forward(*args)

    with patch.object(
        hass.config_entries, "async_forward_entry_setups", side_effect=blocked_forward
    ):
        setup = asyncio.create_task(hass.config_entries.async_setup(entry.entry_id))
        await asyncio.wait_for(started.wait(), 2)
        mapping = next(iter(entry.subentries.values()))
        hass.config_entries.async_update_subentry(
            entry, mapping, title="Changed", data={**mapping.data, "name": "Changed"}
        )
        release.set()
        assert await setup
        await hass.async_block_till_done()
    try:
        assert entry.state is ConfigEntryState.LOADED
        assert (
            entry.runtime_data.applied_config["subentries"][mapping.subentry_id][
                "title"
            ]
            == "Changed"
        )
    finally:
        await hass.config_entries.async_unload(entry.entry_id)


async def test_entity_rename_does_not_cancel_running_actions(hass, unused_tcp_port):
    started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def wait_action(call):
        started.set()
        await release.wait()
        completed.set()

    hass.services.async_register("dummy", "wait", wait_action)
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "entity", {"platform": "switch", "name": "Relay", "switch_join": 1}
            ),
            subentry(
                "action",
                {
                    "join_type": "digital",
                    "join": 2,
                    "actions": [{"action": "dummy.wait"}],
                },
            ),
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    original_script = bridge._scripts[0][1]
    mapping = next(
        item for item in entry.subentries.values() if item.subentry_type == "entity"
    )
    try:
        writer.write(encode_digital(2, True))
        await writer.drain()
        await asyncio.wait_for(started.wait(), 2)
        hass.config_entries.async_update_subentry(
            entry, mapping, data={**mapping.data, "name": "Renamed"}, title="Renamed"
        )
        await wait_until(
            lambda: bridge.applied_config["subentries"][mapping.subentry_id]["title"]
            == "Renamed"
        )
        assert bridge._scripts[0][1] is original_script
        assert original_script.is_running
        assert not completed.is_set()
        assert bridge.available
        release.set()
        await asyncio.wait_for(completed.wait(), 2)
        await hass.async_block_till_done()
    finally:
        release.set()
        await hass.config_entries.async_unload(entry.entry_id)
        writer.close()
        await writer.wait_closed()


async def test_export_edit_keeps_entity_platforms_loaded(hass, unused_tcp_port):
    entry = await create_entry(
        hass,
        unused_tcp_port,
        [
            subentry(
                "entity", {"platform": "switch", "name": "Relay", "switch_join": 1}
            ),
            subentry(
                "export", {"join_type": "analog", "join": 1, "value_template": "10"}
            ),
        ],
    )
    bridge = entry.runtime_data
    reader, writer = await peer(entry)
    assert await read_message(reader) == ("analog", 1, 10)
    mapping = next(
        item for item in entry.subentries.values() if item.subentry_type == "export"
    )
    try:
        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            wraps=hass.config_entries.async_forward_entry_setups,
        ) as forward:
            hass.config_entries.async_update_subentry(
                entry, mapping, data={**mapping.data, "value_template": "20"}
            )
            await hass.async_block_till_done()
            forward.assert_not_awaited()
        assert entry.runtime_data is bridge
        assert bridge.available
        assert await read_message(reader) == ("analog", 1, 20)
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        writer.close()
        await writer.wait_closed()
