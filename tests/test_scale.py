"""Exercise a large inventory through real HA platforms and a loopback peer."""

import asyncio
from unittest.mock import patch

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron.const import PLATFORMS
from custom_components.crestron.inventory import apply_import, prepare_import
from custom_components.crestron.xsig import encode_digital, read_message

from .test_integration import peer
from .test_xsig import wait_until


async def test_500_entities_bulk_edit_keeps_one_device_and_live_connection(
    hass, unused_tcp_port
):
    entry = MockConfigEntry(
        domain="crestron",
        title="Large house",
        minor_version=2,
        data={"name": "Large house", "host": "127.0.0.1", "port": unused_tcp_port},
    )
    entry.add_to_hass(hass)
    inventory = {
        "entities": [
            {
                "key": f"signal_{join}",
                "platform": "binary_sensor",
                "name": f"Signal {join}",
                "is_on_join": join,
            }
            for join in range(1, 501)
        ]
    }
    apply_import(hass, entry, prepare_import(hass, entry, inventory))
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    bridge = entry.runtime_data
    registry = er.async_get(hass)
    initial = er.async_entries_for_config_entry(registry, entry.entry_id)
    identity = {item.entity_id: item.id for item in initial}
    assert len(initial) == 500
    assert all(item.device_id is None for item in initial)
    assert (
        len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
    )
    reader, writer = await peer(entry)
    try:
        writer.write(b"".join(encode_digital(join, True) for join in range(1, 501)))
        await writer.drain()
        await wait_until(
            lambda: all(hass.states.get(item).state == "on" for item in identity)
        )
        for item in inventory["entities"]:
            item["name"] += " renamed"
        with patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            wraps=hass.config_entries.async_forward_entry_setups,
        ) as forward:
            apply_import(hass, entry, prepare_import(hass, entry, inventory))
            await hass.async_block_till_done()
            assert sum(len(call.args[1]) for call in forward.await_args_list) == len(
                PLATFORMS
            )
            apply_import(hass, entry, prepare_import(hass, entry, inventory))
            await hass.async_block_till_done()
            assert sum(len(call.args[1]) for call in forward.await_args_list) == len(
                PLATFORMS
            )
        assert entry.runtime_data is bridge
        assert bridge.available
        assert len(entry.subentries) == 500
        assert {
            item.entity_id: item.id
            for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        } == identity
        assert all(hass.states.get(item).state == "on" for item in identity)
        assert (
            len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id))
            == 1
        )
        await bridge.set_digital(1, False)
        assert await asyncio.wait_for(read_message(reader), 2) == ("digital", 1, False)
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
