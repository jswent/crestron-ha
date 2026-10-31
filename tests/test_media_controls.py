"""Write-through controls through real HA providers, service routing and events."""

import asyncio

import pytest
from homeassistant.components.media_player import DATA_COMPONENT, MediaPlayerEntity
from homeassistant.components.media_player import MediaPlayerEntityFeature as Feature
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from tests import test_media_bindings
from tests.test_integration import eventually
from tests.test_media_bindings import OTHER, SONOS, TRACK

zone = test_media_bindings.zone

BASE = (
    Feature.SELECT_SOURCE
    | Feature.VOLUME_MUTE
    | Feature.VOLUME_SET
    | Feature.TURN_OFF
    | Feature.TURN_ON
)
PLAYBACK = (
    Feature.PLAY
    | Feature.PAUSE
    | Feature.STOP
    | Feature.SEEK
    | Feature.PREVIOUS_TRACK
    | Feature.NEXT_TRACK
    | Feature.CLEAR_PLAYLIST
    | Feature.SHUFFLE_SET
    | Feature.REPEAT_SET
    | Feature.PLAY_MEDIA
    | Feature.MEDIA_ENQUEUE
    | Feature.MEDIA_ANNOUNCE
)


class SourcePlayer(MediaPlayerEntity):
    """A loaded provider that records real service dispatch without optimistic state."""

    _attr_should_poll = False
    _attr_supported_features = (
        PLAYBACK
        | BASE
        | Feature.GROUPING
        | Feature.BROWSE_MEDIA
        | Feature.SEARCH_MEDIA
        | Feature.SELECT_SOUND_MODE
        | Feature.VOLUME_STEP
    )
    _attr_shuffle = False
    _attr_repeat = "off"

    def __init__(self, entity_id, state):
        self.entity_id = entity_id
        self._attr_state = state
        self._attr_media_title = entity_id
        self.calls = []
        self.error = None
        self.started = None
        self.release = None
        self.loop_target = None
        self.report_before_error = False

    async def record(self, action, **data):
        self.calls.append((action, data, self._context))
        if self.started:
            self.started.set()
            await self.release.wait()
        if self.error:
            if self.report_before_error:
                self._attr_media_title = "Queued before failure"
                self.async_write_ha_state()
            raise self.error
        if self.loop_target:
            await self.hass.services.async_call(
                "media_player",
                "media_play",
                {"entity_id": self.loop_target},
                blocking=True,
                context=self._context,
            )

    async def async_media_play(self):
        await self.record("play")

    async def async_media_pause(self):
        await self.record("pause")

    async def async_media_stop(self):
        await self.record("stop")

    async def async_media_next_track(self):
        await self.record("next")

    async def async_media_previous_track(self):
        await self.record("previous")

    async def async_media_seek(self, position):
        await self.record("seek", position=position)

    async def async_set_shuffle(self, shuffle):
        await self.record("shuffle", shuffle=shuffle)

    async def async_set_repeat(self, repeat):
        await self.record("repeat", repeat=repeat)

    async def async_clear_playlist(self):
        await self.record("clear")

    async def async_play_media(self, media_type, media_id, **kwargs):
        await self.record(
            "play_media", media_type=media_type, media_id=media_id, **kwargs
        )


@pytest.fixture
async def players(hass, zone):
    component = hass.data[DATA_COMPONENT]
    hass.states.async_remove(SONOS)
    hass.states.async_remove(OTHER)
    first = SourcePlayer(SONOS, "playing")
    second = SourcePlayer(OTHER, "paused")
    await component.async_add_entities([first, second])
    await hass.async_block_till_done()
    assert component.get_entity(SONOS) is first
    assert component.get_entity(OTHER) is second
    yield first, second
    for entity_id in (SONOS, OTHER):
        if component.get_entity(entity_id):
            await component.async_remove_entity(entity_id)


@pytest.mark.parametrize(
    ("service", "data", "action", "expected"),
    [
        ("media_play", {}, "play", {}),
        ("media_pause", {}, "pause", {}),
        ("media_play_pause", {}, "pause", {}),
        ("media_stop", {}, "stop", {}),
        ("media_next_track", {}, "next", {}),
        ("media_previous_track", {}, "previous", {}),
        ("media_seek", {"seek_position": 12.5}, "seek", {"position": 12.5}),
        ("shuffle_set", {"shuffle": True}, "shuffle", {"shuffle": True}),
        ("repeat_set", {"repeat": "all"}, "repeat", {"repeat": "all"}),
        ("clear_playlist", {}, "clear", {}),
        (
            "play_media",
            {
                "media_content_type": "music",
                "media_content_id": "demo:track",
                "enqueue": "add",
                "extra": {"title": "Demo"},
            },
            "play_media",
            {
                "media_type": "music",
                "media_id": "demo:track",
                "enqueue": "add",
                "extra": {"title": "Demo"},
            },
        ),
        (
            "play_media",
            {
                "media_content_type": "music",
                "media_content_id": "demo:announce",
                "announce": True,
            },
            "play_media",
            {
                "media_type": "music",
                "media_id": "demo:announce",
                "announce": True,
                "extra": {},
            },
        ),
    ],
)
async def test_forwarded_services_use_selected_provider_and_original_context(
    hass, zone, players, service, data, action, expected
):
    _, _, entity_id, _, _ = zone
    first, second = players
    context = Context()
    assert (
        hass.states.get(entity_id).attributes["supported_features"] == BASE | PLAYBACK
    )
    await hass.services.async_call(
        "media_player",
        service,
        {"entity_id": entity_id, **data},
        blocking=True,
        context=context,
    )
    assert first.calls == [(action, expected, context)]
    assert not second.calls
    # A command alone never changes playback or the Crestron zone controls.
    assert hass.states.get(entity_id).state == "playing"
    assert hass.states.get(entity_id).attributes["volume_level"] == 32768 / 65535
    assert hass.states.get(entity_id).attributes["is_volume_muted"] is False


@pytest.mark.parametrize("zone", [False, True], indirect=True)
async def test_controls_follow_selection_and_disappear_during_transition(
    hass, zone, players
):
    entry, bridge, entity_id, service, feedback_source = zone
    first, second = players
    second._attr_supported_features = Feature.PAUSE
    second.async_write_ha_state()
    await hass.async_block_till_done()
    await service("select_source", source="Sonos 2")
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE
    with pytest.raises(ServiceValidationError):
        await service("media_play")
    digital = "source_digital_joins" in entry.subentries["zone"].data
    feedback_source(42 if digital else 2)
    assert (
        hass.states.get(entity_id).attributes["supported_features"]
        == BASE | Feature.PAUSE
    )
    await service("media_pause")
    assert second.calls[0][0] == "pause"
    assert not first.calls
    feedback_source(43 if digital else 3)
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE
    with pytest.raises(ServiceValidationError):
        await hass.data[DATA_COMPONENT].get_entity(entity_id).async_media_pause()
    assert len(second.calls) == 1
    feedback_source(41 if digital else 1)
    await service("turn_off")
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE
    bridge.feedback("digital", 1, False)
    await service("turn_on")
    bridge.feedback("digital", 1, True)
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE
    feedback_source(41 if digital else 1)
    assert (
        hass.states.get(entity_id).attributes["supported_features"] == BASE | PLAYBACK
    )
    bridge.feedback("available", None, False)
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE


@pytest.mark.parametrize(
    "condition",
    [
        "unknown",
        "unavailable",
        "restored",
        "removed",
        "not_loaded",
        "provider_unavailable",
    ],
)
async def test_only_loaded_available_providers_enable_controls(
    hass, zone, players, condition
):
    _, _, entity_id, service, _ = zone
    first, _ = players
    if condition == "not_loaded":
        await hass.data[DATA_COMPONENT].async_remove_entity(SONOS)
        hass.states.async_set(
            SONOS, "playing", {**TRACK, "supported_features": int(PLAYBACK)}
        )
    elif condition == "provider_unavailable":
        first._attr_available = False
        # Even before HA receives the state event the command must be rejected.
    elif condition == "removed":
        hass.states.async_remove(SONOS)
    else:
        hass.states.async_set(
            SONOS,
            "playing" if condition == "restored" else condition,
            {
                **TRACK,
                "supported_features": int(PLAYBACK),
                "restored": condition == "restored",
            },
        )
    await hass.async_block_till_done()
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    assert entity.supported_features == BASE
    with pytest.raises(ServiceValidationError):
        await entity.async_media_play()
    assert not first.calls
    if condition != "not_loaded":
        first._attr_available = True
        first.async_write_ha_state()
        await hass.async_block_till_done()
        assert (
            hass.states.get(entity_id).attributes["supported_features"]
            == BASE | PLAYBACK
        )
        await service("media_play")
        assert len(first.calls) == 1


@pytest.mark.parametrize("state", ["idle", "off", "on", "paused", "buffering"])
async def test_available_players_can_receive_play_even_without_active_track(
    hass, zone, players, state
):
    _, _, entity_id, service, _ = zone
    first, _ = players
    first._attr_state = state
    first.async_write_ha_state()
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id).attributes["supported_features"] == BASE | PLAYBACK
    )
    await service("media_play")
    assert first.calls[0][0] == "play"


async def test_feature_changes_and_shuffle_repeat_feedback_are_live(
    hass, zone, players
):
    _, _, entity_id, service, _ = zone
    first, _ = players
    await service("shuffle_set", shuffle=True)
    await service("repeat_set", repeat="all")
    assert hass.states.get(entity_id).attributes["shuffle"] is False
    assert hass.states.get(entity_id).attributes["repeat"] == "off"
    first._attr_shuffle = True
    first._attr_repeat = "all"
    first.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["shuffle"] is True
    assert hass.states.get(entity_id).attributes["repeat"] == "all"
    first._attr_supported_features = (
        Feature.PLAY | Feature.MEDIA_ENQUEUE | Feature.MEDIA_ANNOUNCE
    )
    first.async_write_ha_state()
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id).attributes["supported_features"]
        == BASE | Feature.PLAY
    )
    assert "shuffle" not in hass.states.get(entity_id).attributes
    assert "repeat" not in hass.states.get(entity_id).attributes
    with pytest.raises(ServiceValidationError):
        await hass.data[DATA_COMPONENT].get_entity(entity_id).async_media_pause()


@pytest.mark.parametrize(
    "error",
    [
        ConnectionError("offline"),
        TimeoutError("timeout"),
        HomeAssistantError("operation failed"),
    ],
)
@pytest.mark.parametrize("unchanged", [False, True])
async def test_connection_failure_disables_controls_until_fresh_provider_report(
    hass, zone, players, error, unchanged
):
    _, _, entity_id, service, _ = zone
    first, _ = players
    first.error = error
    with pytest.raises(HomeAssistantError, match="controls are disabled"):
        await service("media_pause")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE
    with pytest.raises(ServiceValidationError):
        await service("media_pause")
    assert len(first.calls) == 1
    first.error = None
    if not unchanged:
        first._attr_state = "paused"
    first.async_write_ha_state()
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id).attributes["supported_features"] == BASE | PLAYBACK
    )
    await service("media_play")
    assert len(first.calls) == 2


async def test_validation_failure_preserves_connectivity(hass, zone, players):
    _, _, entity_id, service, _ = zone
    first, _ = players
    first.error = ServiceValidationError("Unsupported content")
    with pytest.raises(ServiceValidationError, match="Unsupported content"):
        await service("media_play")
    assert (
        hass.states.get(entity_id).attributes["supported_features"] == BASE | PLAYBACK
    )


async def test_report_queued_before_failure_cannot_reenable_controls(
    hass, zone, players
):
    _, _, entity_id, service, _ = zone
    first, _ = players
    first.error = ConnectionError("failed after report")
    first.report_before_error = True
    with pytest.raises(HomeAssistantError):
        await service("media_play")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE
    first.error = None
    first.async_write_ha_state()
    await hass.async_block_till_done()
    assert (
        hass.states.get(entity_id).attributes["supported_features"] == BASE | PLAYBACK
    )


async def test_late_failure_stays_with_original_target_after_source_switch(
    hass, zone, players
):
    _, _, entity_id, service, feedback_source = zone
    first, second = players
    first.started, first.release = asyncio.Event(), asyncio.Event()
    first.error = ConnectionError("late failure")
    task = asyncio.create_task(service("media_pause"))
    await first.started.wait()
    feedback_source(2)
    await service("media_play")
    first.release.set()
    with pytest.raises(HomeAssistantError):
        await task
    assert first.calls[0][0] == "pause"
    assert second.calls[0][0] == "play"
    assert (
        hass.states.get(entity_id).attributes["supported_features"] == BASE | PLAYBACK
    )
    feedback_source(1)
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE


async def test_play_media_never_accepts_target_override_and_checks_options(
    hass, zone, players
):
    _, _, entity_id, _, _ = zone
    first, second = players
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    await entity.async_play_media(
        "music", "demo:1", entity_id=OTHER, target={"entity_id": OTHER}
    )
    assert first.calls[0][1] == {
        "media_type": "music",
        "media_id": "demo:1",
        "extra": {},
    }
    assert not second.calls
    first._attr_supported_features = Feature.PLAY_MEDIA
    for options in ({"enqueue": "add"}, {"announce": True}):
        with pytest.raises(ServiceValidationError):
            await entity.async_play_media("music", "demo:1", **options)
    assert len(first.calls) == 1


async def test_indirect_control_loop_fails_without_recursing(hass, zone, players):
    _, _, entity_id, service, _ = zone
    first, _ = players
    first.loop_target = entity_id
    with pytest.raises(ServiceValidationError, match="control loop"):
        async with asyncio.timeout(2):
            await service("media_play")
    assert len(first.calls) == 1
    first.loop_target = None
    await service("media_play")
    assert len(first.calls) == 2


@pytest.mark.parametrize("zone", [True], indirect=True)
async def test_digital_source_conflict_disables_forwarding(hass, zone, players):
    _, bridge, entity_id, service, _ = zone
    bridge.feedback("digital", 42, True)
    assert hass.states.get(entity_id).attributes["supported_features"] == BASE
    with pytest.raises(ServiceValidationError):
        await service("media_play")
    assert not any(player.calls for player in players)


async def test_binding_edit_and_unload_remove_control_subscriptions(
    hass, zone, players
):
    entry, _, entity_id, service, _ = zone
    first, second = players
    mapping = entry.subentries["zone"]
    hass.config_entries.async_update_subentry(
        entry, mapping, data={**mapping.data, "source_bindings": {"1": OTHER}}
    )
    await hass.async_block_till_done()
    await service("media_play")
    assert not first.calls
    assert second.calls[0][0] == "play"
    await hass.config_entries.async_unload(entry.entry_id)
    previous = hass.states.get(entity_id)
    for player in players:
        player.async_write_ha_state()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id) is previous


async def test_pending_command_failure_cannot_overwrite_reconfigured_entity(
    hass, zone, players
):
    entry, _, entity_id, service, _ = zone
    first, _ = players
    original = hass.data[DATA_COMPONENT].get_entity(entity_id)
    first.started, first.release = asyncio.Event(), asyncio.Event()
    first.error = ConnectionError("failure after binding edit")
    task = asyncio.create_task(service("media_pause"))
    await first.started.wait()
    try:
        mapping = entry.subentries["zone"]
        hass.config_entries.async_update_subentry(
            entry, mapping, data={**mapping.data, "source_bindings": {"1": OTHER}}
        )
        await eventually(
            lambda: (current := hass.data[DATA_COMPONENT].get_entity(entity_id))
            is not None
            and current is not original
        )
        first.release.set()
        with pytest.raises(HomeAssistantError):
            await task
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["media_title"] == OTHER
        assert (
            hass.states.get(entity_id).attributes["supported_features"]
            == BASE | PLAYBACK
        )
        with pytest.raises(ServiceValidationError):
            await original.async_media_play()
    finally:
        first.release.set()
        await asyncio.gather(task, return_exceptions=True)
