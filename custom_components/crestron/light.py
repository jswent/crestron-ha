"""Dimmable lights using the complete 16-bit XSig analog range."""

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
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
    """Set up light mappings for a bridge."""
    async_setup_entities(entry, async_add_entities, "light", CrestronLight)


class CrestronLight(CrestronEntity, LightEntity):
    """A brightness-only light with processor-owned state."""

    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_color_mode = ColorMode.BRIGHTNESS

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        super().__init__(entry, subentry)
        self._join = self._config["brightness_join"]
        self._default_brightness = self._config.get("brightness_default", 230)
        self._watch("analog", self._join)

    @property
    def brightness(self) -> int | None:
        """Scale received brightness to Home Assistant's 0–255 range."""
        value = self._bridge.get_analog(self._join)
        return round(value / 257) if value is not None else None

    @property
    def is_on(self) -> bool | None:
        """Preserve unknown feedback and recognize any nonzero analog value."""
        value = self._bridge.get_analog(self._join)
        return value > 0 if value is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Use explicit brightness or the configured turn-on default."""
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._default_brightness)
        if not isinstance(brightness, int) or not 0 <= brightness <= 255:
            raise ServiceValidationError("Brightness must be an integer from 0 to 255")
        await self._async_command(self._bridge.set_analog, self._join, brightness * 257)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Request zero brightness."""
        await self._async_command(self._bridge.set_analog, self._join, 0)
