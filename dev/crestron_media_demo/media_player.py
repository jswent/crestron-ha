"""A persistent local Sonos stand-in with real Home Assistant playback actions."""

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.util import dt as dt_util

TRACKS = (
    ("Morning on the Sound", "Demo Quartet", "Source binding sessions", 240),
    ("Evening at the Dock", "Demo Trio", "Source binding sessions", 180),
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([SonosDemo()])


class SonosDemo(MediaPlayerEntity):
    """No network or real Sonos dependency; only loaded by tools/dev.py."""

    _attr_name = "Sonos 1"
    _attr_unique_id = "crestron_binding_demo_sonos_1"
    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_should_poll = False
    _attr_media_image_remotely_accessible = True
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.NEXT_TRACK
        | MediaPlayerEntityFeature.PREVIOUS_TRACK
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(self):
        self._track = 0
        self._attr_state = MediaPlayerState.PLAYING
        self._load_track()

    def _load_track(self):
        (
            self._attr_media_title,
            self._attr_media_artist,
            self._attr_media_album_name,
            self._attr_media_duration,
        ) = TRACKS[self._track]
        self._attr_media_content_id = f"demo:track-{self._track + 1}"
        self._attr_media_content_type = "music"
        self._attr_media_track = self._track + 1
        self._attr_media_position = 0
        self._attr_media_position_updated_at = dt_util.utcnow()
        self._attr_media_image_url = "/local/crestron-demo-album.svg"

    async def async_media_play(self):
        self._load_track()
        self._attr_state = MediaPlayerState.PLAYING
        self.async_write_ha_state()

    async def async_media_pause(self):
        self._attr_state = MediaPlayerState.PAUSED
        self._attr_media_position_updated_at = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_media_stop(self):
        # Deliberately retain the last title to exercise stale-metadata clearing.
        self._attr_state = MediaPlayerState.IDLE
        self.async_write_ha_state()

    async def async_media_next_track(self):
        self._track = (self._track + 1) % len(TRACKS)
        await self.async_media_play()

    async def async_media_previous_track(self):
        self._track = (self._track - 1) % len(TRACKS)
        await self.async_media_play()

    async def async_turn_on(self):
        await self.async_media_play()

    async def async_turn_off(self):
        self._attr_state = MediaPlayerState.OFF
        self.async_write_ha_state()
