"""Optional media projections through real HA entities, services and events."""

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from tests.test_integration import eventually, setup_bridge, subentry
from tests.test_platforms import MEDIA, FakeBridge
from tools.xsig_simulator import Simulator

SONOS = "media_player.sonos_1"
OTHER = "media_player.sonos_2"
TRACK = {
    "media_title": "First track",
    "media_artist": "Artist",
    "media_album_name": "Album",
    "media_album_artist": "Album artist",
    "media_content_id": "track:1",
    "media_content_type": "music",
    "media_duration": 240,
    "media_position": 42,
    "media_position_updated_at": "2026-10-31T12:00:00+00:00",
    "media_track": 3,
    "media_playlist": "Favorites",
    "media_channel": "Radio",
    "media_series_title": "Series",
    "media_season": "1",
    "media_episode": "2",
    "app_id": "sonos",
    "app_name": "Sonos",
    "entity_picture": "/api/media_player_proxy/media_player.sonos_1?token=demo&cache=1",
}


@pytest.fixture
async def zone(hass, request):
    digital = getattr(request, "param", False)
    config = {**MEDIA, "sources": {"1": "Sonos 1", "2": "Sonos 2", "3": "TV"}}
    if digital:
        config.pop("sources")
        config.pop("source_number_join")
        config["source_digital_joins"] = {"41": "Sonos 1", "42": "Sonos 2", "43": "TV"}
        config["default_source"] = 41
    else:
        config["default_source"] = 1
    config["source_bindings"] = {
        "41" if digital else "1": SONOS,
        "42" if digital else "2": OTHER,
    }
    bridge = FakeBridge()
    bridge.digital = {1: True, 3: False, 41: True, 42: False, 43: False}
    bridge.analog = {4: 32768, 5: 1}
    hass.states.async_set(SONOS, "playing", TRACK)
    hass.states.async_set(OTHER, "paused", {"media_title": "Second track"})
    entry = MockConfigEntry(
        domain="crestron",
        title="Audio bridge",
        version=1,
        minor_version=2,
        data={"name": "Audio bridge", "host": "127.0.0.1", "port": 16384},
        subentries_data=[
            {
                "subentry_id": "zone",
                "subentry_type": "entity",
                "title": "Zone",
                "unique_id": "entity:zone",
                "data": {"platform": "media_player", "name": "Zone", **config},
            }
        ],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.crestron.Bridge", return_value=bridge):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "media_player", "crestron", f"{entry.entry_id}_zone"
    )

    async def service(action, **data):
        await hass.services.async_call(
            "media_player", action, {"entity_id": entity_id, **data}, blocking=True
        )

    def feedback_source(number):
        if digital:
            for source in (41, 42, 43):
                bridge.feedback("digital", source, source == number)
        else:
            bridge.feedback("analog", 5, number)

    yield entry, bridge, entity_id, service, feedback_source
    await hass.config_entries.async_unload(entry.entry_id)


def assert_clear(state, expected="on"):
    assert state.state == expected
    assert not set(TRACK).intersection(state.attributes)


@pytest.mark.parametrize("zone", [False, True], indirect=True)
async def test_projection_replaces_metadata_and_preserves_zone_controls(hass, zone):
    _, bridge, entity_id, service, feedback_source = zone
    state = hass.states.get(entity_id)
    assert state.state == "playing"
    for key, value in TRACK.items():
        actual = state.attributes[key]
        assert (
            actual.isoformat() if key == "media_position_updated_at" else actual
        ) == value
    assert state.attributes["volume_level"] == 32768 / 65535
    assert state.attributes["is_volume_muted"] is False
    assert state.attributes["source"] == "Sonos 1"
    assert not state.attributes["supported_features"] & MediaPlayerEntityFeature.PLAY

    hass.states.async_set(
        SONOS,
        "paused",
        {
            "media_title": "New track",
            "volume_level": 0.9,
            "is_volume_muted": True,
            "source": "Wrong source",
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "paused"
    assert state.attributes["media_title"] == "New track"
    assert "media_artist" not in state.attributes
    assert "entity_picture" not in state.attributes
    assert state.attributes["volume_level"] == 32768 / 65535
    assert state.attributes["source"] == "Sonos 1"
    await service("volume_set", volume_level=0.5)
    bridge.set_analog.assert_awaited_with(4, 32768)
    feedback_source(
        43
        if "source_digital_joins" in next(iter(zone[0].subentries.values())).data
        else 3
    )
    assert_clear(hass.states.get(entity_id))
    hass.states.async_set(SONOS, "playing", TRACK)
    await hass.async_block_till_done()
    assert_clear(hass.states.get(entity_id))


@pytest.mark.parametrize("zone", [False, True], indirect=True)
async def test_source_command_clears_before_io_and_ignores_late_updates(hass, zone):
    entry, bridge, entity_id, service, feedback_source = zone
    started = asyncio.Event()
    finish = asyncio.Event()

    async def delayed_command(*args):
        assert_clear(hass.states.get(entity_id))
        started.set()
        await finish.wait()

    digital = "source_digital_joins" in next(iter(entry.subentries.values())).data
    command = bridge.pulse_digital if digital else bridge.set_analog
    command.side_effect = delayed_command
    task = asyncio.create_task(service("select_source", source="Sonos 2"))
    await started.wait()
    hass.states.async_set(SONOS, "playing", {**TRACK, "media_title": "Late old update"})
    hass.states.async_set(OTHER, "playing", {"media_title": "New selection"})
    # Let state events run while the outbound command is still blocked.
    await asyncio.sleep(0)
    assert_clear(hass.states.get(entity_id))
    assert hass.states.get(entity_id).attributes["source"] == "Sonos 1"
    finish.set()
    await task
    assert_clear(hass.states.get(entity_id))
    feedback_source(42 if digital else 2)
    assert hass.states.get(entity_id).state == "playing"
    assert hass.states.get(entity_id).attributes["media_title"] == "New selection"
    assert "media_album_name" not in hass.states.get(entity_id).attributes


@pytest.mark.parametrize("zone", [False, True], indirect=True)
@pytest.mark.parametrize("power_first", [False, True])
async def test_power_commands_clear_until_both_feedbacks_arrive(
    hass, zone, power_first
):
    entry, bridge, entity_id, service, feedback_source = zone
    digital = "source_digital_joins" in next(iter(entry.subentries.values())).data
    await service("turn_off")
    assert_clear(hass.states.get(entity_id))
    hass.states.async_set(
        SONOS, "playing", {**TRACK, "media_title": "While off pending"}
    )
    bridge.feedback("analog", 4, 10000)
    await hass.async_block_till_done()
    assert_clear(hass.states.get(entity_id))
    bridge.feedback("digital", 1, False)
    assert_clear(hass.states.get(entity_id), "off")
    await service("turn_on")
    assert_clear(hass.states.get(entity_id), "off")
    if power_first:
        bridge.feedback("digital", 1, True)
        assert_clear(hass.states.get(entity_id))
        feedback_source(41 if digital else 1)
    else:
        feedback_source(41 if digital else 1)
        assert_clear(hass.states.get(entity_id), "off")
        bridge.feedback("digital", 1, True)
    assert hass.states.get(entity_id).state == "playing"
    assert hass.states.get(entity_id).attributes["media_title"] == "While off pending"


@pytest.mark.parametrize(
    "target_state", ["idle", "off", "on", "standby", "unknown", "unavailable", None]
)
async def test_inactive_or_missing_target_clears_retained_track(
    hass, zone, target_state
):
    _, _, entity_id, _, _ = zone
    if target_state is None:
        hass.states.async_remove(SONOS)
    else:
        hass.states.async_set(SONOS, target_state, TRACK)
    await hass.async_block_till_done()
    assert_clear(hass.states.get(entity_id), "idle" if target_state == "idle" else "on")
    hass.states.async_set(SONOS, "buffering", TRACK)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "buffering"
    assert hass.states.get(entity_id).attributes["media_title"] == TRACK["media_title"]


async def test_unknown_source_power_and_disconnect_clear(hass, zone):
    _, bridge, entity_id, service, feedback_source = zone
    feedback_source(99)
    assert_clear(hass.states.get(entity_id))
    feedback_source(1)
    bridge.feedback("digital", 1, None)
    assert_clear(hass.states.get(entity_id), "unknown")
    bridge.feedback("digital", 1, True)
    await service("select_source", source="Sonos 2")
    bridge.feedback("available", None, False)
    assert_clear(hass.states.get(entity_id), "unavailable")
    bridge.digital.clear()
    bridge.analog.clear()
    bridge.feedback("available", None, True)
    assert_clear(hass.states.get(entity_id), "unknown")
    feedback_source(1)
    assert_clear(hass.states.get(entity_id), "unknown")
    bridge.feedback("digital", 1, True)
    assert hass.states.get(entity_id).state == "playing"


@pytest.mark.parametrize("zone", [True], indirect=True)
async def test_conflicting_digital_feedback_never_picks_arbitrary_metadata(hass, zone):
    _, bridge, entity_id, service, _ = zone
    bridge.feedback("digital", 42, True)
    assert_clear(hass.states.get(entity_id))
    await service("select_source", source="Sonos 2")
    bridge.feedback("digital", 42, True)
    assert_clear(hass.states.get(entity_id))
    bridge.feedback("digital", 41, False)
    assert hass.states.get(entity_id).state == "paused"
    assert hass.states.get(entity_id).attributes["media_title"] == "Second track"
    bridge.feedback("digital", 42, False)
    assert_clear(hass.states.get(entity_id))


async def test_failed_command_does_not_restore_old_metadata(hass, zone):
    _, bridge, entity_id, service, feedback_source = zone
    bridge.set_analog.side_effect = ConnectionError
    with pytest.raises(HomeAssistantError):
        await service("select_source", source="Sonos 2")
    assert_clear(hass.states.get(entity_id))
    hass.states.async_set(SONOS, "paused", TRACK)
    await hass.async_block_till_done()
    assert_clear(hass.states.get(entity_id))
    feedback_source(2)
    assert hass.states.get(entity_id).state == "paused"


async def test_live_binding_edits_preserve_identity_and_remove_subscriptions(
    hass, zone
):
    entry, bridge, entity_id, _, _ = zone
    registry = er.async_get(hass)
    registry_id = registry.async_get(entity_id).id

    async def configure(bindings):
        mapping = entry.subentries["zone"]
        hass.config_entries.async_update_subentry(
            entry, mapping, data={**mapping.data, "source_bindings": bindings}
        )
        await hass.async_block_till_done()

    await configure({"1": OTHER})
    assert hass.states.get(entity_id).attributes["media_title"] == "Second track"
    assert registry.async_get(entity_id).id == registry_id
    assert bridge.available
    hass.states.async_set(SONOS, "playing", {"media_title": "Old binding update"})
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["media_title"] == "Second track"
    await configure({})
    assert_clear(hass.states.get(entity_id))
    previous = hass.states.get(entity_id)
    hass.states.async_set(OTHER, "playing", TRACK)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is previous
    await configure({"1": SONOS})
    assert hass.states.get(entity_id).attributes["media_title"] == "Old binding update"
    await hass.config_entries.async_unload(entry.entry_id)
    assert not bridge.callbacks
    previous = hass.states.get(entity_id)
    hass.states.async_set(SONOS, "paused", TRACK)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is previous


async def test_self_and_other_crestron_bindings_are_ignored(hass, zone):
    entry, _, entity_id, _, _ = zone
    registry = er.async_get(hass)
    other = registry.async_get_or_create("media_player", "crestron", "another_zone")
    hass.states.async_set(other.entity_id, "playing", TRACK)
    for target in (entity_id, other.entity_id):
        mapping = entry.subentries["zone"]
        hass.config_entries.async_update_subentry(
            entry, mapping, data={**mapping.data, "source_bindings": {"1": target}}
        )
        await hass.async_block_till_done()
        assert_clear(hass.states.get(entity_id))


@pytest.mark.parametrize(
    "timestamp", [None, "invalid", "2026-99-31T12:00:00Z", 123, []]
)
async def test_invalid_position_timestamp_cannot_break_state_updates(
    hass, zone, timestamp
):
    _, _, entity_id, _, _ = zone
    hass.states.async_set(
        SONOS, "playing", {**TRACK, "media_position_updated_at": timestamp}
    )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "playing"
    assert "media_position_updated_at" not in hass.states.get(entity_id).attributes


async def test_overlapping_power_and_source_commands_keep_wire_order(hass, zone):
    _, bridge, entity_id, service, feedback_source = zone
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release(*args):
        assert_clear(hass.states.get(entity_id))
        started.set()
        await release.wait()

    bridge.pulse_digital.side_effect = wait_for_release
    off = asyncio.create_task(service("turn_off"))
    await started.wait()
    select = asyncio.create_task(service("select_source", source="Sonos 2"))
    await asyncio.sleep(0)
    bridge.set_analog.assert_not_awaited()
    release.set()
    await asyncio.gather(off, select)
    assert [call.args for call in bridge.set_analog.await_args_list] == [(5, 0), (5, 2)]
    assert_clear(hass.states.get(entity_id))
    feedback_source(2)
    assert hass.states.get(entity_id).attributes["media_title"] == "Second track"


@pytest.mark.parametrize("digital", [False, True])
async def test_real_xsig_refresh_confirms_unchanged_source_and_power(
    hass, unused_tcp_port, digital
):
    config = {**MEDIA, "default_source": 1, "source_bindings": {"1": SONOS, "2": OTHER}}
    if digital:
        config.pop("source_number_join")
        config.pop("sources")
        config.update(
            source_digital_joins={"41": "Radio", "42": "Music"},
            default_source=41,
            source_bindings={"41": SONOS, "42": OTHER},
        )
    hass.states.async_set(SONOS, "playing", TRACK)
    hass.states.async_set(OTHER, "paused", {"media_title": "Second track"})
    entry = await setup_bridge(
        hass,
        unused_tcp_port,
        mappings=[
            subentry("entity", {"platform": "media_player", "name": "Zone", **config})
        ],
    )
    entity_id = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)[
        0
    ].entity_id
    # This peer never echoes commands. Only a refresh returns its existing
    # non-zero feedback, just as an unchanged signal in a real XSIG program.
    simulator = Simulator(
        {"digital": {"1": True, "41": True}, "analog": {"4": 32768, "5": 1}}, echo=False
    )
    peer = asyncio.create_task(
        simulator.run_session("127.0.0.1", entry.runtime_data.bound_port)
    )
    transitions = []

    def changed(event):
        if event.data["entity_id"] == entity_id and event.data["new_state"]:
            transitions.append(event.data["new_state"].state)

    unsubscribe = hass.bus.async_listen("state_changed", changed)
    try:
        await eventually(lambda: hass.states.get(entity_id).state == "playing")
        for action, data in (("select_source", {"source": "Radio"}), ("turn_on", {})):
            transitions.clear()
            await hass.services.async_call(
                "media_player", action, {"entity_id": entity_id, **data}, blocking=True
            )
            await eventually(lambda: hass.states.get(entity_id).state == "playing")
            await hass.async_block_till_done()
            assert "on" in transitions
            assert transitions[-1] == "playing"
            assert (
                hass.states.get(entity_id).attributes["media_title"]
                == TRACK["media_title"]
            )
        # Refreshing the previous source must NOT confirm a different request.
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": entity_id, "source": "Music"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert_clear(hass.states.get(entity_id))
        if digital:
            await simulator.send_feedback("digital", 41, False)
            await simulator.send_feedback("digital", 42, True)
        else:
            await simulator.send_feedback("analog", 5, 2)
        await eventually(lambda: hass.states.get(entity_id).state == "paused")
        assert hass.states.get(entity_id).attributes["media_title"] == "Second track"
    finally:
        unsubscribe()
        await hass.config_entries.async_unload(entry.entry_id)
        await asyncio.wait_for(peer, 2)
