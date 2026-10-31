"""Exercise platform behavior and real Home Assistant entity registration."""

import asyncio
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from homeassistant.components.climate import FAN_AUTO, HVACAction, HVACMode
from homeassistant.components.media_player import MediaPlayerState
from homeassistant.config_entries import ConfigSubentry
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron.binary_sensor import CrestronBinarySensor
from custom_components.crestron.climate import CrestronClimate
from custom_components.crestron.cover import CrestronCover
from custom_components.crestron.light import CrestronLight
from custom_components.crestron.media_player import CrestronMediaPlayer
from custom_components.crestron.sensor import CrestronSensor
from custom_components.crestron.switch import CrestronSwitch

CLIMATE = {
    "heat_sp_join": 1,
    "cool_sp_join": 2,
    "reg_temp_join": 3,
    "mode_heat_join": 4,
    "mode_cool_join": 5,
    "mode_auto_join": 6,
    "mode_off_join": 7,
    "fan_on_join": 8,
    "fan_auto_join": 9,
    "h1_join": 10,
    "h2_join": 11,
    "c1_join": 12,
    "c2_join": 13,
    "fa_join": 14,
}
MEDIA = {
    "power_on_join": 1,
    "power_off_join": 2,
    "mute_join": 3,
    "volume_join": 4,
    "source_number_join": 5,
    "sources": {"1": "Radio", "2": "Music"},
    "default_source": 2,
}


class FakeBridge:
    """Only substitute hardware; entities use Home Assistant's actual classes."""

    def __init__(self):
        self.available = True
        self.digital = {}
        self.analog = {}
        self.serial = {}
        self.callbacks = set()
        self.set_digital = AsyncMock()
        self.set_analog = AsyncMock()
        self.set_serial = AsyncMock()
        self.pulse_digital = AsyncMock()
        self.request_update = AsyncMock()
        self.configuration_lock = asyncio.Lock()
        self.listener_config = None
        self.start = AsyncMock(side_effect=self._start_listener)
        self.stop = AsyncMock()
        self.setup_mappings = Mock()
        self.async_stop_mappings = AsyncMock()

    async def _start_listener(self, host, port):
        self.listener_config = (host, port)

    def get_digital(self, join):
        return self.digital.get(join)

    def get_analog(self, join):
        return self.analog.get(join)

    def get_serial(self, join):
        return self.serial.get(join)

    def subscribe(self, callback, *, joins=None):
        self.callbacks.add(callback)
        return lambda: self.callbacks.discard(callback)

    def feedback(self, kind, join, value):
        if kind == "available":
            self.available = value
        else:
            getattr(self, kind)[join] = value
        for callback in self.callbacks.copy():
            callback(kind, join, value)


@pytest.fixture
def bridge():
    return FakeBridge()


def make_entity(bridge, entity_class, platform, config):
    entry = MockConfigEntry(domain="crestron", entry_id="bridge-one")
    entry.runtime_data = bridge
    subentry = ConfigSubentry(
        data=MappingProxyType({"platform": platform, "name": "Test device", **config}),
        subentry_id="mapping-one",
        subentry_type="entity",
        title="Test device",
        unique_id=None,
    )
    return entity_class(entry, subentry)


@pytest.mark.parametrize(
    ("entity_class", "platform", "config", "attribute"),
    [
        (CrestronBinarySensor, "binary_sensor", {"is_on_join": 1}, "is_on"),
        (CrestronSensor, "sensor", {"value_join": 1}, "native_value"),
        (CrestronSwitch, "switch", {"switch_join": 1}, "is_on"),
        (CrestronLight, "light", {"brightness_join": 1}, "brightness"),
        (
            CrestronCover,
            "cover",
            {"pos_join": 1, "stop_join": 2},
            "current_cover_position",
        ),
        (CrestronClimate, "climate", CLIMATE, "current_temperature"),
        (CrestronMediaPlayer, "media_player", MEDIA, "state"),
    ],
)
def test_missing_feedback_and_identity(
    bridge, entity_class, platform, config, attribute
):
    """An absent reply is unknown, and the identity survives display-name changes."""
    entity = make_entity(bridge, entity_class, platform, config)
    assert getattr(entity, attribute) is None
    assert entity.unique_id == "bridge-one_mapping-one"
    assert entity.available
    assert not entity.should_poll
    bridge.available = False
    assert not entity.available


def test_sensor_scaling_and_serial(bridge):
    sensor = make_entity(
        bridge, CrestronSensor, "sensor", {"value_join": 1, "divisor": 10}
    )
    bridge.analog[1] = 725
    assert sensor.native_value == 72.5
    text = make_entity(
        bridge, CrestronSensor, "sensor", {"value_join": 1, "join_type": "serial"}
    )
    assert text.native_value is None
    bridge.serial[1] = "Crestron ready"
    assert text.native_value == "Crestron ready"


@pytest.mark.parametrize("pulsed", [False, True])
async def test_switch_commands_use_feedback(bridge, pulsed):
    entity = make_entity(
        bridge, CrestronSwitch, "switch", {"switch_join": 1, "pulsed": pulsed}
    )
    bridge.digital[1] = False
    await entity.async_turn_on()
    assert entity.is_on is False
    bridge.digital[1] = True
    await entity.async_turn_off()
    if pulsed:
        assert bridge.pulse_digital.await_args_list == [call(1), call(1)]
    else:
        assert bridge.set_digital.await_args_list == [call(1, True), call(1, False)]


async def test_light_full_range_and_default(bridge):
    entity = make_entity(bridge, CrestronLight, "light", {"brightness_join": 1})
    assert entity.is_on is None
    await entity.async_turn_on(brightness=255)
    bridge.set_analog.assert_awaited_with(1, 65535)
    assert entity.brightness is None
    bridge.analog[1] = 65535
    assert entity.brightness == 255
    assert entity.is_on
    await entity.async_turn_on()
    bridge.set_analog.assert_awaited_with(1, 230 * 257)
    await entity.async_turn_off()
    bridge.set_analog.assert_awaited_with(1, 0)
    with pytest.raises(ServiceValidationError):
        await entity.async_turn_on(brightness=256)


async def test_cover_full_scale_stop_and_unknown_movement(bridge):
    entity = make_entity(
        bridge, CrestronCover, "cover", {"pos_join": 1, "stop_join": 2}
    )
    assert entity.is_closed is None
    assert entity.is_opening is None
    assert entity.is_closing is None
    await entity.async_set_cover_position(position=100)
    bridge.set_analog.assert_awaited_with(1, 65535)
    await entity.async_set_cover_position(position=50)
    bridge.set_analog.assert_awaited_with(1, 32768)
    bridge.analog[1] = 65535
    assert entity.current_cover_position == 100
    assert entity.is_closed is False
    await entity.async_close_cover()
    bridge.set_analog.assert_awaited_with(1, 0)
    await entity.async_open_cover()
    bridge.set_analog.assert_awaited_with(1, 65535)
    await entity.async_stop_cover()
    bridge.pulse_digital.assert_awaited_with(2, seconds=0.2)
    with pytest.raises(ServiceValidationError):
        await entity.async_set_cover_position(position=101)


def test_cover_explicit_feedback_takes_precedence(bridge):
    entity = make_entity(
        bridge,
        CrestronCover,
        "cover",
        {
            "pos_join": 1,
            "stop_join": 2,
            "is_closed_join": 3,
            "is_opening_join": 4,
            "is_closing_join": 5,
        },
    )
    bridge.analog[1] = 0
    assert entity.is_closed is None
    bridge.digital.update({3: False, 4: True, 5: False})
    assert entity.is_closed is False
    assert entity.is_opening is True
    assert entity.is_closing is False


async def test_climate_fractional_temperatures_and_commands(bridge):
    entity = make_entity(bridge, CrestronClimate, "climate", CLIMATE)
    assert entity.hvac_mode is None
    assert entity.hvac_action is None
    bridge.analog.update({1: 681, 2: 755, 3: 712})
    bridge.digital[4] = True
    assert entity.current_temperature == 71.2
    assert entity.target_temperature == 68.1
    assert entity.target_temperature_low == 68.1
    assert entity.target_temperature_high == 75.5
    await entity.async_set_temperature(temperature=68.5)
    bridge.set_analog.assert_awaited_with(1, 685)
    await entity.async_set_temperature(target_temp_low=68.2, target_temp_high=75.3)
    assert bridge.set_analog.await_args_list[-2:] == [call(1, 682), call(2, 753)]
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    bridge.pulse_digital.assert_awaited_with(6)
    await entity.async_set_fan_mode(FAN_AUTO)
    bridge.pulse_digital.assert_awaited_with(9)
    await entity.async_turn_off()
    bridge.pulse_digital.assert_awaited_with(7)
    await entity.async_turn_on()
    bridge.pulse_digital.assert_awaited_with(6)
    with pytest.raises(ServiceValidationError):
        await entity.async_set_hvac_mode(HVACMode.DRY)
    with pytest.raises(ServiceValidationError):
        await entity.async_set_fan_mode("high")


@pytest.mark.parametrize(
    "values",
    [
        {"target_temp_low": 80, "target_temp_high": 70},
        {"target_temp_low": float("nan")},
        {"target_temp_high": 100},
        {},
        {"temperature": 70},
    ],
)
async def test_climate_rejects_invalid_request_before_sending(bridge, values):
    entity = make_entity(bridge, CrestronClimate, "climate", CLIMATE)
    with pytest.raises(ServiceValidationError):
        await entity.async_set_temperature(**values)
    bridge.set_analog.assert_not_awaited()
    bridge.pulse_digital.assert_not_awaited()


def test_climate_relay_actions_and_celsius(bridge):
    entity = make_entity(
        bridge, CrestronClimate, "climate", {**CLIMATE, "temperature_unit": "C"}
    )
    assert entity.temperature_unit == "°C"
    assert entity.min_temp == 7
    bridge.digital.update({10: False, 11: False, 12: False, 13: False, 14: False})
    assert entity.hvac_action == HVACAction.IDLE
    bridge.digital[14] = True
    assert entity.hvac_action == HVACAction.FAN
    bridge.digital[12] = True
    assert entity.hvac_action == HVACAction.COOLING
    bridge.digital[10] = True
    assert entity.hvac_action == HVACAction.HEATING
    bridge.digital[7] = True
    assert entity.hvac_action == HVACAction.OFF


async def test_media_analog_source_default_volume_and_power(bridge):
    entity = make_entity(bridge, CrestronMediaPlayer, "media_player", MEDIA)
    assert entity.volume_level is None
    assert entity.is_volume_muted is None
    assert entity.source is None
    assert entity.source_list == ["Radio", "Music"]
    bridge.digital[1] = True
    bridge.analog.update({4: 65535, 5: 2})
    assert entity.state == MediaPlayerState.ON
    assert entity.volume_level == 1
    assert entity.source == "Music"
    await entity.async_turn_on()
    bridge.pulse_digital.assert_awaited_with(1)
    bridge.set_analog.assert_awaited_with(5, 2)
    await entity.async_select_source("Radio")
    bridge.set_analog.assert_awaited_with(5, 1)
    await entity.async_set_volume_level(0.5)
    bridge.set_analog.assert_awaited_with(4, 32768)
    await entity.async_turn_off()
    bridge.pulse_digital.assert_awaited_with(2)
    bridge.set_analog.assert_awaited_with(5, 0)
    assert entity.state == MediaPlayerState.ON  # Still awaiting real feedback.
    with pytest.raises(ServiceValidationError):
        await entity.async_select_source("Missing")
    with pytest.raises(ServiceValidationError):
        await entity.async_set_volume_level(1.1)


async def test_media_mute_is_idempotent(bridge):
    entity = make_entity(bridge, CrestronMediaPlayer, "media_player", MEDIA)
    with pytest.raises(ServiceValidationError, match="feedback is unknown"):
        await entity.async_mute_volume(True)
    bridge.digital[3] = True
    await entity.async_mute_volume(True)
    bridge.pulse_digital.assert_not_awaited()
    await entity.async_mute_volume(False)
    bridge.pulse_digital.assert_awaited_once_with(3)


async def test_media_digital_source_conflicts_and_default(bridge, caplog):
    config = {
        key: value
        for key, value in MEDIA.items()
        if key not in ("sources", "source_number_join", "default_source")
    }
    config.update(
        source_digital_joins={"21": "Radio", "22": "Music"}, default_source=22
    )
    entity = make_entity(bridge, CrestronMediaPlayer, "media_player", config)
    await entity.async_select_source("Music")
    bridge.pulse_digital.assert_awaited_with(22)
    await entity.async_turn_on()
    assert bridge.pulse_digital.await_args_list[-2:] == [call(1), call(22)]
    bridge.digital.update({21: True, 22: True})
    assert entity.source == "Radio"
    assert entity.source == "Radio"
    assert caplog.text.count("multiple active source joins") == 1
    bridge.digital[21] = False
    assert entity.source == "Music"
    await entity.async_turn_off()
    bridge.pulse_digital.assert_awaited_with(2)
    bridge.set_analog.assert_not_awaited()


async def test_ha_registration_push_updates_and_unload(hass, bridge):
    """Load every real platform and exercise HA state and registry lifecycles."""
    hass.config.units = US_CUSTOMARY_SYSTEM
    mappings = [
        ("binary_sensor", {"is_on_join": 1}),
        ("sensor", {"value_join": 1, "divisor": 10}),
        ("switch", {"switch_join": 2}),
        ("light", {"brightness_join": 2}),
        ("cover", {"pos_join": 3, "stop_join": 3}),
        ("climate", CLIMATE),
        ("media_player", MEDIA),
    ]
    entry = MockConfigEntry(
        domain="crestron",
        title="Test bridge",
        data={"host": "127.0.0.1", "port": 16384},
        subentries_data=[
            {
                "subentry_id": f"mapping-{platform}",
                "subentry_type": "entity",
                "title": platform,
                "unique_id": None,
                "data": {"platform": platform, "name": f"Test {platform}", **config},
            }
            for platform, config in mappings
        ],
    )
    entry.add_to_hass(hass)
    with patch("custom_components.crestron.Bridge", return_value=bridge):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    assert len(bridge.callbacks) == 7
    entity_ids = {}
    for platform, _ in mappings:
        entity_id = registry.async_get_entity_id(
            platform, "crestron", f"{entry.entry_id}_mapping-{platform}"
        )
        assert entity_id is not None
        entity_ids[platform] = entity_id
        assert registry.async_get(entity_id).config_subentry_id == f"mapping-{platform}"
        assert hass.states.get(entity_id).state == "unknown"
        device = devices.async_get(registry.async_get(entity_id).device_id)
        assert device.via_device_id is not None
    bridge.feedback("digital", 1, True)
    bridge.feedback("analog", 1, 725)
    bridge.feedback("analog", 2, 65535)
    await hass.async_block_till_done()
    assert hass.states.get(entity_ids["binary_sensor"]).state == "on"
    assert hass.states.get(entity_ids["sensor"]).state == "72.5"
    assert hass.states.get(entity_ids["light"]).attributes["brightness"] == 255
    await hass.services.async_call(
        "light",
        "turn_on",
        {"entity_id": entity_ids["light"], "brightness": 128},
        blocking=True,
    )
    bridge.set_analog.assert_awaited_with(2, 128 * 257)
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": entity_ids["cover"], "position": 100},
        blocking=True,
    )
    bridge.set_analog.assert_awaited_with(3, 65535)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_ids["switch"]}, blocking=True
    )
    bridge.set_digital.assert_awaited_with(2, True)
    bridge.feedback("digital", 4, True)
    bridge.feedback("analog", 1, 681)
    bridge.feedback("analog", 2, 755)
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": entity_ids["climate"], "temperature": 68.5},
        blocking=True,
    )
    bridge.set_analog.assert_awaited_with(1, 685)
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": entity_ids["media_player"], "source": "Music"},
        blocking=True,
    )
    bridge.set_analog.assert_awaited_with(5, 2)
    bridge.feedback("available", None, False)
    await hass.async_block_till_done()
    assert all(
        hass.states.get(entity_id).state == "unavailable"
        for entity_id in entity_ids.values()
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not bridge.callbacks
    bridge.stop.assert_awaited_once()
