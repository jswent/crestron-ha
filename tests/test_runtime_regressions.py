"""Regression tests for bridge runtime behavior and scalability."""

import asyncio
import logging
from collections import Counter
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.script import DATA_SCRIPTS

from custom_components.crestron.bridge import Bridge
from custom_components.crestron.xsig import XSigServer, read_message
from tests.test_bridge_edges import bridge_entry, mapping
from tests.test_xsig import wait_until


@pytest.mark.parametrize("error_type", [HomeAssistantError, RuntimeError])
async def test_action_logs_do_not_expose_serial_values(hass, caplog, error_type):
    secret = "private-serial-value"

    async def fail(call):
        raise error_type(call.data["message"])

    hass.services.async_register("dummy", "fail", fail)
    entry = bridge_entry(
        hass,
        [
            mapping(
                "action",
                {
                    "join_type": "serial",
                    "join": 1,
                    "actions": [
                        {"action": "dummy.fail", "data": {"message": "{{ value }}"}}
                    ],
                },
            )
        ],
    )
    bridge = Bridge(hass, entry)
    bridge.setup_mappings()
    with caplog.at_level(logging.WARNING):
        bridge._message("serial", 1, secret)
        await hass.async_block_till_done()
    await bridge.stop()
    assert secret not in caplog.text


async def test_reconnect_during_export_sync_sends_complete_snapshot(hass):
    entry = bridge_entry(
        hass,
        [
            mapping(
                "export",
                {"join_type": "analog", "join": join, "value_template": str(join * 10)},
            )
            for join in (1, 2, 3)
        ],
    )
    bridge = Bridge(hass, entry)
    bridge.setup_mappings()
    await bridge.start("127.0.0.1", 0)
    first_reader, first_writer = await asyncio.open_connection(
        "127.0.0.1", bridge.bound_port
    )
    second_writer = None
    release = asyncio.Event()
    try:
        assert await read_message(first_reader) == ("update", None, None)
        for join in (1, 2, 3):
            assert await read_message(first_reader) == ("analog", join, join * 10)
        await wait_until(lambda: bridge._sync_task.done())
        with patch.object(bridge._writer, "drain", release.wait):
            first_writer.write(b"\xfb")
            await first_writer.drain()
            assert await read_message(first_reader) == ("analog", 1, 10)
            first_writer.close()
            await first_writer.wait_closed()
            await wait_until(lambda: not bridge.available)
            second_reader, second_writer = await asyncio.open_connection(
                "127.0.0.1", bridge.bound_port
            )
            await wait_until(lambda: bridge.available)
            release.set()
            assert await read_message(second_reader) == ("update", None, None)
            received = []
            try:
                async with asyncio.timeout(0.1):
                    for _ in range(3):
                        received.append(await read_message(second_reader))
            except TimeoutError:
                pass
            assert received == [("analog", join, join * 10) for join in (1, 2, 3)]
    finally:
        release.set()
        first_writer.close()
        await first_writer.wait_closed()
        if second_writer is not None:
            second_writer.close()
            await second_writer.wait_closed()
        await bridge.stop()


async def test_mapping_refresh_unloads_scripts_but_keeps_transport(hass):
    entry = bridge_entry(
        hass,
        [
            mapping(
                "action",
                {
                    "join_type": "digital",
                    "join": 1,
                    "actions": [{"event": "review_action"}],
                },
            )
        ],
    )
    bridge = Bridge(hass, entry)
    await bridge.start("127.0.0.1", 0)
    bridge.setup_mappings()
    reader, writer = await asyncio.open_connection("127.0.0.1", bridge.bound_port)
    try:
        assert await read_message(reader) == ("update", None, None)
        initial_port = bridge.bound_port
        for _ in range(5):
            old_scripts = [script for _, script in bridge._scripts]
            await bridge.async_stop_mappings()
            assert all(
                id(script) not in hass.data[DATA_SCRIPTS] for script in old_scripts
            )
            assert bridge.available
            assert bridge.bound_port == initial_port
            assert not bridge._mapping_unsubs
            bridge.setup_mappings()
        await bridge.set_analog(1, 42)
        assert await read_message(reader) == ("analog", 1, 42)
    finally:
        await bridge.stop()
        writer.close()
        await writer.wait_closed()
    assert not hass.data[DATA_SCRIPTS]


async def test_join_index_dispatches_only_relevant_subscribers():
    server = XSigServer()
    messages = Counter()
    unsubscribes = []
    for number in range(1, 501):

        def received(kind, join, value, number=number):
            messages[number] += 1

        unsubscribes.append(server.subscribe(received, joins={("analog", number)}))
    wildcard = []
    server.subscribe(lambda *message: wildcard.append(message))
    for join in range(1, 501):
        server._notify("analog", join, 42)
    assert sum(messages.values()) == 500
    assert len(wildcard) == 500
    server._notify("sync", None, None)
    assert sum(messages.values()) == 500
    server._notify("available", None, True)
    assert sum(messages.values()) == 1000
    assert len(wildcard) == 502
    for unsubscribe in unsubscribes:
        unsubscribe()
        unsubscribe()
    assert not server._join_callbacks
    assert not server._availability_callbacks


async def test_template_error_logs_omit_template_bodies(hass, caplog):
    secret = "private-template-text"
    entry = bridge_entry(
        hass,
        [
            mapping(
                "export",
                {
                    "join_type": "analog",
                    "join": 1,
                    "value_template": "{{ '" + secret + "' | int }}",
                },
            )
        ],
    )
    bridge = Bridge(hass, entry)
    # Enabling HA's own template/event DEBUG logging is outside the integration's
    # redaction boundary; ordinary template errors must still omit all contents.
    with caplog.at_level(logging.WARNING):
        bridge.setup_mappings()
        await hass.async_block_till_done()
        await bridge._sync_exports()
        await bridge.stop()
    assert secret not in caplog.text
