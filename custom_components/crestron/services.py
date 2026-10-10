"""Developer actions explicitly target a single bridge config entry."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .schema import ANALOG, DIGITAL

BASE = {vol.Required("entry_id"): cv.string}
JOIN = {
    **BASE,
    vol.Required("join_type"): vol.In(("digital", "analog", "serial")),
    vol.Required("join"): DIGITAL,
}


@callback
def async_register_services(hass):
    """Resolve runtime at call time so reloads never leave stale service handlers."""

    def bridge_for(call):
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state != ConfigEntryState.LOADED
        ):
            raise ServiceValidationError("Select a loaded Crestron bridge entry")
        return entry.runtime_data

    def checked_join(call):
        try:
            return (DIGITAL if call.data["join_type"] == "digital" else ANALOG)(
                call.data["join"]
            )
        except vol.Invalid as err:
            raise ServiceValidationError("Join is outside the XSIG range") from err

    async def get_join(call):
        bridge = bridge_for(call)
        value = getattr(bridge, f"get_{call.data['join_type']}")(checked_join(call))
        return {"value": value, "available": bridge.available}

    async def set_join(call):
        bridge = bridge_for(call)
        kind = call.data["join_type"]
        join = checked_join(call)
        value = call.data["value"]
        try:
            if kind == "digital":
                value = cv.boolean(value)
            elif kind == "analog":
                if isinstance(value, bool) or not isinstance(value, int):
                    raise ValueError("Analog value must be an integer")
            elif not isinstance(value, str):
                raise ValueError("Serial value must be text")
            await getattr(bridge, f"set_{kind}")(join, value)
        except (vol.Invalid, ValueError, TypeError) as err:
            raise ServiceValidationError("Invalid XSIG value") from err
        except ConnectionError as err:
            raise HomeAssistantError("Crestron bridge is disconnected") from err

    async def pulse_join(call):
        try:
            await bridge_for(call).pulse_digital(
                call.data["join"], call.data["seconds"]
            )
        except ConnectionError as err:
            raise HomeAssistantError("Crestron bridge is disconnected") from err

    async def request_update(call):
        try:
            await bridge_for(call).request_update()
        except ConnectionError as err:
            raise HomeAssistantError("Crestron bridge is disconnected") from err

    hass.services.async_register(
        DOMAIN,
        "get_join",
        get_join,
        schema=vol.Schema(JOIN),
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "set_join",
        set_join,
        schema=vol.Schema({**JOIN, vol.Required("value"): object}),
    )
    hass.services.async_register(
        DOMAIN,
        "pulse_join",
        pulse_join,
        schema=vol.Schema(
            {
                **BASE,
                vol.Required("join"): DIGITAL,
                vol.Optional("seconds", default=0.05): vol.All(
                    vol.Coerce(float), vol.Range(min=0.01, max=5)
                ),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN, "request_update", request_update, schema=vol.Schema(BASE)
    )
