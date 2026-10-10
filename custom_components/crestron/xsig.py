"""Small, dependency-free asyncio implementation of the Crestron XSIG wire format.

The processor connects to this server. Received values are feedback, so sending a
command deliberately does not update the feedback cache. All callbacks run on the
event loop and must be synchronous and non-blocking.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

MAX_DIGITAL_JOIN = 4096
MAX_ANALOG_JOIN = 1024
MAX_SERIAL_JOIN = 1024
MAX_SERIAL_BYTES = 252
TRACE_LIMIT = 100
SEND_TIMEOUT = 5

type Value = bool | int | str | None
type Message = tuple[str, int | None, Value]
type Callback = Callable[[str, int | None, Value], None]

_LOGGER = logging.getLogger(__name__)


class ProtocolError(ValueError):
    """The peer sent an invalid or unsupported XSIG frame."""


def _join(join: int, maximum: int) -> int:
    """Validate a one-based join without accepting bools or truncating floats."""
    if type(join) is not int or not 1 <= join <= maximum:
        raise ValueError(f"Join must be an integer between 1 and {maximum}")
    return join - 1


def encode_digital(join: int, value: bool | int) -> bytes:
    """Encode a digital signal; XSIG stores an inverted boolean in bit 5."""
    index = _join(join, MAX_DIGITAL_JOIN)
    if not isinstance(value, (bool, int)) or value not in (0, 1):
        raise ValueError("Digital value must be a boolean or 0 or 1")
    return bytes((0x80 | (0 if value else 0x20) | (index >> 7), index & 0x7F))


def encode_analog(join: int, value: int) -> bytes:
    """Encode an unsigned 16-bit analog signal."""
    index = _join(join, MAX_ANALOG_JOIN)
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("Analog value must be an integer between 0 and 65535")
    return bytes(
        (
            0xC0 | ((value >> 10) & 0x30) | (index >> 7),
            index & 0x7F,
            (value >> 7) & 0x7F,
            value & 0x7F,
        )
    )


def encode_serial(join: int, value: str) -> bytes:
    """Encode UTF-8 text, enforcing the processor's limit in bytes."""
    index = _join(join, MAX_SERIAL_JOIN)
    if not isinstance(value, str):
        raise ValueError("Serial value must be text")
    payload = value.encode("utf-8")
    if len(payload) > MAX_SERIAL_BYTES:
        raise ValueError(f"Serial value exceeds {MAX_SERIAL_BYTES} UTF-8 bytes")
    return bytes((0xC8 | (index >> 7), index & 0x7F)) + payload + b"\xff"


async def read_message(reader: asyncio.StreamReader) -> Message:
    """Read one frame, preserving boundaries across fragmented/coalesced TCP reads.

    An incomplete frame raises IncompleteReadError. Invalid headers, analog bytes,
    oversized serial values and invalid UTF-8 raise ProtocolError; the caller must
    close that connection rather than attempt to guess the next frame boundary.
    """
    first = (await reader.readexactly(1))[0]
    if first == 0xFB:
        return "sync", None, None
    if first == 0xFD:
        return "update", None, None
    if first & 0xC0 == 0x80:
        kind = "digital"
    elif first & 0xC8 == 0xC0:
        kind = "analog"
    elif first & 0xF8 == 0xC8:
        kind = "serial"
    else:
        raise ProtocolError("Invalid XSIG frame header")
    second = (await reader.readexactly(1))[0]
    if second & 0x80:
        raise ProtocolError("Invalid XSIG join byte")
    if kind == "digital":
        return kind, ((first & 0x1F) << 7 | second) + 1, not bool(first & 0x20)
    join = ((first & 7) << 7 | second) + 1
    if kind == "analog":
        upper, lower = await reader.readexactly(2)
        if upper & 0x80 or lower & 0x80:
            raise ProtocolError("Invalid XSIG analog value bytes")
        return kind, join, ((first & 0x30) << 10) | (upper << 7) | lower
    # Read at most the supported payload plus terminator, even if a peer never
    # sends the terminator. This bound also applies to callers with a large reader.
    payload = bytearray()
    for _ in range(MAX_SERIAL_BYTES + 1):
        byte = (await reader.readexactly(1))[0]
        if byte == 0xFF:
            try:
                return kind, join, payload.decode("utf-8")
            except UnicodeDecodeError as err:
                raise ProtocolError("Invalid XSIG serial UTF-8") from err
        payload.append(byte)
    raise ProtocolError("XSIG serial payload exceeds byte limit")


class XSigServer:
    """One isolated processor connection with feedback, commands and diagnostics."""

    def __init__(self) -> None:
        self._server: asyncio.Server | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._available = False
        self._stopping = False
        self._session_id = 0
        self._digital: dict[int, bool] = {}
        self._analog: dict[int, int] = {}
        self._serial: dict[int, str] = {}
        self._callbacks: set[Callback] = set()
        self._join_callbacks: dict[tuple[str, int], set[Callback]] = {}
        self._availability_callbacks: set[Callback] = set()
        self._handlers: set[asyncio.Task[None]] = set()
        self._streams: set[asyncio.StreamWriter] = set()
        self._pulse_tasks: set[asyncio.Task[Any]] = set()
        self._pulse_locks: dict[int, asyncio.Lock] = {}
        self._send_lock = asyncio.Lock()
        self._trace: deque[dict[str, Any]] = deque(maxlen=TRACE_LIMIT)
        self._received = 0
        self._sent = 0
        self._connections = 0
        self._rejected_connections = 0
        self._protocol_errors = 0

    @property
    def available(self) -> bool:
        """Whether a processor is currently connected."""
        return self._available

    @property
    def bound_port(self) -> int | None:
        """The actual listening port (also useful when tests request port zero)."""
        if self._server and self._server.sockets:
            return self._server.sockets[0].getsockname()[1]
        return None

    async def start(self, host: str, port: int) -> None:
        """Bind a listener; propagate bind errors to the integration setup flow."""
        if self._server is not None:
            raise RuntimeError("XSIG server is already started")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Port must be an integer between 0 and 65535")
        self._stopping = False
        self._server = await asyncio.start_server(self._accept, host, port, limit=1024)
        self._record("lifecycle", "listening")

    async def stop(self) -> None:
        """Release the listening socket, active stream and all owned tasks."""
        self._stopping = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        # Let cancellation deassert outstanding pulses while the socket is usable.
        await self._cancel_pulses()
        for writer in tuple(self._streams):
            writer.close()
        tasks = tuple(self._handlers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if server is not None:
            # Modern asyncio waits for client streams as well as the listener;
            # waiting before closing the active peer would deadlock unloading.
            await server.wait_closed()
        self._clear_feedback()
        self._set_available(False)
        self._record("lifecycle", "stopped")

    def subscribe(
        self,
        callback: Callback,
        *,
        joins: set[tuple[str, int]] | None = None,
    ) -> Callable[[], None]:
        """Subscribe to all messages, or selected joins and availability changes.

        Indexed subscriptions do not receive sync requests: those request an
        outbound snapshot and do not alter entity feedback.
        """
        if joins is None:
            self._callbacks.add(callback)

            def unsubscribe() -> None:
                self._callbacks.discard(callback)

            return unsubscribe

        watched = frozenset(joins)
        self._availability_callbacks.add(callback)
        for key in watched:
            self._join_callbacks.setdefault(key, set()).add(callback)

        def unsubscribe() -> None:
            self._availability_callbacks.discard(callback)
            for key in watched:
                if callbacks := self._join_callbacks.get(key):
                    callbacks.discard(callback)
                    if not callbacks:
                        del self._join_callbacks[key]

        return unsubscribe

    def get_digital(self, join: int) -> bool | None:
        """Read current feedback, or None until this session reports the join."""
        _join(join, MAX_DIGITAL_JOIN)
        return self._digital.get(join)

    def get_analog(self, join: int) -> int | None:
        """Read analog feedback, or None when no current value is known."""
        _join(join, MAX_ANALOG_JOIN)
        return self._analog.get(join)

    def get_serial(self, join: int) -> str | None:
        """Read serial feedback, or None when no current value is known."""
        _join(join, MAX_SERIAL_JOIN)
        return self._serial.get(join)

    async def set_digital(self, join: int, value: bool | int) -> None:
        """Send a digital command without fabricating processor feedback."""
        await self._send(encode_digital(join, value), "digital", join)

    async def set_analog(self, join: int, value: int) -> None:
        """Send an analog command."""
        await self._send(encode_analog(join, value), "analog", join)

    async def set_serial(self, join: int, value: str) -> None:
        """Send a serial command."""
        await self._send(encode_serial(join, value), "serial", join)

    async def request_update(self) -> None:
        """Ask the connected processor to send all current feedback (0xFD)."""
        await self._send(b"\xfd", "update")

    async def pulse_digital(self, join: int, seconds: float = 0.05) -> None:
        """Pulse a join, serializing overlapping pulses and cleaning up on cancel."""
        _join(join, MAX_DIGITAL_JOIN)
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not 0 < seconds <= 60
        ):
            raise ValueError(
                "Pulse duration must be greater than zero and at most 60 seconds"
            )
        task = asyncio.current_task()
        assert task is not None
        self._pulse_tasks.add(task)
        session = self._session_id
        lock = self._pulse_locks.setdefault(join, asyncio.Lock())
        try:
            async with lock:
                if session != self._session_id or not self.available:
                    raise ConnectionError("Crestron processor is disconnected")
                try:
                    await self.set_digital(join, True)
                    await asyncio.sleep(seconds)
                finally:
                    if session == self._session_id and self.available:
                        with suppress(ConnectionError, OSError):
                            await self.set_digital(join, False)
        finally:
            self._pulse_tasks.discard(task)

    def snapshot(self) -> dict[str, Any]:
        """Return bounded diagnostics with no peer identity or signal values."""
        return {
            "available": self.available,
            "listening": self._server is not None,
            "bound_port": self.bound_port,
            "connections": self._connections,
            "rejected_connections": self._rejected_connections,
            "protocol_errors": self._protocol_errors,
            "messages_received": self._received,
            "messages_sent": self._sent,
            "known_joins": {
                "digital": len(self._digital),
                "analog": len(self._analog),
                "serial": len(self._serial),
            },
            "trace": [dict(event) for event in self._trace],
        }

    def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._streams.add(writer)
        task = asyncio.create_task(self._handle_connection(reader, writer))
        self._handlers.add(task)

        def finished(task: asyncio.Task[None]) -> None:
            # A task cancelled before its first instruction never enters its
            # finally block. Track the accepted stream independently for unload.
            writer.close()
            self._streams.discard(writer)
            self._handlers.discard(task)

        task.add_done_callback(finished)

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._stopping or self._writer is not None:
            self._rejected_connections += 1
            self._record("lifecycle", "connection_rejected")
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()
            return
        self._writer = writer
        self._session_id += 1
        self._connections += 1
        self._clear_feedback()
        self._available = True
        self._record("lifecycle", "connected")
        try:
            await self.request_update()
            # Home Assistant can start callback-created tasks eagerly. Complete
            # the handshake before subscribers can send initial template values.
            self._notify("available", None, True)
            while True:
                kind, join, value = await read_message(reader)
                self._received += 1
                self._record("receive", kind, join)
                if kind == "digital":
                    self._digital[join] = value
                elif kind == "analog":
                    self._analog[join] = value
                elif kind == "serial":
                    self._serial[join] = value
                elif kind == "update":
                    continue
                self._notify(kind, join, value)
        except asyncio.IncompleteReadError:
            pass
        except ProtocolError:
            self._protocol_errors += 1
            self._record("lifecycle", "protocol_error")
            _LOGGER.warning("Closing Crestron connection after an invalid XSIG frame")
        except (ConnectionError, OSError):
            self._record("lifecycle", "connection_error")
        finally:
            if self._writer is writer:
                self._writer = None
                self._clear_feedback()
                self._set_available(False)
                await self._cancel_pulses()
                self._record("lifecycle", "disconnected")
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()

    async def _send(self, payload: bytes, kind: str, join: int | None = None) -> None:
        writer = self._writer
        async with self._send_lock:
            if (
                writer is None
                or writer is not self._writer
                or not self.available
                or writer.is_closing()
            ):
                raise ConnectionError("Crestron processor is disconnected")
            try:
                writer.write(payload)
                async with asyncio.timeout(SEND_TIMEOUT):
                    await writer.drain()
            except TimeoutError as err:
                writer.close()
                raise ConnectionError(
                    "Crestron processor stopped accepting commands"
                ) from err
            except (ConnectionError, OSError):
                writer.close()
                raise
            self._sent += 1
            self._record("send", kind, join)

    async def _cancel_pulses(self) -> None:
        current = asyncio.current_task()
        tasks = tuple(task for task in self._pulse_tasks if task is not current)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _clear_feedback(self) -> None:
        self._digital.clear()
        self._analog.clear()
        self._serial.clear()

    def _set_available(self, value: bool) -> None:
        if self._available != value:
            self._available = value
            self._notify("available", None, value)

    def _notify(self, kind: str, join: int | None, value: Value) -> None:
        callbacks = self._callbacks | self._join_callbacks.get((kind, join), set())
        if kind == "available":
            callbacks |= self._availability_callbacks
        for callback in callbacks:
            try:
                callback(kind, join, value)
            except Exception:
                # Exception text can contain user signal values. Do not log it.
                _LOGGER.error("Crestron subscription callback failed")

    def _record(self, direction: str, kind: str, join: int | None = None) -> None:
        event: dict[str, Any] = {
            "time": datetime.now(UTC).isoformat(),
            "direction": direction,
            "kind": kind,
        }
        if join is not None:
            event["join"] = join
            event["value"] = "**REDACTED**"
        self._trace.append(event)
