"""Numeric and text sensors backed by XSig feedback."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import CrestronEntity, EntityConfig, async_setup_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensor mappings for a bridge."""
    async_setup_entities(entry, async_add_entities, "sensor", CrestronSensor)


class CrestronSensor(CrestronEntity, SensorEntity):
    """A scaled unsigned analog value or serial string."""

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        super().__init__(entry, subentry)
        self._join = self._config["value_join"]
        self._join_type = self._config.get("join_type", "analog")
        self._divisor = self._config.get("divisor", 1)
        self._attr_device_class = (
            SensorDeviceClass(self._config["device_class"])
            if "device_class" in self._config
            else None
        )
        self._attr_native_unit_of_measurement = self._config.get("unit_of_measurement")
        self._watch(self._join_type, self._join)

    @property
    def native_value(self) -> float | str | None:
        """Convert only known feedback values."""
        if self._join_type == "serial":
            return self._bridge.get_serial(self._join)
        value = self._bridge.get_analog(self._join)
        return value / self._divisor if value is not None else None
