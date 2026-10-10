"""Constants for Crestron XSIG bridges."""

from homeassistant.const import Platform

DOMAIN = "crestron"
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.LIGHT,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.MEDIA_PLAYER,
]
DEFAULT_PORT = 16384
EVENT_JOIN_CHANGED = "crestron_join_changed"
