"""Mapping identity, optional physical devices and validated bulk configuration."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.template import Template

from .schema import ANALOG, DIGITAL, NONEMPTY, validate_entity

KEY = vol.All(cv.string, vol.Match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z"))


@dataclass(frozen=True)
class EntityMapping:
    """One feature with identity independent of its editable joins and name."""

    owner: ConfigSubentry
    data: Mapping[str, Any]
    suffix: str
    device: Mapping[str, Any] | None = None
    legacy_device: bool = False


def standalone_mapping(subentry: ConfigSubentry) -> EntityMapping:
    legacy = subentry.data.get("legacy_device", False)
    return EntityMapping(
        subentry,
        subentry.data,
        subentry.subentry_id,
        subentry.data if legacy else None,
        legacy,
    )


def entity_mappings(entry):
    """Expand device features without inventing new HA configuration owners."""
    for item in entry.subentries.values():
        if item.subentry_type == "entity":
            yield standalone_mapping(item)
        elif item.subentry_type == "device":
            for data in item.data["entities"]:
                yield EntityMapping(
                    item, data, f"{item.subentry_id}_{data['id']}", item.data
                )


def validate_device(data, previous=None):
    """Validate a complete physical device, keeping each feature's ID immutable."""
    value = vol.Schema(
        {
            vol.Required("name"): NONEMPTY,
            vol.Optional("manufacturer"): NONEMPTY,
            vol.Optional("model"): NONEMPTY,
            vol.Required("entities"): vol.All(list, vol.Length(min=1)),
        }
    )(data)
    old = {
        item["id"]: item["platform"] for item in (previous or {}).get("entities", [])
    }
    entities = []
    seen = set()
    for raw in value["entities"]:
        if not isinstance(raw, dict):
            raise vol.Invalid("Each entity must be an object")
        identity = KEY(raw.get("id"))
        if identity in seen:
            raise vol.Invalid("Duplicate entity id in device")
        seen.add(identity)
        platform = raw.get("platform")
        if identity in old and old[identity] != platform:
            raise vol.Invalid(
                "Keep an entity's platform unchanged; use a new id for a new feature"
            )
        config = validate_entity(
            platform, {k: v for k, v in raw.items() if k not in ("id", "platform")}
        )
        entities.append({"id": identity, **config})
    return {**value, "entities": entities}


def validate_join_mapping(hass, kind, data):
    """Validate JSON input without storing compiled Template or Script objects."""
    fields = {
        vol.Required("name"): NONEMPTY,
        vol.Required("join_type"): vol.In(("digital", "analog", "serial")),
        vol.Required("join"): DIGITAL,
    }
    if kind == "export":
        fields[vol.Required("value_template")] = cv.string
    else:
        fields[vol.Required("actions")] = object
        fields[vol.Optional("ignore_release", default=True)] = cv.boolean
    value = vol.Schema(fields)(deepcopy(data))
    value["join"] = (DIGITAL if value["join_type"] == "digital" else ANALOG)(
        value["join"]
    )
    if kind == "export":
        Template(value["value_template"], hass).ensure_valid()
    else:
        cv.SCRIPT_SCHEMA(deepcopy(value["actions"]))
    return value


@dataclass(frozen=True)
class ImportItem:
    kind: str
    identity: str
    data: dict[str, Any]
    previous: ConfigSubentry | None


def prepare_import(hass, entry, configuration):
    """Validate the entire batch before making any config-entry mutations.

    Top-level keys identify mappings. Imports upsert those keys; omitted mappings
    and omitted device features are preserved. Deletion is explicit in the UI.
    """
    raw = vol.Schema(
        {
            vol.Optional(kind, default=[]): list
            for kind in ("entities", "devices", "exports", "actions")
        }
    )(configuration)
    existing = {
        item.unique_id or f"{item.subentry_type}:{item.subentry_id}": item
        for item in entry.subentries.values()
    }
    planned = []
    seen = set()
    for collection, kind in (
        ("entities", "entity"),
        ("devices", "device"),
        ("exports", "export"),
        ("actions", "action"),
    ):
        for index, item in enumerate(raw[collection]):
            try:
                if not isinstance(item, dict):
                    raise vol.Invalid("Every mapping must be an object with a key")
                identity = f"{kind}:{KEY(item.get('key'))}"
                if identity in seen:
                    raise vol.Invalid("Duplicate mapping key")
                seen.add(identity)
                previous = existing.get(identity)
                data = {k: v for k, v in item.items() if k != "key"}
                if previous is not None and previous.subentry_type != kind:
                    raise vol.Invalid("Mapping key has a different type")
                if kind == "entity":
                    platform = data.pop("platform", None)
                    if previous and previous.data["platform"] != platform:
                        raise vol.Invalid("An entity key cannot change platform")
                    data = validate_entity(platform, data)
                    if previous and previous.data.get("legacy_device"):
                        data["legacy_device"] = True
                elif kind == "device":
                    if previous:
                        children = data.get("entities", [])
                        if not isinstance(children, list):
                            raise vol.Invalid("Device entities must be a list")
                        supplied = {
                            KEY(child.get("id"))
                            for child in children
                            if isinstance(child, dict)
                        }
                        data["entities"] = [
                            *children,
                            *(
                                deepcopy(child)
                                for child in previous.data["entities"]
                                if child["id"] not in supplied
                            ),
                        ]
                    data = validate_device(data, previous.data if previous else None)
                else:
                    data = validate_join_mapping(hass, kind, data)
                planned.append(ImportItem(kind, identity, data, previous))
            except (vol.Invalid, TemplateError) as err:
                raise vol.Invalid(f"{collection}[{index}]: {err}") from err
    export_joins = set()
    replacement_ids = {item.previous.subentry_id for item in planned if item.previous}
    exports = [
        item.data
        for item in entry.subentries.values()
        if item.subentry_type == "export" and item.subentry_id not in replacement_ids
    ]
    exports += [item.data for item in planned if item.kind == "export"]
    for data in exports:
        join = data["join_type"], data["join"]
        if join in export_joins:
            raise vol.Invalid("Each outbound join may have only one export")
        export_joins.add(join)
    return planned


def apply_import(hass, entry, planned):
    """Apply a validated batch without yielding; update listeners coalesce refreshes."""
    for item in planned:
        if item.previous:
            hass.config_entries.async_update_subentry(
                entry,
                item.previous,
                data=item.data,
                title=item.data["name"],
                unique_id=item.identity,
            )
        else:
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(item.data),
                    subentry_type=item.kind,
                    title=item.data["name"],
                    unique_id=item.identity,
                ),
            )


def export_inventory(entry):
    """Export editable values and stable keys, excluding migration-only metadata."""
    result = {"entities": [], "devices": [], "exports": [], "actions": []}
    collections = {
        "entity": "entities",
        "device": "devices",
        "export": "exports",
        "action": "actions",
    }
    for item in entry.subentries.values():
        if item.subentry_type not in collections:
            continue
        key = item.unique_id.split(":", 1)[1] if item.unique_id else item.subentry_id
        data = deepcopy(dict(item.data))
        data.pop("legacy_device", None)
        result[collections[item.subentry_type]].append({"key": key, **data})
    return result
