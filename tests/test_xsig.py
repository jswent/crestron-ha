"""Wire compatibility and lifecycle tests with real local TCP streams."""

import asyncio
import json
from contextlib import suppress
from unittest.mock import MagicMock

import pytest

from custom_components.crestron import xsig
from custom_components.crestron.xsig import (
    MAX_SERIAL_BYTES,
    TRACE_LIMIT,
    ProtocolError,
    XSigServer,
    encode_analog,
    encode_digital,
    encode_serial,
    read_message,
)


async def wait_until(predicate):
    """Wait for event-loop work without assuming the number of scheduling turns."""
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
async def server():
    """Bind to an ephemeral loopback port and clean up after every test."""
    instance = XSigServer()
    await instance.start("127.0.0.1", 0)
    try:
        yield instance
    finally:
        await instance.stop()


async def connect(server):
    """Connect a processor and consume its automatic update request."""
    reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
    assert await asyncio.wait_for(reader.readexactly(1), 2) == b"\xfd"
    return reader, writer


async def close_peer(writer):
    writer.close()
    with suppress(ConnectionError):
        await writer.wait_closed()


@pytest.mark.parametrize(
    ("encoder", "join", "value", "wire"),
    [
        (encode_digital, 1, True, b"\x80\x00"),
        (encode_digital, 4096, False, b"\xbf\x7f"),
        (encode_analog, 1, 0, b"\xc0\x00\x00\x00"),
        (encode_analog, 1024, 65535, b"\xf7\x7f\x7f\x7f"),
        (encode_analog, 129, 16384, b"\xd1\x00\x00\x00"),
        (encode_serial, 1024, "é", b"\xcf\x7f\xc3\xa9\xff"),
        (encode_serial, 1, "", b"\xc8\x00\xff"),
    ],
)
def test_legacy_wire_vectors(encoder, join, value, wire):
    """Pin bytes against the original component's published encoding."""
    assert encoder(join, value) == wire


@pytest.mark.parametrize("join", [0, -1, 4097, 1.5, True, "1"])
def test_invalid_digital_joins(join):
    with pytest.raises(ValueError):
        encode_digital(join, True)


@pytest.mark.parametrize("encoder,value", [(encode_analog, 0), (encode_serial, "x")])
@pytest.mark.parametrize("join", [0, 1025, 1.5, True])
def test_invalid_analog_serial_joins(encoder, value, join):
    with pytest.raises(ValueError):
        encoder(join, value)


@pytest.mark.parametrize("value", [-1, 2, 1.0, "yes", None])
def test_invalid_digital_values(value):
    with pytest.raises(ValueError):
        encode_digital(1, value)


@pytest.mark.parametrize("value", [-1, 65536, 0.5, True, "10"])
def test_invalid_analog_values(value):
    with pytest.raises(ValueError):
        encode_analog(1, value)


def test_serial_limit_counts_utf8_bytes():
    assert len(encode_serial(1, "é" * 126)) == MAX_SERIAL_BYTES + 3
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        encode_serial(1, "é" * 127)


@pytest.mark.parametrize(
    "payload",
    [
        b"\x01",
        b"\x80\x80",
        b"\xc0\x00\x80\x00",
        b"\xc8\x00\xfe\xff",
        b"\xc8\x00" + b"x" * 253,
    ],
)
async def test_malformed_frames_fail_bounded(payload):
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    with pytest.raises(ProtocolError):
        await asyncio.wait_for(read_message(reader), 1)


async def test_tcp_fragmentation_coalescing_and_sync(server):
    events = []
    server.subscribe(lambda *event: events.append(event))
    reader, writer = await connect(server)
    payload = (
        encode_digital(4096, True)
        + encode_analog(1024, 65535)
        + encode_serial(1024, "Å room")
    )
    for byte in payload:
        writer.write(bytes((byte,)))
        await writer.drain()
        await asyncio.sleep(0)
    writer.write(encode_digital(1, False) + encode_analog(1, 4097) + b"\xfb")
    await writer.drain()
    await wait_until(lambda: ("sync", None, None) in events)
    assert server.get_digital(4096) is True
    assert server.get_digital(1) is False
    assert server.get_analog(1024) == 65535
    assert server.get_analog(1) == 4097
    assert server.get_serial(1024) == "Å room"
    assert events[0] == ("available", None, True)
    await close_peer(writer)


async def test_outbound_commands_and_update_are_real_wire(server):
    reader, writer = await connect(server)
    await server.set_digital(1, True)
    await server.set_analog(1024, 65000)
    await server.set_serial(2, "Signal")
    await server.request_update()
    assert await read_message(reader) == ("digital", 1, True)
    assert await read_message(reader) == ("analog", 1024, 65000)
    assert await read_message(reader) == ("serial", 2, "Signal")
    assert await read_message(reader) == ("update", None, None)
    assert server.get_digital(1) is None
    assert server.get_analog(1024) is None
    assert server.get_serial(2) is None
    await close_peer(writer)


async def test_feedback_clears_on_disconnect_and_reconnect(server):
    _, writer = await connect(server)
    writer.write(
        encode_digital(1, True) + encode_analog(1, 4) + encode_serial(1, "old")
    )
    await writer.drain()
    await wait_until(lambda: server.get_serial(1) == "old")
    await close_peer(writer)
    await wait_until(lambda: not server.available)
    assert server.get_digital(1) is None
    assert server.get_analog(1) is None
    assert server.get_serial(1) is None
    with pytest.raises(ConnectionError):
        await server.set_digital(1, False)
    _, writer = await connect(server)
    assert server.get_digital(1) is None
    assert server.get_serial(1) is None
    await close_peer(writer)


async def test_reject_second_peer_without_displacing_first(server):
    first_reader, first_writer = await connect(server)
    second_reader, second_writer = await asyncio.open_connection(
        "127.0.0.1", server.bound_port
    )
    assert await asyncio.wait_for(second_reader.read(), 1) == b""
    await server.set_digital(7, True)
    assert await read_message(first_reader) == ("digital", 7, True)
    assert server.available
    assert server.snapshot()["rejected_connections"] == 1
    await close_peer(second_writer)
    await close_peer(first_writer)


async def test_invalid_peer_frame_disconnects_cleanly(server):
    reader, writer = await connect(server)
    writer.write(b"\xc0\x00\x80\x00")
    await writer.drain()
    assert await asyncio.wait_for(reader.read(), 1) == b""
    assert not server.available
    assert server.snapshot()["protocol_errors"] == 1
    await close_peer(writer)


async def test_partial_peer_frame_does_not_leave_task_running(server):
    _, writer = await connect(server)
    writer.write(b"\xc0\x00")
    await writer.drain()
    await close_peer(writer)
    await wait_until(lambda: not server.available)
    assert server.snapshot()["protocol_errors"] == 0


async def test_unsubscribe_and_callback_exception_isolation(server):
    events = []
    unsubscribe = server.subscribe(lambda *event: events.append(event))

    def broken(*event):
        raise RuntimeError("secret callback value")

    server.subscribe(broken)
    _, writer = await connect(server)
    assert events == [("available", None, True)]
    unsubscribe()
    unsubscribe()
    writer.write(encode_analog(1, 3))
    await writer.drain()
    await wait_until(lambda: server.get_analog(1) == 3)
    assert len(events) == 1
    await close_peer(writer)


async def test_trace_is_bounded_and_redacts_every_signal_value(server):
    _, writer = await connect(server)
    writer.write(b"".join(encode_analog(1, value) for value in range(150)))
    writer.write(encode_serial(1, "sensitive private scene"))
    await writer.drain()
    await wait_until(lambda: server.get_serial(1) is not None)
    snapshot = server.snapshot()
    assert len(snapshot["trace"]) == TRACE_LIMIT
    assert snapshot["messages_received"] == 151
    text = json.dumps(snapshot)
    assert "sensitive private scene" not in text
    assert "127.0.0.1" not in text
    assert all(
        event["value"] == "**REDACTED**"
        for event in snapshot["trace"]
        if "join" in event
    )
    snapshot["trace"][0]["value"] = "changed"
    assert server.snapshot()["trace"][0]["value"] == "**REDACTED**"
    await close_peer(writer)


async def test_same_join_pulses_serialize(server):
    reader, writer = await connect(server)
    tasks = [asyncio.create_task(server.pulse_digital(2, 0.01)) for _ in range(2)]
    messages = [await asyncio.wait_for(read_message(reader), 1) for _ in range(4)]
    await asyncio.gather(*tasks)
    assert messages == [("digital", 2, value) for value in (True, False, True, False)]
    await close_peer(writer)


async def test_cancelled_pulse_is_deasserted(server):
    reader, writer = await connect(server)
    pulse = asyncio.create_task(server.pulse_digital(1, 10))
    assert await read_message(reader) == ("digital", 1, True)
    pulse.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pulse
    assert await asyncio.wait_for(read_message(reader), 1) == ("digital", 1, False)
    await close_peer(writer)


async def test_stop_deasserts_pulses_closes_peer_and_releases_port(server):
    reader, writer = await connect(server)
    port = server.bound_port
    pulse = asyncio.create_task(server.pulse_digital(1, 10))
    assert await read_message(reader) == ("digital", 1, True)
    await server.stop()
    assert pulse.cancelled()
    assert await read_message(reader) == ("digital", 1, False)
    assert await asyncio.wait_for(reader.read(), 1) == b""
    assert not server.available
    assert server.bound_port is None
    await server.start("127.0.0.1", port)
    assert server.bound_port == port
    await close_peer(writer)


async def test_disconnect_cancels_queued_pulses_before_new_session(server):
    reader, writer = await connect(server)
    pulses = [asyncio.create_task(server.pulse_digital(1, 10)) for _ in range(2)]
    assert await read_message(reader) == ("digital", 1, True)
    await close_peer(writer)
    await wait_until(lambda: all(task.done() for task in pulses))
    assert all(task.cancelled() for task in pulses)
    reader, writer = await connect(server)
    await server.set_analog(1, 123)
    assert await read_message(reader) == ("analog", 1, 123)
    await close_peer(writer)


async def test_bind_collision_preserves_first_listener(server):
    duplicate = XSigServer()
    with pytest.raises(OSError):
        await duplicate.start("127.0.0.1", server.bound_port)
    assert duplicate.bound_port is None
    reader, writer = await connect(server)
    assert server.available
    await close_peer(writer)


async def test_shutdown_closes_peer_before_handler_has_started():
    """The accept callback can race with an integration unload in the same tick."""
    server = XSigServer()
    writer = MagicMock(spec=asyncio.StreamWriter)
    server._accept(asyncio.StreamReader(), writer)
    await server.stop()
    writer.close.assert_called()


async def test_stalled_writer_has_bounded_command_timeout(server, monkeypatch):
    reader, writer = await connect(server)
    monkeypatch.setattr(xsig, "SEND_TIMEOUT", 0.01)
    never_ready = asyncio.Event()
    monkeypatch.setattr(server._writer, "drain", never_ready.wait)
    with pytest.raises(ConnectionError, match="stopped accepting"):
        await server.set_digital(1, True)
    assert await read_message(reader) == ("digital", 1, True)
    assert await asyncio.wait_for(reader.read(), 1) == b""
    await wait_until(lambda: not server.available)
    await close_peer(writer)
