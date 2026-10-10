"""Shared push lifecycle for standalone signals and physical-device features."""

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .inventory import EntityMapping, entity_mappings, standalone_mapping

type EntityConfig = ConfigSubentry | EntityMapping


def async_setup_entities(
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    platform: str,
    entity_class: type[CrestronEntity],
) -> None:
    """Batch features under their real configuration owner, across platforms."""
    groups = {}
    for mapping in entity_mappings(entry):
        if mapping.data["platform"] == platform:
            groups.setdefault(mapping.owner.subentry_id, []).append(
                entity_class(entry, mapping)
            )
    for subentry_id, entities in groups.items():
        async_add_entities(entities, config_subentry_id=subentry_id)


class CrestronEntity(Entity):
    """One feature; a device is optional and represents user-described hardware."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        mapping = (
            standalone_mapping(subentry)
            if isinstance(subentry, ConfigSubentry)
            else subentry
        )
        self._bridge = entry.runtime_data
        self._config = mapping.data
        self._watched: set[tuple[str, int]] = set()
        self._attr_unique_id = f"{entry.entry_id}_{mapping.suffix}"
        self._attr_name = None if mapping.legacy_device else mapping.data["name"]
        self._attr_device_info = None
        if mapping.device is not None:
            info = DeviceInfo(
                identifiers={(DOMAIN, mapping.owner.subentry_id)},
                name=mapping.device["name"],
            )
            # Explicit None clears optional metadata removed during reconfiguration.
            info["manufacturer"] = mapping.device.get("manufacturer")
            info["model"] = mapping.device.get("model")
            if mapping.legacy_device:
                info.update(
                    manufacturer="Crestron",
                    model=f"XSig {mapping.data['platform'].replace('_', ' ')}",
                )
            self._attr_device_info = info

    @property
    def device_info(self) -> DeviceInfo | None:
        if self._attr_device_info is None:
            return None
        return {**self._attr_device_info, "via_device_id": self._bridge.device_id}

    def _watch(self, kind: str, *joins: int | None) -> None:
        self._watched.update((kind, join) for join in joins if join is not None)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._bridge.subscribe(self._handle_feedback, joins=self._watched)
        )

    @callback
    def _handle_feedback(self, kind: str, join: int | None, value: Any) -> None:
        if kind in ("available", "refreshed") or (kind, join) in self._watched:
            self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return self._bridge.available

    async def _async_command(
        self, command: Callable[..., Awaitable[None]], *args: Any, **kwargs: Any
    ) -> None:
        try:
            await command(*args, **kwargs)
        except (ConnectionError, OSError) as err:
            raise HomeAssistantError(
                "Cannot send command: the Crestron processor is disconnected"
            ) from err
