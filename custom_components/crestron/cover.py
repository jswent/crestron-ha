"""Covers with position, travel feedback, and a momentary stop join."""

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import CrestronEntity, EntityConfig, async_setup_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up cover mappings for a bridge."""
    async_setup_entities(entry, async_add_entities, "cover", CrestronCover)


class CrestronCover(CrestronEntity, CoverEntity):
    """A cover with 0 closed and 65535 fully open."""

    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
    )

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        super().__init__(entry, subentry)
        self._attr_device_class = self._config.get("type", CoverDeviceClass.SHADE)
        self._pos_join = self._config["pos_join"]
        self._opening_join = self._config.get("is_opening_join")
        self._closing_join = self._config.get("is_closing_join")
        self._closed_join = self._config.get("is_closed_join")
        self._stop_join = self._config["stop_join"]
        self._watch("analog", self._pos_join)
        self._watch(
            "digital", self._opening_join, self._closing_join, self._closed_join
        )

    @property
    def current_cover_position(self) -> int | None:
        """Return a whole percentage without truncating the maximum position."""
        value = self._bridge.get_analog(self._pos_join)
        return round(value * 100 / 65535) if value is not None else None

    @property
    def is_opening(self) -> bool | None:
        """Return opening feedback when mapped."""
        if self._opening_join is None:
            return None
        return self._bridge.get_digital(self._opening_join)

    @property
    def is_closing(self) -> bool | None:
        """Return closing feedback when mapped."""
        if self._closing_join is None:
            return None
        return self._bridge.get_digital(self._closing_join)

    @property
    def is_closed(self) -> bool | None:
        """Prefer explicit closed feedback, otherwise use known position."""
        if self._closed_join is not None:
            return self._bridge.get_digital(self._closed_join)
        position = self._bridge.get_analog(self._pos_join)
        return position == 0 if position is not None else None

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Convert the complete percentage range to XSig."""
        position = kwargs[ATTR_POSITION]
        if not isinstance(position, (int, float)) or not 0 <= position <= 100:
            raise ServiceValidationError("Cover position must be from 0 to 100")
        await self._async_command(
            self._bridge.set_analog, self._pos_join, round(position * 65535 / 100)
        )

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Request fully open."""
        await self._async_command(self._bridge.set_analog, self._pos_join, 65535)

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Request fully closed."""
        await self._async_command(self._bridge.set_analog, self._pos_join, 0)

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Release the momentary stop command after 200 milliseconds."""
        await self._async_command(
            self._bridge.pulse_digital, self._stop_join, seconds=0.2
        )
