"""A persistent local Sonos stand-in with real Home Assistant playback actions."""

from pathlib import Path

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

TRACKS = (
    ("Morning on the Sound", "Demo Quartet", "Source binding sessions", 240),
    ("Evening at the Dock", "Demo Trio", "Source binding sessions", 180),
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    async_add_entities([SonosDemo(1), SonosDemo(2)])


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
        | MediaPlayerEntityFeature.SEEK
        | MediaPlayerEntityFeature.SHUFFLE_SET
        | MediaPlayerEntityFeature.REPEAT_SET
        | MediaPlayerEntityFeature.CLEAR_PLAYLIST
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(self, number=1):
        self._attr_name = f"Sonos {number}"
        self._attr_unique_id = f"crestron_binding_demo_sonos_{number}"
        self._track = 0
        self._attr_shuffle = False
        self._attr_repeat = "off"
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

    async def async_media_seek(self, position):
        if not 0 <= position <= self._attr_media_duration:
            raise ServiceValidationError("Position is outside this demo track")
        self._attr_media_position = position
        self._attr_media_position_updated_at = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_set_shuffle(self, shuffle):
        self._attr_shuffle = shuffle
        self.async_write_ha_state()

    async def async_set_repeat(self, repeat):
        self._attr_repeat = repeat
        self.async_write_ha_state()

    async def async_clear_playlist(self):
        await self.async_media_stop()

    async def async_play_media(self, media_type, media_id, **kwargs):
        if (media_type, media_id) == ("album", "demo:album"):
            self._track = 0
            await self.async_media_play()
            return
        if media_type != "music" or media_id not in ("demo:track-1", "demo:track-2"):
            raise ServiceValidationError(
                "Choose demo:track-1 or demo:track-2 with type music"
            )
        self._track = int(media_id[-1]) - 1
        await self.async_media_play()

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        art = self.get_browse_image_url("album", "demo:album", "cover")
        album = BrowseMedia(
            media_class="album",
            media_content_type="album",
            media_content_id="demo:album",
            title="Source binding sessions",
            can_play=True,
            can_expand=True,
            thumbnail=art,
        )
        if media_content_id is None or (media_content_type, media_content_id) == (
            "library",
            "",
        ):
            return BrowseMedia(
                media_class="directory",
                media_content_type="library",
                media_content_id="",
                title=f"{self.name} media",
                can_play=False,
                can_expand=True,
                children=[album],
            )
        if (media_content_type, media_content_id) != ("album", "demo:album"):
            raise BrowseError("Demo media folder not found")
        album.children = [
            BrowseMedia(
                media_class="music",
                media_content_type="music",
                media_content_id=f"demo:track-{index}",
                title=track[0],
                can_play=True,
                can_expand=False,
                thumbnail=art,
            )
            for index, track in enumerate(TRACKS, 1)
        ]
        return album

    async def async_get_browse_image(
        self, media_content_type, media_content_id, media_image_id=None
    ):
        if (media_content_type, media_content_id) != ("album", "demo:album"):
            return None, None
        artwork = Path(__file__).resolve().parent.parent / "crestron-demo-album.svg"
        return await self.hass.async_add_executor_job(
            artwork.read_bytes
        ), "image/svg+xml"
