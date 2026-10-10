"""Binary sensors backed by XSig digital feedback."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import CrestronEntity, EntityConfig, async_setup_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensor mappings for a bridge."""
    async_setup_entities(
        entry, async_add_entities, "binary_sensor", CrestronBinarySensor
    )


class CrestronBinarySensor(CrestronEntity, BinarySensorEntity):
    """A digital input which remains unknown until feedback arrives."""

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        super().__init__(entry, subentry)
        self._join = self._config["is_on_join"]
        self._attr_device_class = self._config.get("device_class")
        self._watch("digital", self._join)

    @property
    def is_on(self) -> bool | None:
        """Return the processor's reported value."""
        return self._bridge.get_digital(self._join)
