"""Validation and UI schemas for user-defined join mappings."""

from ipaddress import ip_address
from math import isfinite

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector

PLATFORM_NAMES = (
    "binary_sensor",
    "sensor",
    "switch",
    "light",
    "cover",
    "climate",
    "media_player",
)


def integer(value):
    """Accept UI numeric whole values without truncating invalid joins."""
    if isinstance(value, bool):
        raise vol.Invalid("Expected an integer")
    try:
        parsed = int(value)
        if float(value) != parsed:
            raise ValueError
        return parsed
    except (ValueError, TypeError, OverflowError) as err:
        raise vol.Invalid("Expected an integer") from err


DIGITAL = vol.All(integer, vol.Range(min=1, max=4096))
ANALOG = vol.All(integer, vol.Range(min=1, max=1024))
NONEMPTY = vol.All(cv.string, vol.Strip, vol.Length(min=1, max=100))


def listen_host(value):
    """Only literal local bind addresses; no blocking DNS in a flow."""
    return str(ip_address(value))


def entity_fields(platform):
    """Keep legacy join names while using current UI-driven configuration."""
    fields = {vol.Required("name"): NONEMPTY}
    if platform == "binary_sensor":
        fields |= {
            vol.Required("is_on_join"): DIGITAL,
            vol.Optional("device_class"): cv.string,
        }
    elif platform == "sensor":
        fields |= {
            vol.Required("value_join"): ANALOG,
            vol.Optional("join_type", default="analog"): vol.In(("analog", "serial")),
            vol.Optional("divisor", default=1): vol.All(
                vol.Coerce(float), vol.Range(min=0.000001)
            ),
            vol.Optional("device_class"): cv.string,
            vol.Optional("unit_of_measurement"): cv.string,
        }
    elif platform == "switch":
        fields |= {
            vol.Required("switch_join"): DIGITAL,
            vol.Optional("pulsed", default=False): cv.boolean,
            vol.Optional("device_class"): vol.In(("switch", "outlet")),
        }
    elif platform == "light":
        fields |= {
            vol.Required("brightness_join"): ANALOG,
            vol.Optional("brightness_default", default=230): vol.All(
                integer, vol.Range(min=1, max=255)
            ),
        }
    elif platform == "cover":
        fields |= {
            vol.Required("pos_join"): ANALOG,
            vol.Required("stop_join"): DIGITAL,
            vol.Optional("is_opening_join"): DIGITAL,
            vol.Optional("is_closing_join"): DIGITAL,
            vol.Optional("is_closed_join"): DIGITAL,
            vol.Optional("type", default="shade"): vol.In(
                (
                    "shade",
                    "blind",
                    "curtain",
                    "shutter",
                    "window",
                    "door",
                    "garage",
                    "gate",
                    "awning",
                    "damper",
                )
            ),
        }
    elif platform == "climate":
        fields |= {
            vol.Required(key): ANALOG
            for key in ("heat_sp_join", "cool_sp_join", "reg_temp_join")
        }
        fields |= {
            vol.Required(key): DIGITAL
            for key in (
                "mode_heat_join",
                "mode_cool_join",
                "mode_auto_join",
                "mode_off_join",
                "fan_on_join",
                "fan_auto_join",
            )
        }
        fields |= {
            vol.Optional(key): DIGITAL
            for key in ("h1_join", "h2_join", "c1_join", "c2_join", "fa_join")
        }
        fields[vol.Optional("temperature_unit", default="F")] = vol.In(("F", "C"))
    elif platform == "media_player":
        fields |= {
            vol.Required(key): DIGITAL
            for key in ("power_on_join", "power_off_join", "mute_join")
        }
        fields |= {
            vol.Required("volume_join"): ANALOG,
            vol.Optional("source_number_join"): ANALOG,
            vol.Optional("sources"): dict,
            vol.Optional("source_digital_joins"): dict,
            vol.Optional("source_bindings"): dict,
            vol.Optional("default_source"): vol.All(
                integer, vol.Range(min=1, max=65535)
            ),
        }
    else:
        raise vol.Invalid("Unsupported platform")
    return fields


def validate_entity(platform, data):
    """Validate joins and related fields, and normalize JSON map keys."""
    result = vol.Schema(entity_fields(platform))(data)
    if platform == "sensor" and not isfinite(result["divisor"]):
        raise vol.Invalid("Divisor must be finite")
    if platform in ("sensor", "binary_sensor") and "device_class" in result:
        if platform == "sensor":
            from homeassistant.components.sensor import SensorDeviceClass
            from homeassistant.components.sensor.const import (
                DEVICE_CLASS_UNITS,
                NON_NUMERIC_DEVICE_CLASSES,
            )

            classes = SensorDeviceClass
        else:
            from homeassistant.components.binary_sensor import BinarySensorDeviceClass

            classes = BinarySensorDeviceClass
        if result["device_class"] not in {item.value for item in classes}:
            raise vol.Invalid("Unknown device class")
        if platform == "sensor":
            device_class = SensorDeviceClass(result["device_class"])
            if device_class in NON_NUMERIC_DEVICE_CLASSES:
                raise vol.Invalid("Analog sensors require a numeric device class")
            unit = result.get("unit_of_measurement")
            if device_class == SensorDeviceClass.MONETARY:
                cv.currency(unit)
            elif (
                units := DEVICE_CLASS_UNITS.get(device_class)
            ) is not None and unit not in units:
                raise vol.Invalid("Unit of measurement does not match device class")
    if (
        platform == "sensor"
        and result["join_type"] == "serial"
        and ("device_class" in result or "unit_of_measurement" in result)
    ):
        raise vol.Invalid("Serial sensors cannot have numeric units or device classes")
    if platform == "media_player":
        digital = "source_digital_joins" in result
        if digital and ("sources" in result or "source_number_join" in result):
            raise vol.Invalid("Choose analog or digital sources, not both")
        if not digital and not ("sources" in result and "source_number_join" in result):
            raise vol.Invalid(
                "Provide an analog source join and sources, or digital sources"
            )
        key = "source_digital_joins" if digital else "sources"
        source_map = result[key]
        maximum = 4096 if digital else 65535
        normalized = {}
        for number, name in source_map.items():
            number = vol.All(integer, vol.Range(min=1, max=maximum))(number)
            if str(number) in normalized:
                raise vol.Invalid("Duplicate source number")
            normalized[str(number)] = NONEMPTY(name)
        if not normalized or len(set(normalized.values())) != len(normalized):
            raise vol.Invalid("Source names must be unique and nonempty")
        result[key] = normalized
        if "source_bindings" in result:
            bindings = {}
            for number, entity_id in result["source_bindings"].items():
                number = str(integer(number))
                if number not in normalized:
                    raise vol.Invalid("Bound source must be configured")
                if number in bindings:
                    raise vol.Invalid("Duplicate source binding")
                bindings[number] = vol.All(
                    cv.entity_id, cv.entity_domain("media_player")
                )(entity_id)
            result["source_bindings"] = bindings
        if (
            "default_source" in result
            and str(result["default_source"]) not in normalized
        ):
            raise vol.Invalid("Default source must be configured")
    return {"platform": platform, **result}


def entity_form(platform, defaults=None):
    """Build native form widgets for the selected entity platform."""
    fields = {}
    for key, validator in entity_fields(platform).items():
        name = str(key)
        if defaults and name in defaults:
            key = type(key)(name, description={"suggested_value": defaults[name]})
        if name in ("sources", "source_digital_joins", "source_bindings"):
            widget = selector.ObjectSelector()
        elif name == "pulsed":
            widget = selector.BooleanSelector()
        elif name == "join_type":
            widget = selector.SelectSelector(
                selector.SelectSelectorConfig(options=["analog", "serial"])
            )
        elif name == "temperature_unit":
            widget = selector.SelectSelector(
                selector.SelectSelectorConfig(options=["F", "C"])
            )
        elif name == "type":
            widget = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        "shade",
                        "blind",
                        "curtain",
                        "shutter",
                        "window",
                        "door",
                        "garage",
                        "gate",
                        "awning",
                        "damper",
                    ]
                )
            )
        elif name.endswith("_join") or name in (
            "brightness_default",
            "default_source",
            "divisor",
        ):
            maximum = {
                "default_source": 65535,
                "brightness_default": 255,
                "divisor": 1000000,
            }.get(name, 4096 if validator is DIGITAL else 1024)
            widget = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.000001 if name == "divisor" else 1,
                    max=maximum,
                    mode=selector.NumberSelectorMode.BOX,
                    step="any" if name == "divisor" else 1,
                )
            )
        else:
            widget = selector.TextSelector()
        fields[key] = widget
    return vol.Schema(fields)
