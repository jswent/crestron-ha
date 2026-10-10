"""Real HA platforms, real TCP XSIG, and a deliberately dumb processor peer."""

import asyncio

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron import xsig
from custom_components.crestron.xsig import (
    encode_analog,
    encode_digital,
    encode_serial,
    read_message,
)

MAPPINGS = {
    "contact": {"platform": "binary_sensor", "is_on_join": 1},
    "temperature": {"platform": "sensor", "value_join": 1, "divisor": 10},
    "text": {"platform": "sensor", "value_join": 1, "join_type": "serial"},
    "relay": {"platform": "switch", "switch_join": 2},
    "toggle": {"platform": "switch", "switch_join": 3, "pulsed": True},
    "lamp": {"platform": "light", "brightness_join": 2},
    "shade": {
        "platform": "cover",
        "pos_join": 3,
        "stop_join": 4,
        "is_opening_join": 5,
        "is_closing_join": 6,
        "is_closed_join": 7,
    },
    "thermostat": {
        "platform": "climate",
        "heat_sp_join": 10,
        "cool_sp_join": 11,
        "reg_temp_join": 12,
        "mode_heat_join": 10,
        "mode_cool_join": 11,
        "mode_auto_join": 12,
        "mode_off_join": 13,
        "fan_on_join": 14,
        "fan_auto_join": 15,
        "h1_join": 16,
        "c1_join": 17,
        "fa_join": 18,
    },
    "audio": {
        "platform": "media_player",
        "power_on_join": 20,
        "power_off_join": 21,
        "mute_join": 22,
        "volume_join": 20,
        "source_number_join": 21,
        "sources": {"1": "Radio", "2": "Music"},
        "default_source": 2,
    },
    "digital_audio": {
        "platform": "media_player",
        "power_on_join": 30,
        "power_off_join": 31,
        "mute_join": 32,
        "volume_join": 30,
        "source_digital_joins": {"40": "Radio", "41": "Music"},
        "default_source": 41,
    },
}


async def eventually(predicate):
    """Wait for a known state transition without assuming TCP scheduling."""
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
async def network_platforms(hass, unused_tcp_port):
    """Load real entry/platforms and guarantee cleanup even on assertion failure."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    entry = MockConfigEntry(
        domain="crestron",
        title="Integration lab",
        data={"name": "Integration lab", "host": "127.0.0.1", "port": unused_tcp_port},
        subentries_data=[
            {
                "subentry_type": "entity",
                "subentry_id": name,
                "title": name,
                "unique_id": None,
                "data": {"name": name, **mapping},
            }
            for name, mapping in MAPPINGS.items()
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    ids = {
        name: registry.async_get_entity_id(
            mapping["platform"], "crestron", f"{entry.entry_id}_{name}"
        )
        for name, mapping in MAPPINGS.items()
    }
    assert all(ids.values())
    peers = []

    async def connect():
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", entry.runtime_data.bound_port
        )
        peers.append(writer)
        assert await asyncio.wait_for(reader.readexactly(1), 2) == b"\xfd"
        await eventually(lambda: entry.runtime_data.available)
        return reader, writer

    try:
        yield entry, ids, connect
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        for writer in peers:
            writer.close()
            await writer.wait_closed()


async def test_all_platform_commands_feedback_and_reconnect(hass, network_platforms):
    """Drive actual entity services over the wire, with no mocked runtime or entities."""
    entry, ids, connect = network_platforms
    assert all(
        hass.states.get(entity_id).state == "unavailable" for entity_id in ids.values()
    )
    reader, writer = await connect()
    assert all(
        hass.states.get(entity_id).state == "unknown" for entity_id in ids.values()
    )

    digital = {
        1: True,
        2: False,
        3: False,
        5: False,
        6: False,
        7: False,
        10: True,
        11: False,
        12: False,
        13: False,
        14: False,
        15: True,
        16: False,
        17: False,
        18: False,
        20: True,
        22: False,
        30: True,
        32: False,
        40: True,
        41: False,
    }
    analog = {
        1: 725,
        2: 32896,
        3: 32768,
        10: 680,
        11: 750,
        12: 715,
        20: 32768,
        21: 1,
        30: 65535,
    }
    frames = b"".join(encode_digital(join, value) for join, value in digital.items())
    frames += b"".join(encode_analog(join, value) for join, value in analog.items())
    frames += encode_serial(1, "Ready")
    writer.write(frames)
    await writer.drain()
    await eventually(lambda: hass.states.get(ids["text"]).state == "Ready")
    expected_states = {
        "contact": "on",
        "temperature": "72.5",
        "text": "Ready",
        "relay": "off",
        "toggle": "off",
        "lamp": "on",
        "shade": "open",
        "thermostat": "heat",
        "audio": "on",
        "digital_audio": "on",
    }
    assert {
        name: hass.states.get(entity_id).state for name, entity_id in ids.items()
    } == expected_states
    assert hass.states.get(ids["lamp"]).attributes["brightness"] == 128
    assert hass.states.get(ids["shade"]).attributes["current_position"] == 50
    assert hass.states.get(ids["thermostat"]).attributes["current_temperature"] == 71.5
    assert hass.states.get(ids["thermostat"]).attributes["temperature"] == 68
    assert hass.states.get(ids["audio"]).attributes["source"] == "Radio"
    assert hass.states.get(ids["digital_audio"]).attributes["source"] == "Radio"

    async def command(name, service, expected, **data):
        await hass.services.async_call(
            MAPPINGS[name]["platform"],
            service,
            {"entity_id": ids[name], **data},
            blocking=True,
        )
        actual = [await asyncio.wait_for(read_message(reader), 2) for _ in expected]
        assert actual == expected

    await command("relay", "turn_on", [("digital", 2, True)])
    assert hass.states.get(ids["relay"]).state == "off"
    await command("relay", "turn_off", [("digital", 2, False)])
    await command("toggle", "turn_on", [("digital", 3, True), ("digital", 3, False)])
    # A command does not replace physical feedback. Still off means off is a no-op.
    await command("toggle", "turn_off", [])
    writer.write(encode_digital(3, True))
    await writer.drain()
    await eventually(lambda: hass.states.get(ids["toggle"]).state == "on")
    expected_states["toggle"] = "on"
    await command("toggle", "turn_off", [("digital", 3, True), ("digital", 3, False)])
    await command("lamp", "turn_on", [("analog", 2, 65535)], brightness=255)
    assert hass.states.get(ids["lamp"]).attributes["brightness"] == 128
    await command("lamp", "turn_off", [("analog", 2, 0)])
    await command("shade", "set_cover_position", [("analog", 3, 65535)], position=100)
    await command("shade", "close_cover", [("analog", 3, 0)])
    await command("shade", "open_cover", [("analog", 3, 65535)])
    await command("shade", "stop_cover", [("digital", 4, True), ("digital", 4, False)])
    await command(
        "thermostat", "set_temperature", [("analog", 10, 685)], temperature=68.5
    )
    await command(
        "thermostat",
        "set_temperature",
        [("analog", 10, 682), ("analog", 11, 754)],
        target_temp_low=68.2,
        target_temp_high=75.4,
    )
    await command(
        "thermostat",
        "set_hvac_mode",
        [("digital", 12, True), ("digital", 12, False)],
        hvac_mode="heat_cool",
    )
    await command(
        "thermostat",
        "set_fan_mode",
        [("digital", 14, True), ("digital", 14, False)],
        fan_mode="on",
    )
    await command(
        "thermostat", "turn_off", [("digital", 13, True), ("digital", 13, False)]
    )
    await command("audio", "volume_set", [("analog", 20, 65535)], volume_level=1)
    await command(
        "audio",
        "volume_mute",
        [("digital", 22, True), ("digital", 22, False)],
        is_volume_muted=True,
    )
    await command("audio", "select_source", [("analog", 21, 2)], source="Music")
    await command(
        "audio",
        "turn_on",
        [("digital", 20, True), ("digital", 20, False), ("analog", 21, 2)],
    )
    await command(
        "audio",
        "turn_off",
        [("digital", 21, True), ("digital", 21, False), ("analog", 21, 0)],
    )
    await command(
        "digital_audio",
        "select_source",
        [("digital", 41, True), ("digital", 41, False)],
        source="Music",
    )
    await command(
        "digital_audio",
        "turn_on",
        [
            ("digital", 30, True),
            ("digital", 30, False),
            ("digital", 41, True),
            ("digital", 41, False),
        ],
    )
    await command(
        "digital_audio", "turn_off", [("digital", 31, True), ("digital", 31, False)]
    )
    # Commands have not been looped back by this deliberately dumb peer.
    assert {
        name: hass.states.get(entity_id).state for name, entity_id in ids.items()
    } == expected_states

    writer.write(
        encode_digital(2, True)
        + encode_analog(2, 65535)
        + encode_analog(3, 65535)
        + encode_analog(10, 685)
        + encode_analog(21, 2)
        + encode_digital(40, False)
        + encode_digital(41, True)
    )
    await writer.drain()
    await eventually(
        lambda: hass.states.get(ids["digital_audio"]).attributes["source"] == "Music"
    )
    assert hass.states.get(ids["relay"]).state == "on"
    assert hass.states.get(ids["lamp"]).attributes["brightness"] == 255
    assert hass.states.get(ids["shade"]).attributes["current_position"] == 100
    assert hass.states.get(ids["thermostat"]).attributes["temperature"] == 68.5
    assert hass.states.get(ids["audio"]).attributes["source"] == "Music"

    registry = er.async_get(hass)
    identities = {
        name: (
            registry.async_get(entity_id).id,
            registry.async_get(entity_id).unique_id,
            registry.async_get(entity_id).device_id,
        )
        for name, entity_id in ids.items()
    }
    writer.close()
    await writer.wait_closed()
    await eventually(lambda: not entry.runtime_data.available)
    assert all(
        hass.states.get(entity_id).state == "unavailable" for entity_id in ids.values()
    )
    reader, writer = await connect()
    assert all(
        hass.states.get(entity_id).state == "unknown" for entity_id in ids.values()
    )
    assert hass.states.get(ids["lamp"]).attributes.get("brightness") is None
    assert (
        hass.states.get(ids["thermostat"]).attributes.get("current_temperature") is None
    )
    assert identities == {
        name: (
            registry.async_get(entity_id).id,
            registry.async_get(entity_id).unique_id,
            registry.async_get(entity_id).device_id,
        )
        for name, entity_id in ids.items()
    }
    writer.write(encode_analog(2, 257))
    await writer.drain()
    await eventually(lambda: hass.states.get(ids["lamp"]).state == "on")
    assert hass.states.get(ids["lamp"]).attributes["brightness"] == 1
    assert hass.states.get(ids["relay"]).state == "unknown"


async def test_disconnect_race_becomes_home_assistant_action_error(
    hass, network_platforms
):
    """The entity wrapper turns a real closed-transport send into a HA error."""
    entry, ids, connect = network_platforms
    _, writer = await connect()
    # Closing the server-side writer leaves availability true until its read task
    # processes EOF: the same race can happen after HA checks availability.
    entry.runtime_data._writer.close()
    with pytest.raises(HomeAssistantError, match="disconnected"):
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ids["lamp"]}, blocking=True
        )
    writer.close()
    await writer.wait_closed()


async def test_refresh_omitting_zero_joins_reads_off_not_unknown(
    hass, network_platforms, monkeypatch
):
    """A processor refresh omits low and zero joins; they are off, not unknown."""
    monkeypatch.setattr(xsig, "REFRESH_SETTLE", 0.05)
    entry, ids, connect = network_platforms
    reader, writer = await connect()
    writer.write(encode_digital(1, True))
    await writer.drain()
    await eventually(lambda: hass.states.get(ids["relay"]).state == "off")
    assert hass.states.get(ids["contact"]).state == "on"
    assert hass.states.get(ids["toggle"]).state == "off"
    assert hass.states.get(ids["lamp"]).state == "off"
    assert hass.states.get(ids["text"]).state == "unknown"
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": ids["toggle"]}, blocking=True
    )
    assert await asyncio.wait_for(read_message(reader), 2) == ("digital", 3, True)
    assert await asyncio.wait_for(read_message(reader), 2) == ("digital", 3, False)
