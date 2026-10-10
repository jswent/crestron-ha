"""Regression checks for mapping lifecycle and failed setup cleanup."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import async_get_platforms
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron import async_setup_entry
from custom_components.crestron.bridge import Bridge
from custom_components.crestron.services import async_register_services


def mapping(kind, data):
    return {
        "subentry_type": kind,
        "title": "Mapping",
        "unique_id": None,
        "data": data,
    }


def bridge_entry(hass, mappings=(), domain="crestron"):
    entry = MockConfigEntry(
        domain=domain,
        title="Bridge",
        data={"name": "Bridge", "host": "127.0.0.1", "port": 16384},
        subentries_data=mappings,
    )
    entry.add_to_hass(hass)
    return entry


async def test_stop_cancels_inflight_action_and_unsubscribes(hass):
    started = asyncio.Event()
    release = asyncio.Event()
    completed = []

    async def wait_for_release(call):
        started.set()
        await release.wait()
        completed.append(call.data)

    hass.services.async_register("dummy", "wait", wait_for_release)
    entry = bridge_entry(
        hass,
        [
            mapping(
                "action",
                {
                    "join_type": "digital",
                    "join": 2,
                    "actions": [{"action": "dummy.wait"}],
                },
            )
        ],
    )
    bridge = Bridge(hass, entry)
    bridge.setup_mappings()
    bridge._notify("digital", 2, True)
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.wait_for(bridge.stop(), 2)
    release.set()
    bridge._notify("digital", 2, True)
    await hass.async_block_till_done()
    assert completed == []
    assert not bridge._mapping_tasks
    assert not bridge._mapping_unsubs


async def test_invalid_export_does_not_block_other_exports(hass):
    entry = bridge_entry(
        hass,
        [
            mapping(
                "export",
                {"join_type": "analog", "join": 1, "value_template": "invalid number"},
            ),
            mapping(
                "export", {"join_type": "analog", "join": 2, "value_template": "42"}
            ),
        ],
    )
    bridge = Bridge(hass, entry)
    bridge._available = True
    with patch.object(bridge, "set_analog", new_callable=AsyncMock) as write:
        bridge.setup_mappings()
        await hass.async_block_till_done()
        write.assert_awaited_once_with(2, 42)
        write.reset_mock()
        await bridge._sync_exports()
        write.assert_awaited_once_with(2, 42)
        await bridge.stop()


async def test_template_listener_removed_on_stop(hass):
    hass.states.async_set("sensor.export", "1")
    entry = bridge_entry(
        hass,
        [
            mapping(
                "export",
                {
                    "join_type": "analog",
                    "join": 1,
                    "value_template": "{{ states('sensor.export') }}",
                },
            )
        ],
    )
    bridge = Bridge(hass, entry)
    bridge._available = True
    with patch.object(bridge, "set_analog", new_callable=AsyncMock) as write:
        bridge.setup_mappings()
        await hass.async_block_till_done()
        write.assert_awaited_once_with(1, 1)
        await bridge.stop()
        write.reset_mock()
        hass.states.async_set("sensor.export", "2")
        await hass.async_block_till_done()
        write.assert_not_called()
        assert not bridge._mapping_tasks


async def test_services_reject_unloaded_and_other_domain_entries(hass):
    async_register_services(hass)
    own = bridge_entry(hass)
    other = bridge_entry(hass, domain="other_integration")
    for entry in (own, other):
        with pytest.raises(ServiceValidationError, match="loaded Crestron"):
            await hass.services.async_call(
                "crestron",
                "get_join",
                {"entry_id": entry.entry_id, "join_type": "digital", "join": 1},
                blocking=True,
                return_response=True,
            )


async def test_failed_mapping_setup_removes_forwarded_entities(hass):
    """A runtime mapping failure must not leave half-loaded entity platforms."""
    entry = bridge_entry(
        hass,
        [
            mapping(
                "entity",
                {
                    "platform": "switch",
                    "name": "Relay",
                    "switch_join": 1,
                },
            )
        ],
    )
    with (
        patch.object(Bridge, "start", new_callable=AsyncMock),
        patch.object(
            Bridge, "setup_mappings", side_effect=ValueError("Bad stored mapping")
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert all(
        not platform.entities for platform in async_get_platforms(hass, "crestron")
    )
    assert not entry.runtime_data._callbacks
    assert not entry.runtime_data._mapping_tasks
    # HA may retain an unavailable registry placeholder after unloading an entity.
    for state in hass.states.async_all("switch"):
        assert state.state == "unavailable"
        assert state.attributes.get("restored") is True


async def test_cancelled_setup_stops_listener(hass):
    """A cancellation after binding must release resources before propagating."""
    entry = bridge_entry(hass)
    with (
        patch.object(Bridge, "start", new_callable=AsyncMock),
        patch.object(Bridge, "stop", new_callable=AsyncMock) as stop,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=asyncio.CancelledError,
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await async_setup_entry(hass, entry)
        stop.assert_awaited_once()


async def test_repeated_sync_requests_share_one_pending_task(hass):
    """A slow peer cannot turn repeated sync requests into an unbounded backlog."""
    bridge = Bridge(hass, bridge_entry(hass))
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_sync():
        started.set()
        await release.wait()

    with patch.object(bridge, "_sync_exports", side_effect=blocked_sync) as sync:
        bridge._message("sync", None, None)
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(100):
            bridge._message("sync", None, None)
        await asyncio.sleep(0)
        assert sync.await_count == 1
        assert len(bridge._mapping_tasks) <= 1
        release.set()
        await hass.async_block_till_done()
        await bridge.stop()
        assert not bridge._mapping_tasks


async def test_export_backpressure_keeps_only_latest_pending_value(hass):
    """The first blocked write is followed by current state, not stale history."""
    bridge = Bridge(hass, bridge_entry(hass))
    started = asyncio.Event()
    release = asyncio.Event()
    written = []
    data = {"join_type": "analog", "join": 1}

    async def blocked_export(mapping_data, value):
        written.append(value)
        if len(written) == 1:
            started.set()
            await release.wait()

    bridge._available = True
    with patch.object(bridge, "_export_value", side_effect=blocked_export):
        bridge._queue_export(data, 0)
        await asyncio.wait_for(started.wait(), 2)
        for value in range(1, 101):
            bridge._queue_export(data, value)
        await asyncio.sleep(0)
        assert len(bridge._mapping_tasks) <= 1
        release.set()
        await hass.async_block_till_done()
        assert written == [0, 100]
        await bridge.stop()
        assert not bridge._mapping_tasks


async def test_stop_cancels_blocked_export_and_discards_pending_value(hass):
    bridge = Bridge(hass, bridge_entry(hass))
    started = asyncio.Event()
    release = asyncio.Event()
    data = {"join_type": "analog", "join": 1}
    written = []

    async def blocked_export(mapping_data, value):
        written.append(value)
        started.set()
        await release.wait()

    bridge._available = True
    with patch.object(bridge, "_export_value", side_effect=blocked_export):
        bridge._queue_export(data, 1)
        await asyncio.wait_for(started.wait(), 2)
        bridge._queue_export(data, 2)
        await asyncio.wait_for(bridge.stop(), 2)
        release.set()
        await hass.async_block_till_done()
        assert written == [1]
        assert not bridge._mapping_tasks
