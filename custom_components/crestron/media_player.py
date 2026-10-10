"""Audio zones with analog source selectors or digital source pulses."""

import asyncio
import logging

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import CrestronEntity, EntityConfig, async_setup_entities

_LOGGER = logging.getLogger(__name__)


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
        self._attr_source_list = list(self._sources.values())
        self._watch("analog", self._volume_join, self._source_number_join)
        self._watch(
            "digital", self._power_on_join, self._mute_join, *self._source_digital_joins
        )

    @property
    def state(self) -> MediaPlayerState | None:
        """Return on/off only when power feedback has been received."""
        value = self._bridge.get_digital(self._power_on_join)
        if value is None:
            return None
        return MediaPlayerState.ON if value else MediaPlayerState.OFF

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
        if self._source_number_join is not None:
            await self._async_command(
                self._bridge.set_analog, self._source_number_join, selector
            )
        else:
            await self._async_command(self._bridge.pulse_digital, selector)

    async def async_turn_on(self) -> None:
        """Pulse power and optionally select the configured default source."""
        await self._async_command(self._bridge.pulse_digital, self._power_on_join)
        if self._default_source is not None:
            await self.async_select_source(self._sources[self._default_source])

    async def async_turn_off(self) -> None:
        """Pulse power and clear the legacy analog source selector."""
        await self._async_command(self._bridge.pulse_digital, self._power_off_join)
        if self._source_number_join is not None:
            await asyncio.sleep(0.05)
            await self._async_command(
                self._bridge.set_analog, self._source_number_join, 0
            )
