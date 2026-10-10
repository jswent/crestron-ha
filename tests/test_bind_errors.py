"""Socket setup failures must explain the actual operating-system error."""

import errno
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.crestron import async_setup_entry
from custom_components.crestron.bridge import Bridge


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (errno.EADDRNOTAVAIL, "Listen address is not available"),
        (errno.EADDRINUSE, "already in use"),
        (errno.EACCES, "denied permission"),
        (errno.EPERM, "denied permission"),
        (errno.EAFNOSUPPORT, "address family is unsupported"),
        (errno.EMFILE, "EMFILE"),
        (None, "without an error code"),
    ],
)
async def test_bind_error_explains_cause_and_cleans_up(hass, code, expected):
    entry = MockConfigEntry(
        domain="crestron", data={"name": "Bridge", "host": "192.0.2.17", "port": 32705}
    )
    failure = OSError(code, "Cannot bind private address 192.0.2.17")
    with (
        patch.object(Bridge, "start", side_effect=failure),
        patch.object(Bridge, "stop", new_callable=AsyncMock) as stop,
        patch.object(hass.config_entries, "async_forward_entry_setups") as forward,
    ):
        with pytest.raises(ConfigEntryNotReady) as exc:
            await async_setup_entry(hass, entry)
    message = str(exc.value)
    assert "32705" in message
    assert expected in message
    assert "192.0.2.17" not in message
    assert exc.value.__cause__ is failure
    stop.assert_awaited_once()
    forward.assert_not_called()
