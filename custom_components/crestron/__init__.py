"""Crestron XSIG: UI-configured, independent local bridges."""

import asyncio
import errno
import logging
import os
from copy import deepcopy

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .bridge import Bridge
from .const import DOMAIN, PLATFORMS
from .inventory import entity_mappings
from .services import async_register_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
type CrestronConfigEntry = ConfigEntry[Bridge]
_LOGGER = logging.getLogger(__name__)


def _bind_error_reason(err: OSError) -> str:
    """Explain the OS failure without exposing addresses in exception text."""
    if err.errno == errno.EADDRNOTAVAIL:
        return (
            "the Listen address is not available on this Home Assistant machine "
            "(EADDRNOTAVAIL). Set Listen address to 0.0.0.0 to listen on all "
            "local IPv4 interfaces; do not enter the Crestron processor's IP"
        )
    if err.errno == errno.EADDRINUSE:
        return (
            "the local address/port is already in use (EADDRINUSE). "
            "Check other listeners or choose another bridge port"
        )
    if err.errno in (errno.EACCES, errno.EPERM):
        return f"the operating system denied permission ({errno.errorcode[err.errno]})"
    if err.errno == errno.EAFNOSUPPORT:
        return "the address family is unsupported (EAFNOSUPPORT); try Listen address 0.0.0.0"
    if err.errno is not None:
        code = errno.errorcode.get(err.errno, str(err.errno))
        return f"{os.strerror(err.errno)} ({code})"
    return "the operating system rejected the listener without an error code"


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Register bridge-scoped actions once for the integration."""
    async_register_services(hass)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: CrestronConfigEntry) -> bool:
    """Keep existing entity/device identifiers while enabling explicit devices."""
    if entry.version != 1:
        return False
    if entry.minor_version < 2:
        for subentry in entry.subentries.values():
            data = dict(subentry.data)
            if subentry.subentry_type == "entity":
                data.setdefault("legacy_device", True)
            hass.config_entries.async_update_subentry(
                entry,
                subentry,
                data=data,
                unique_id=subentry.unique_id
                or f"{subentry.subentry_type}:{subentry.subentry_id}",
            )
        hass.config_entries.async_update_entry(entry, version=1, minor_version=2)
    return True


def _configuration_snapshot(entry):
    """Freeze mutable subentry contents so batched updates can be coalesced."""
    return deepcopy(
        {
            "data": dict(entry.data),
            "title": entry.title,
            "subentries": {
                item.subentry_id: {
                    "type": item.subentry_type,
                    "title": item.title,
                    "unique_id": item.unique_id,
                    "data": dict(item.data),
                }
                for item in entry.subentries.values()
            },
        }
    )


def _mapping_projection(snapshot, kinds):
    """Separate entity configuration from templates/actions with independent work."""
    return {
        identity: mapping
        for identity, mapping in snapshot["subentries"].items()
        if mapping["type"] in kinds
    }


def _register_bridge_device(hass, entry, bridge):
    """Represent the logical listener once, independently of physical devices."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        entry_type=dr.DeviceEntryType.SERVICE,
        manufacturer="Crestron",
        model="XSIG connection",
        name=entry.title,
    )
    bridge.device_id = device.id


def _remove_deleted_device_features(hass, entry):
    """Prune only explicitly removed children of a surviving physical device."""
    desired = {
        f"{entry.entry_id}_{mapping.suffix}" for mapping in entity_mappings(entry)
    }
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        owner = entry.subentries.get(entity.config_subentry_id)
        if (
            owner is not None
            and owner.subentry_type == "device"
            and entity.unique_id not in desired
        ):
            registry.async_remove(entity.entity_id)


async def _async_forward_platforms(hass, entry, bridge):
    """Record successful forwards so failure cleanup never double-unloads HA."""

    async def forward(platform):
        await hass.config_entries.async_forward_entry_setups(entry, (platform,))
        bridge.loaded_platforms.add(platform)

    pending = asyncio.gather(
        *(forward(platform) for platform in PLATFORMS), return_exceptions=True
    )
    try:
        results = await asyncio.shield(pending)
    except asyncio.CancelledError:
        # Finish ownership bookkeeping before cancellation cleanup removes them.
        await pending
        raise
    for result in results:
        if isinstance(result, BaseException):
            raise result


async def _async_unload_platforms(hass, entry, bridge):
    """Unload only platforms still owned by this runtime, including partial setup."""

    async def unload(platform):
        if await hass.config_entries.async_unload_platforms(entry, (platform,)):
            bridge.loaded_platforms.discard(platform)
            return True
        return False

    pending = asyncio.gather(
        *(unload(platform) for platform in tuple(bridge.loaded_platforms))
    )
    try:
        return all(await asyncio.shield(pending))
    except asyncio.CancelledError:
        await pending
        raise


async def _async_cleanup_mappings(hass, entry, bridge):
    """Attempt both cleanup stages, allowing the full reload fallback to proceed."""
    try:
        await _async_unload_platforms(hass, entry, bridge)
    except Exception:
        _LOGGER.warning(
            "Crestron platform cleanup failed for bridge %s", entry.entry_id
        )
    try:
        await bridge.async_stop_mappings()
    except Exception:
        _LOGGER.warning("Crestron mapping cleanup failed for bridge %s", entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: CrestronConfigEntry) -> bool:
    """Start a listener; a disconnected processor is valid, unavailable state."""
    bridge = Bridge(hass, entry)
    bridge.applied_config = _configuration_snapshot(entry)
    bridge.loaded_platforms = set()
    try:
        await bridge.start(entry.data["host"], entry.data["port"])
    except OSError as err:
        await bridge.stop()
        raise ConfigEntryNotReady(
            f"Cannot bind XSIG listener on port {entry.data['port']}: {_bind_error_reason(err)}"
        ) from err
    entry.runtime_data = bridge
    try:
        _register_bridge_device(hass, entry, bridge)
        _remove_deleted_device_features(hass, entry)
        await _async_forward_platforms(hass, entry, bridge)
        bridge.setup_mappings()
    except (Exception, asyncio.CancelledError):
        try:
            await _async_unload_platforms(hass, entry, bridge)
        finally:
            await bridge.stop()
        raise

    async def stop(event):
        async with bridge.configuration_lock:
            await bridge.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    if bridge.applied_config != _configuration_snapshot(entry):
        hass.async_create_task(async_reload_entry(hass, entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CrestronConfigEntry) -> bool:
    """Unload only this bridge; core holds setup_lock before this local lock."""
    bridge = entry.runtime_data
    async with bridge.configuration_lock:
        if await _async_unload_platforms(hass, entry, bridge):
            await bridge.stop()
            return True
    return False


async def async_reload_entry(hass: HomeAssistant, entry: CrestronConfigEntry) -> None:
    """Apply batched mapping edits in place; listener edits need a full reload."""
    bridge = getattr(entry, "runtime_data", None)
    if bridge is None or hass.is_stopping:
        return
    active = getattr(bridge, "refresh_task", None)
    if active is not None and not active.done():
        return
    bridge.refresh_task = asyncio.current_task()
    refresh_started = False
    try:
        # A bulk import mutates every subentry without yielding. One refresh sees
        # its complete final state; subsequent listener tasks share this refresh.
        await asyncio.sleep(0)
        while True:
            reload_listener = False
            async with entry.setup_lock:
                if (
                    hass.is_stopping
                    or entry.state is not ConfigEntryState.LOADED
                    or entry.runtime_data is not bridge
                ):
                    return
                async with bridge.configuration_lock:
                    desired = _configuration_snapshot(entry)
                    if desired == bridge.applied_config:
                        return
                    if bridge.listener_config != (
                        entry.data["host"],
                        entry.data["port"],
                    ):
                        reload_listener = True
                    else:
                        entities_changed = _mapping_projection(
                            desired, {"entity", "device"}
                        ) != _mapping_projection(
                            bridge.applied_config, {"entity", "device"}
                        )
                        mappings_changed = _mapping_projection(
                            desired, {"export", "action"}
                        ) != _mapping_projection(
                            bridge.applied_config, {"export", "action"}
                        )
                        try:
                            refresh_started = entities_changed or mappings_changed
                            if entities_changed and not await _async_unload_platforms(
                                hass, entry, bridge
                            ):
                                reload_listener = True
                            else:
                                if mappings_changed:
                                    await bridge.async_stop_mappings()
                                if entities_changed:
                                    _remove_deleted_device_features(hass, entry)
                                _register_bridge_device(hass, entry, bridge)
                                if entities_changed:
                                    await _async_forward_platforms(hass, entry, bridge)
                                if mappings_changed:
                                    bridge.setup_mappings()
                                bridge.applied_config = desired
                                refresh_started = False
                        except Exception:
                            # Stored mapping failures must not strand partial
                            # platforms or tasks. Full setup reports retry/error.
                            _LOGGER.warning(
                                "Refreshing Crestron mappings failed; reloading bridge %s",
                                entry.entry_id,
                            )
                            await _async_cleanup_mappings(hass, entry, bridge)
                            reload_listener = True
            if reload_listener:
                # Core also takes setup_lock; never call reload under either lock.
                await hass.config_entries.async_reload(entry.entry_id)
                return
            # Config changes made while platform setup yielded require one more
            # pass. The new snapshot prevents losing edits behind the coalescer.
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        if refresh_started:
            async with entry.setup_lock:
                if (
                    entry.state is ConfigEntryState.LOADED
                    and getattr(entry, "runtime_data", None) is bridge
                ):
                    async with bridge.configuration_lock:
                        await _async_cleanup_mappings(hass, entry, bridge)
                        if hass.is_stopping:
                            await bridge.stop()
                        else:
                            hass.config_entries.async_schedule_reload(entry.entry_id)
        raise
    finally:
        bridge.refresh_task = None
