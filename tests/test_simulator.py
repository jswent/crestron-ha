"""Exercise the standalone dummy processor against the actual TCP server."""

import asyncio

import pytest

from custom_components.crestron.xsig import XSigServer
from tools.xsig_simulator import Simulator, validate_scenario


async def wait_until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def test_simulator_seeds_and_maps_command_feedback():
    server = XSigServer()
    simulator = Simulator(
        {
            "digital": {"1": False},
            "analog": {"1": 32768},
            "serial": {"1": "Initial"},
            "feedback": [{"kind": "digital", "join": 2, "feedback_join": 1}],
        }
    )
    await server.start("127.0.0.1", 0)
    peer = asyncio.create_task(simulator.run_session("127.0.0.1", server.bound_port))
    try:
        await wait_until(lambda: server.get_serial(1) == "Initial")
        assert server.get_digital(1) is False
        assert server.get_analog(1) == 32768
        await server.set_digital(2, True)
        await wait_until(lambda: server.get_digital(1) is True)
        assert server.get_digital(2) is None
        assert simulator.commands == [("digital", 2, True)]
        await server.set_serial(2, "Changed")
        await wait_until(lambda: server.get_serial(2) == "Changed")
    finally:
        await server.stop()
        await asyncio.wait_for(peer, 2)


async def test_scheduled_feedback_sync_and_reconnect():
    server = XSigServer()
    events = []
    server.subscribe(lambda *event: events.append(event))
    simulator = Simulator(
        {
            "digital": {"1": False},
            "events": [
                {"after": 0.01, "kind": "digital", "join": 1, "value": True},
                {"after": 0.02, "kind": "sync"},
                {"after": 0.03, "kind": "disconnect"},
            ],
        }
    )
    await server.start("127.0.0.1", 0)
    try:
        for _ in range(2):
            await asyncio.wait_for(
                simulator.run_session("127.0.0.1", server.bound_port), 2
            )
            await wait_until(lambda: not server.available)
            assert server.get_digital(1) is None
        assert events.count(("sync", None, None)) == 2
        assert events.count(("digital", 1, True)) >= 2
        assert server.snapshot()["connections"] == 2
    finally:
        await server.stop()


async def test_no_echo_leaves_feedback_unchanged():
    server = XSigServer()
    simulator = Simulator({"digital": {"1": False}}, echo=False)
    await server.start("127.0.0.1", 0)
    peer = asyncio.create_task(simulator.run_session("127.0.0.1", server.bound_port))
    try:
        await wait_until(lambda: server.get_digital(1) is False)
        await server.set_digital(1, True)
        await wait_until(lambda: bool(simulator.commands))
        assert server.get_digital(1) is False
    finally:
        await server.stop()
        await asyncio.wait_for(peer, 2)


def test_seed_all_joins_and_validate_before_connecting():
    simulator = Simulator({}, all_joins=True)
    assert len(simulator.values["digital"]) == 4096
    assert len(simulator.values["analog"]) == 1024
    assert len(simulator.values["serial"]) == 1024
    with pytest.raises(ValueError):
        validate_scenario({"serial": {"1": "é" * 127}})
    with pytest.raises(ValueError):
        validate_scenario({"events": [{"after": -1, "kind": "sync"}]})
