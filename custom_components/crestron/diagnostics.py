"""Diagnostics omit network addresses, templates, actions and join values."""

from collections import Counter

from .inventory import entity_mappings


async def async_get_config_entry_diagnostics(hass, entry):
    """Return transport health and mapping counts, never user payloads."""
    runtime = getattr(entry, "runtime_data", None)
    return {
        "version": entry.version,
        "mapping_counts": dict(
            Counter(item.subentry_type for item in entry.subentries.values())
        ),
        "entity_counts": dict(
            Counter(item.data["platform"] for item in entity_mappings(entry))
        ),
        "transport": runtime.snapshot()
        if runtime is not None
        else {"runtime_initialized": False},
    }
