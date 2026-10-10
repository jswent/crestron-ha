"""Thermostats with tenths-of-a-degree analogs and pulsed mode commands."""

from math import isfinite
from typing import Any

from homeassistant.components.climate import (
    FAN_AUTO,
    FAN_ON,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import CrestronEntity, EntityConfig, async_setup_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up climate mappings for a bridge."""
    async_setup_entities(entry, async_add_entities, "climate", CrestronClimate)


class CrestronClimate(CrestronEntity, ClimateEntity):
    """A thermostat whose configured temperature unit describes the wire."""

    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.HEAT, HVACMode.COOL, HVACMode.OFF]
    _attr_fan_modes = [FAN_ON, FAN_AUTO]
    _attr_supported_features = (
        ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_target_temperature_step = 0.1
    _attr_precision = 0.1

    def __init__(self, entry: ConfigEntry, subentry: EntityConfig) -> None:
        super().__init__(entry, subentry)
        self._attr_temperature_unit = (
            UnitOfTemperature.CELSIUS
            if self._config.get("temperature_unit", "F") == "C"
            else UnitOfTemperature.FAHRENHEIT
        )
        self._attr_min_temp = (
            7 if self._attr_temperature_unit == UnitOfTemperature.CELSIUS else 44.6
        )
        self._attr_max_temp = (
            35 if self._attr_temperature_unit == UnitOfTemperature.CELSIUS else 95
        )
        self._mode_joins = {
            HVACMode.HEAT_COOL: self._config["mode_auto_join"],
            HVACMode.HEAT: self._config["mode_heat_join"],
            HVACMode.COOL: self._config["mode_cool_join"],
            HVACMode.OFF: self._config["mode_off_join"],
        }
        self._fan_joins = {
            FAN_AUTO: self._config["fan_auto_join"],
            FAN_ON: self._config["fan_on_join"],
        }
        self._heat_joins = [
            self._config[key] for key in ("h1_join", "h2_join") if key in self._config
        ]
        self._cool_joins = [
            self._config[key] for key in ("c1_join", "c2_join") if key in self._config
        ]
        self._fan_active_join = self._config.get("fa_join")
        self._watch(
            "analog",
            self._config["reg_temp_join"],
            self._config["heat_sp_join"],
            self._config["cool_sp_join"],
        )
        self._watch(
            "digital",
            *self._mode_joins.values(),
            *self._fan_joins.values(),
            *self._heat_joins,
            *self._cool_joins,
            self._fan_active_join,
        )

    def _temperature(self, key: str) -> float | None:
        """Read an optional temperature value in the processor's native unit."""
        value = self._bridge.get_analog(self._config[key])
        return value / 10 if value is not None else None

    @property
    def current_temperature(self) -> float | None:
        """Return room temperature feedback."""
        return self._temperature("reg_temp_join")

    @property
    def target_temperature_high(self) -> float | None:
        """Return the cooling setpoint."""
        return self._temperature("cool_sp_join")

    @property
    def target_temperature_low(self) -> float | None:
        """Return the heating setpoint."""
        return self._temperature("heat_sp_join")

    @property
    def target_temperature(self) -> float | None:
        """Use the active setpoint for single-mode heat and cool controls."""
        if self.hvac_mode == HVACMode.HEAT:
            return self.target_temperature_low
        if self.hvac_mode == HVACMode.COOL:
            return self.target_temperature_high
        return None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Read selected mode, with the legacy auto/heat/cool/off priority."""
        return next(
            (
                mode
                for mode, join in self._mode_joins.items()
                if self._bridge.get_digital(join)
            ),
            None,
        )

    @property
    def fan_mode(self) -> str | None:
        """Return selected fan behavior."""
        return next(
            (
                mode
                for mode, join in self._fan_joins.items()
                if self._bridge.get_digital(join)
            ),
            None,
        )

    @property
    def hvac_action(self) -> HVACAction | None:
        """Report activity only from actual relay feedback."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        heat = [self._bridge.get_digital(join) for join in self._heat_joins]
        cool = [self._bridge.get_digital(join) for join in self._cool_joins]
        if any(heat):
            return HVACAction.HEATING
        if any(cool):
            return HVACAction.COOLING
        fan = (
            self._bridge.get_digital(self._fan_active_join)
            if self._fan_active_join is not None
            else None
        )
        if fan:
            return HVACAction.FAN
        # An absent or incomplete relay mapping cannot establish inactivity.
        if not heat or not cool or any(value is None for value in [*heat, *cool]):
            return None
        if self._fan_active_join is not None and fan is None:
            return None
        return HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Pulse the requested operating-mode command."""
        if hvac_mode not in self._mode_joins:
            raise ServiceValidationError(f"Unsupported HVAC mode: {hvac_mode}")
        await self._async_command(
            self._bridge.pulse_digital, self._mode_joins[hvac_mode]
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Pulse the requested fan-mode command."""
        if fan_mode not in self._fan_joins:
            raise ServiceValidationError(f"Unsupported fan mode: {fan_mode}")
        await self._async_command(self._bridge.pulse_digital, self._fan_joins[fan_mode])

    async def async_turn_on(self) -> None:
        """Enable automatic heating/cooling."""
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    async def async_turn_off(self) -> None:
        """Disable the thermostat."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Validate all setpoints before sending tenths, preserving fractions."""
        values: dict[str, float] = {}
        for attribute, key in (
            ("target_temp_low", "heat_sp_join"),
            ("target_temp_high", "cool_sp_join"),
        ):
            if attribute in kwargs:
                values[key] = kwargs[attribute]
        if ATTR_TEMPERATURE in kwargs:
            mode = kwargs.get("hvac_mode", self.hvac_mode)
            if mode not in (HVACMode.HEAT, HVACMode.COOL):
                raise ServiceValidationError(
                    "Use target_temp_low and target_temp_high in heat/cool mode"
                )
            key = "heat_sp_join" if mode == HVACMode.HEAT else "cool_sp_join"
            values[key] = kwargs[ATTR_TEMPERATURE]
        if not values:
            raise ServiceValidationError("A target temperature is required")
        for value in values.values():
            if (
                not isinstance(value, (int, float))
                or not isfinite(value)
                or not self.min_temp <= value <= self.max_temp
            ):
                raise ServiceValidationError(
                    f"Temperature must be from {self.min_temp} to {self.max_temp} "
                    f"{self.temperature_unit}"
                )
        low = values.get("heat_sp_join", self.target_temperature_low)
        high = values.get("cool_sp_join", self.target_temperature_high)
        if low is not None and high is not None and low > high:
            raise ServiceValidationError(
                "Heating setpoint must not exceed cooling setpoint"
            )
        if "hvac_mode" in kwargs:
            await self.async_set_hvac_mode(kwargs["hvac_mode"])
        for key, value in values.items():
            await self._async_command(
                self._bridge.set_analog, self._config[key], round(value * 10)
            )
