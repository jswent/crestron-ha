"""Audio zones with analog source selectors or digital source pulses."""

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .entity import CrestronEntity, EntityConfig, async_setup_entities

_LOGGER = logging.getLogger(__name__)

# Deliberately exclude target power, source, volume, capabilities and controls.
_MEDIA_ATTRIBUTES = (
    "media_content_id",
    "media_content_type",
    "media_duration",
    "media_position",
    "media_position_updated_at",
    "media_title",
    "media_artist",
    "media_album_name",
    "media_album_artist",
    "media_track",
    "media_series_title",
    "media_season",
    "media_episode",
    "media_channel",
    "media_playlist",
    "app_id",
    "app_name",
)
_MEDIA_STATES = (
    MediaPlayerState.PLAYING,
    MediaPlayerState.PAUSED,
    MediaPlayerState.BUFFERING,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up media player mappings for a bridge."""
    async_setup_entities(entry, async_add_entities, "media_player", CrestronMediaPlayer)


class CrestronMediaPlayer(CrestronEntity, MediaPlayerEntity):
    """An audio zone with 16-bit volume and momentary power controls."""

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.TURN_ON
    )

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        super().__init__(entry, subentry)
        self._power_on_join = self._config["power_on_join"]
        self._power_off_join = self._config["power_off_join"]
        self._mute_join = self._config["mute_join"]
        self._volume_join = self._config["volume_join"]
        self._source_number_join = self._config.get("source_number_join")
        self._source_digital_joins = {
            int(key): name
            for key, name in self._config.get("source_digital_joins", {}).items()
        }
        self._sources = {
            int(key): name
            for key, name in self._config.get(
                "sources", self._source_digital_joins
            ).items()
        }
        self._source_by_name = {name: key for key, name in self._sources.items()}
        default_source = self._config.get(
            "default_source", self._config.get("source_default")
        )
        self._default_source = (
            int(default_source) if default_source is not None else None
        )
        self._active_source_conflict: tuple[int, ...] | None = None
        self._source_bindings = {
            int(key): entity_id
            for key, entity_id in self._config.get("source_bindings", {}).items()
        }
        self._bound_state: MediaPlayerState | None = None
        self._pending_power: bool | None = None
        self._pending_source: int | None = None
        self._source_power_lock = asyncio.Lock()
        self._attr_source_list = list(self._sources.values())
        self._watch("analog", self._volume_join, self._source_number_join)
        self._watch(
            "digital", self._power_on_join, self._mute_join, *self._source_digital_joins
        )

    @property
    def state(self) -> MediaPlayerState | None:
        """Keep power authoritative, enriching an on zone with bound playback."""
        value = self._bridge.get_digital(self._power_on_join)
        if value is None:
            return None
        if not value:
            return MediaPlayerState.OFF
        return self._bound_state or MediaPlayerState.ON

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._source_bindings:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    set(self._source_bindings.values()) - {self.entity_id},
                    self._handle_bound_state,
                )
            )
            self._refresh_bound_media()

    def _binding_entity_id(self) -> str | None:
        """Only unambiguous source feedback may select a metadata provider."""
        if self._source_number_join is not None:
            selector = self._bridge.get_analog(self._source_number_join)
        else:
            active = [
                join
                for join in self._source_digital_joins
                if self._bridge.get_digital(join)
            ]
            selector = active[0] if len(active) == 1 else None
        return self._source_bindings.get(selector)

    @callback
    def _handle_bound_state(self, event: Event[EventStateChangedData]) -> None:
        if event.data["entity_id"] == self._binding_entity_id():
            self._refresh_bound_media()
            self.async_write_ha_state()

    @callback
    def _handle_feedback(self, kind: str, join: int | None, value: Any) -> None:
        if kind not in ("available", "refreshed") and (kind, join) not in self._watched:
            return
        if kind == "available":
            # A new connection establishes a new feedback baseline.
            self._pending_power = self._pending_source = None
        elif kind == "digital" and join == self._power_on_join:
            if value == self._pending_power:
                self._pending_power = None
        if self._pending_source is not None:
            if self._source_number_join is not None:
                confirmed = (
                    kind == "analog"
                    and join == self._source_number_join
                    and value == self._pending_source
                )
            else:
                confirmed = (
                    kind == "digital"
                    and join in self._source_digital_joins
                    and [
                        source
                        for source in self._source_digital_joins
                        if self._bridge.get_digital(source)
                    ]
                    == [self._pending_source]
                )
            if confirmed:
                self._pending_source = None
        if self._source_bindings:
            self._refresh_bound_media()
        self.async_write_ha_state()

    @callback
    def _refresh_bound_media(self) -> None:
        """Replace the entire projection; absent fields never survive an update."""
        self._bound_state = None
        self._attr_media_image_url = None
        for attribute in _MEDIA_ATTRIBUTES:
            setattr(self, f"_attr_{attribute}", None)
        if (
            not self.available
            or self._bridge.get_digital(self._power_on_join) is not True
            or self._pending_power is not None
            or self._pending_source is not None
            or not (entity_id := self._binding_entity_id())
            or entity_id == self.entity_id
        ):
            return
        registered = er.async_get(self.hass).async_get(entity_id)
        if registered is not None and registered.platform == "crestron":
            return
        target = self.hass.states.get(entity_id)
        if target is None:
            return
        if target.state == MediaPlayerState.IDLE:
            self._bound_state = MediaPlayerState.IDLE
        if target.state not in _MEDIA_STATES:
            return
        self._bound_state = MediaPlayerState(target.state)
        for attribute in _MEDIA_ATTRIBUTES:
            value = target.attributes.get(attribute)
            if attribute == "media_position_updated_at":
                if isinstance(value, str):
                    try:
                        value = dt_util.parse_datetime(value)
                    except ValueError:
                        value = None
                elif not isinstance(value, datetime):
                    value = None
            setattr(self, f"_attr_{attribute}", value)
        self._attr_media_image_url = target.attributes.get("entity_picture")

    @property
    def entity_picture(self) -> str | None:
        """Reuse the provider's artwork URL, including HA's authenticated proxy."""
        return self.media_image_url

    @callback
    def _begin_transition(
        self, *, power: bool | None = None, source: int | None = None
    ) -> None:
        """Hide metadata before yielding to any outbound command or late update."""
        if self._source_bindings:
            self._pending_power = power
            self._pending_source = source
            self._refresh_bound_media()
            self.async_write_ha_state()

    @property
    def source(self) -> str | None:
        """Resolve source feedback, reporting conflicts once per combination."""
        if self._source_number_join is not None:
            return self._sources.get(self._bridge.get_analog(self._source_number_join))
        active = [
            (join, name)
            for join, name in self._source_digital_joins.items()
            if self._bridge.get_digital(join)
        ]
        if len(active) > 1:
            conflict = tuple(join for join, _ in active)
            if conflict != self._active_source_conflict:
                _LOGGER.warning(
                    "%s has multiple active source joins %s; using %s",
                    self._config["name"],
                    list(conflict),
                    active[0][1],
                )
            self._active_source_conflict = conflict
        else:
            self._active_source_conflict = None
        return active[0][1] if active else None

    @property
    def volume_level(self) -> float | None:
        """Scale known volume feedback to 0–1."""
        value = self._bridge.get_analog(self._volume_join)
        return value / 65535 if value is not None else None

    @property
    def is_volume_muted(self) -> bool | None:
        """Return mute feedback."""
        return self._bridge.get_digital(self._mute_join)

    async def async_mute_volume(self, mute: bool) -> None:
        """Toggle only if the requested state differs from known feedback."""
        current = self.is_volume_muted
        if current is None:
            raise ServiceValidationError(
                "Mute feedback is unknown; cannot safely toggle mute"
            )
        if current != mute:
            await self._async_command(self._bridge.pulse_digital, self._mute_join)

    async def async_set_volume_level(self, volume: float) -> None:
        """Set the analog volume without changing feedback optimistically."""
        if not isinstance(volume, (int, float)) or not 0 <= volume <= 1:
            raise ServiceValidationError("Volume must be from 0 to 1")
        await self._async_command(
            self._bridge.set_analog, self._volume_join, round(volume * 65535)
        )

    async def async_select_source(self, source: str) -> None:
        """Select an analog source number or pulse its digital command."""
        if source not in self._source_by_name:
            raise ServiceValidationError(f"Unknown source: {source}")
        selector = self._source_by_name[source]
        async with self._source_power_lock:
            self._begin_transition(source=selector)
            await self._async_select_source(selector)
            await self._async_request_bound_feedback()

    async def _async_request_bound_feedback(self) -> None:
        """XSIG may omit unchanged feedback until explicitly asked to refresh."""
        if self._source_bindings and (
            self._pending_power is not None or self._pending_source is not None
        ):
            await self._async_command(self._bridge.request_update)

    async def _async_select_source(self, selector: int) -> None:
        """Send selection within an already established transition."""
        if self._source_number_join is not None:
            await self._async_command(
                self._bridge.set_analog, self._source_number_join, selector
            )
        else:
            await self._async_command(self._bridge.pulse_digital, selector)

    async def async_turn_on(self) -> None:
        """Pulse power and optionally select the configured default source."""
        async with self._source_power_lock:
            self._begin_transition(power=True, source=self._default_source)
            await self._async_command(self._bridge.pulse_digital, self._power_on_join)
            if self._default_source is not None:
                await self._async_select_source(self._default_source)
            await self._async_request_bound_feedback()

    async def async_turn_off(self) -> None:
        """Pulse power and clear the legacy analog source selector."""
        async with self._source_power_lock:
            self._begin_transition(power=False)
            await self._async_command(self._bridge.pulse_digital, self._power_off_join)
            if self._source_number_join is not None:
                await asyncio.sleep(0.05)
                await self._async_command(
                    self._bridge.set_analog, self._source_number_join, 0
                )
