"""UI configuration for independent bridges and their mappings."""

from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigSubentryFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers import selector

from .const import DEFAULT_PORT, DOMAIN
from .inventory import (
    apply_import,
    export_inventory,
    prepare_import,
    validate_device,
    validate_join_mapping,
)
from .schema import (
    NONEMPTY,
    PLATFORM_NAMES,
    entity_form,
    integer,
    listen_host,
    validate_entity,
)


def bridge_form(data=None):
    data = data or {}
    return vol.Schema(
        {
            vol.Required("name", default=data.get("name", "Crestron Bridge")): str,
            vol.Required("host", default=data.get("host", "0.0.0.0")): str,
            vol.Required(
                "port", default=data.get("port", DEFAULT_PORT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=65535, mode=selector.NumberSelectorMode.BOX, step=1
                )
            ),
        }
    )


class CrestronConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create and reconfigure an XSIG listener, without requiring a live processor."""

    VERSION = 1
    MINOR_VERSION = 2

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry):
        return {
            "entity": EntityFlow,
            "device": DeviceFlow,
            "export": ExportFlow,
            "action": ActionFlow,
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return InventoryOptionsFlow()

    async def async_step_user(self, user_input=None):
        return await self._bridge(user_input, "user")

    async def async_step_reconfigure(self, user_input=None):
        return await self._bridge(user_input, "reconfigure")

    async def _bridge(self, user_input, step):
        errors = {}
        entry = self._get_reconfigure_entry() if step == "reconfigure" else None
        if user_input is not None:
            try:
                data = bridge_form()(user_input)
                data["name"] = NONEMPTY(data["name"].strip())
                data["host"] = listen_host(data["host"])
                data["port"] = integer(data["port"])
                # A port identifies a listener reservation, never an entity's identity.
                if any(
                    other.entry_id != (entry.entry_id if entry else None)
                    and other.data["port"] == data["port"]
                    for other in self._async_current_entries()
                ):
                    errors["base"] = "port_in_use"
                elif entry:
                    return self.async_update_and_abort(
                        entry,
                        data=data,
                        title=data["name"],
                        reason="reconfigure_successful",
                    )
                else:
                    return self.async_create_entry(title=data["name"], data=data)
            except (vol.Invalid, ValueError):
                errors["base"] = "invalid_config"
        return self.async_show_form(
            step_id=step,
            data_schema=bridge_form(
                user_input or (dict(entry.data) if entry else None)
            ),
            errors=errors,
        )


class EntityFlow(ConfigSubentryFlow):
    """Each subentry is one mapped entity with persistent identity."""

    _platform: str

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            self._platform = user_input["platform"]
            return await self.async_step_mapping()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("platform"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=list(PLATFORM_NAMES))
                    )
                }
            ),
        )

    async def async_step_mapping(self, user_input=None):
        return await self._mapping(user_input, "mapping")

    async def async_step_reconfigure(self, user_input=None):
        self._platform = self._get_reconfigure_subentry().data["platform"]
        return await self._mapping(user_input, "reconfigure")

    async def _mapping(self, user_input, step):
        errors = {}
        subentry = self._get_reconfigure_subentry() if step == "reconfigure" else None
        if user_input is not None:
            try:
                data = validate_entity(self._platform, user_input)
                if subentry and subentry.data.get("legacy_device"):
                    data["legacy_device"] = True
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_mapping"
            else:
                if subentry:
                    return self.async_update_and_abort(
                        self._get_entry(), subentry, data=data, title=data["name"]
                    )
                return self.async_create_entry(
                    title=data["name"], data=data, unique_id=f"entity:{uuid4().hex}"
                )
        return self.async_show_form(
            step_id=step,
            data_schema=entity_form(
                self._platform, user_input or (subentry.data if subentry else None)
            ),
            errors=errors,
        )


def mapping_form(kind, defaults=None):
    defaults = defaults or {}
    fields: dict[Any, Any] = {
        vol.Required("name", default=defaults.get("name", "")): selector.TextSelector(),
        vol.Required(
            "join_type", default=defaults.get("join_type", "digital")
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(options=["digital", "analog", "serial"])
        ),
        vol.Required("join", default=defaults.get("join", 1)): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=4096, mode=selector.NumberSelectorMode.BOX
            )
        ),
    }
    if kind == "export":
        fields[
            vol.Required("value_template", default=defaults.get("value_template", ""))
        ] = selector.TemplateSelector()
    else:
        fields[vol.Required("actions", default=defaults.get("actions", []))] = (
            selector.ActionSelector()
        )
        fields[
            vol.Optional("ignore_release", default=defaults.get("ignore_release", True))
        ] = selector.BooleanSelector()
    return vol.Schema(fields)


class JoinMappingFlow(ConfigSubentryFlow):
    """Shared flow for outbound templates and inbound actions."""

    kind: str

    async def async_step_user(self, user_input=None):
        return await self._mapping(user_input, "user")

    async def async_step_reconfigure(self, user_input=None):
        return await self._mapping(user_input, "reconfigure")

    async def _mapping(self, user_input, step):
        errors = {}
        subentry = self._get_reconfigure_subentry() if step == "reconfigure" else None
        if user_input is not None:
            try:
                data = validate_join_mapping(self.hass, self.kind, user_input)
                if self.kind == "export" and any(
                    item.subentry_type == "export"
                    and item.subentry_id != (subentry.subentry_id if subentry else None)
                    and item.data["join_type"] == data["join_type"]
                    and item.data["join"] == data["join"]
                    for item in self._get_entry().subentries.values()
                ):
                    raise vol.Invalid("Duplicate outbound join")
            except (vol.Invalid, ValueError, TypeError, TemplateError):
                errors["base"] = "invalid_mapping"
            else:
                if subentry:
                    return self.async_update_and_abort(
                        self._get_entry(), subentry, data=data, title=data["name"]
                    )
                return self.async_create_entry(
                    title=data["name"],
                    data=data,
                    unique_id=f"{self.kind}:{uuid4().hex}",
                )
        return self.async_show_form(
            step_id=step,
            data_schema=mapping_form(
                self.kind, user_input or (subentry.data if subentry else None)
            ),
            errors=errors,
        )


class ExportFlow(JoinMappingFlow):
    """Publish Home Assistant template values to Crestron."""

    kind = "export"


class ActionFlow(JoinMappingFlow):
    """Run Home Assistant actions when a Crestron join arrives."""

    kind = "action"


def device_form(defaults=None):
    fields = {
        vol.Required("name"): selector.TextSelector(),
        vol.Optional("manufacturer"): selector.TextSelector(),
        vol.Optional("model"): selector.TextSelector(),
        vol.Required("entities"): selector.ObjectSelector(),
    }
    return vol.Schema(
        {
            type(key)(str(key), description={"suggested_value": defaults[str(key)]})
            if defaults and str(key) in defaults
            else key: value
            for key, value in fields.items()
        }
    )


class DeviceFlow(ConfigSubentryFlow):
    """A physical device and all its features share one configuration owner."""

    async def async_step_user(self, user_input=None):
        return await self._mapping(user_input, "user")

    async def async_step_reconfigure(self, user_input=None):
        return await self._mapping(user_input, "reconfigure")

    async def _mapping(self, user_input, step):
        previous = self._get_reconfigure_subentry() if step == "reconfigure" else None
        errors = {}
        if user_input is not None:
            try:
                data = validate_device(user_input, previous.data if previous else None)
            except (vol.Invalid, ValueError, TypeError):
                errors["base"] = "invalid_mapping"
            else:
                if previous:
                    return self.async_update_and_abort(
                        self._get_entry(), previous, data=data, title=data["name"]
                    )
                return self.async_create_entry(
                    title=data["name"], data=data, unique_id=f"device:{uuid4().hex}"
                )
        return self.async_show_form(
            step_id=step,
            data_schema=device_form(
                user_input or (previous.data if previous else None)
            ),
            errors=errors,
        )


class InventoryOptionsFlow(OptionsFlow):
    """Add or update hundreds of mappings in one validated operation."""

    async def async_step_init(self, user_input=None):
        errors = {}
        placeholders = {}
        if user_input is not None:
            try:
                plan = prepare_import(
                    self.hass, self.config_entry, user_input["configuration"]
                )
            except (vol.Invalid, ValueError, TypeError, TemplateError) as err:
                errors["base"] = "invalid_inventory"
                placeholders["error"] = str(err)
            else:
                apply_import(self.hass, self.config_entry, plan)
                return self.async_create_entry(
                    title="", data=dict(self.config_entry.options)
                )
        return self.async_show_form(
            step_id="init",
            errors=errors,
            description_placeholders=placeholders,
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "configuration",
                        description={
                            "suggested_value": user_input["configuration"]
                            if user_input
                            else export_inventory(self.config_entry)
                        },
                    ): selector.ObjectSelector()
                }
            ),
        )
