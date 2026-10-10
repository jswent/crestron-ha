"""Run integration tests inside real Home Assistant fixtures, without hardware."""

import pytest

# Import before the HA fixture temporarily prepends its test config directory.
import custom_components.crestron  # noqa: F401

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Enable loading the local integration under test."""


@pytest.fixture(autouse=True)
def local_sockets(socket_enabled):
    """The fake XSIG processors use real loopback TCP connections."""
