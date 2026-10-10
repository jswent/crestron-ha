"""Maintained and pulsed XSig switches."""

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up switch mappings for a bridge."""
    async_setup_entities(entry, async_add_entities, "switch", CrestronSwitch)


class CrestronSwitch(CrestronEntity, SwitchEntity):
    """Use the same join for feedback and maintained or momentary commands."""

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        super().__init__(entry, subentry)
        self._join = self._config["switch_join"]
        self._pulsed = self._config.get("pulsed", False)
        self._attr_device_class = self._config.get("device_class", "switch")
        self._watch("digital", self._join)

    @property
    def is_on(self) -> bool | None:
        """Return feedback, without assuming the last command succeeded."""
        return self._bridge.get_digital(self._join)

    async def _async_set_state(self, requested: bool) -> None:
        """Only toggle when known feedback is opposite the requested state."""
        if not self._pulsed:
            await self._async_command(self._bridge.set_digital, self._join, requested)
            return
        current = self.is_on
        if current is None:
            raise ServiceValidationError(
                "Switch feedback is unknown; cannot safely toggle the switch"
            )
        if current != requested:
            await self._async_command(self._bridge.pulse_digital, self._join)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Request on without toggling an already-on momentary switch."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Request off without toggling an already-off momentary switch."""
        await self._async_set_state(False)
