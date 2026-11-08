#!/usr/bin/env python3
"""Run a dummy Crestron processor against the integration's TCP listener.

Usage: python tools/xsig_simulator.py --host 127.0.0.1 --port 5000

A scenario JSON file can contain digital/analog/serial maps keyed by join number,
feedback mappings, and scheduled events relative to each connection. Example::

    {
      "digital": {"1": false}, "analog": {"1": 32768},
      "serial": {"1": "Living room"},
      "feedback": [{"kind": "digital", "join": 2, "feedback_join": 1}],
      "events": [{"after": 2, "kind": "digital", "join": 1, "value": true},
                 {"after": 5, "kind": "sync"},
                 {"after": 10, "kind": "disconnect"}]
    }

Commands are echoed on the same join unless a feedback mapping redirects them.
Optional pulse_feedback entries (join, feedback_join, value) latch digital
feedback on a rising command and ignore its release, e.g. power on/off buttons.
Like the XSIG symbol, the reply to an update request carries non-zero signals only.
Use --all-joins to seed every legal join with a non-zero value, --no-echo for
feedback-only testing, and --interactive for commands: digital JOIN 0|1,
analog JOIN VALUE, serial JOIN TEXT, sync, reconnect, quit. Serial values are never
printed to the console.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any

# Load the dependency-free codec without importing Home Assistant's package.
_XSIG_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "crestron" / "xsig.py"
)
_SPEC = importlib.util.spec_from_file_location("crestron_simulator_xsig", _XSIG_PATH)
assert _SPEC and _SPEC.loader
xsig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(xsig)

ENCODERS = {
    "digital": xsig.encode_digital,
    "analog": xsig.encode_analog,
    "serial": xsig.encode_serial,
}
DEFAULT_SCENARIO = {
    "digital": {"1": False},
    "analog": {"1": 32768},
    "serial": {"1": "Dummy processor"},
}


def validate_scenario(raw: Any) -> dict[str, Any]:
    """Validate all scenario commands before opening a network connection."""
    if not isinstance(raw, dict):
        raise ValueError("Scenario must be a JSON object")
    scenario: dict[str, Any] = {}
    for kind, encode in ENCODERS.items():
        values = raw.get(kind, {})
        if not isinstance(values, dict):
            raise ValueError(f"{kind} must be an object keyed by join number")
        scenario[kind] = {}
        for join, value in values.items():
            index = int(join)
            if str(index) != str(join):
                raise ValueError("Scenario joins must be whole numbers")
            encode(index, value)
            scenario[kind][index] = value
    scenario["events"] = []
    for event in raw.get("events", []):
        if not isinstance(event, dict):
            raise ValueError("Each scheduled event must be an object")
        after = event.get("after", 0)
        if type(after) not in (int, float) or not 0 <= after <= 86400:
            raise ValueError(
                "Scheduled event delay must be between 0 and 86400 seconds"
            )
        kind = event.get("kind")
        if kind in ENCODERS:
            ENCODERS[kind](event.get("join"), event.get("value"))
        elif kind not in ("sync", "disconnect"):
            raise ValueError("Unknown scheduled event kind")
        scenario["events"].append(dict(event, after=after))
    scenario["events"].sort(key=lambda event: event["after"])
    scenario["feedback"] = {}
    scenario["pulse_feedback"] = {}
    for mapping in raw.get("pulse_feedback", []):
        xsig.encode_digital(mapping["join"], True)
        xsig.encode_digital(mapping["feedback_join"], mapping["value"])
        if mapping["join"] in scenario["pulse_feedback"]:
            raise ValueError("Duplicate pulse feedback mapping")
        scenario["pulse_feedback"][mapping["join"]] = (
            mapping["feedback_join"],
            bool(mapping["value"]),
        )
    for mapping in raw.get("feedback", []):
        kind = mapping["kind"]
        if kind not in ENCODERS:
            raise ValueError("Unknown feedback mapping kind")
        sample = "" if kind == "serial" else 0
        ENCODERS[kind](mapping["join"], sample)
        ENCODERS[kind](mapping["feedback_join"], sample)
        key = (kind, mapping["join"])
        if key in scenario["feedback"]:
            raise ValueError("Duplicate command feedback mapping")
        scenario["feedback"][key] = mapping["feedback_join"]
    return scenario


class Simulator:
    """A processor peer with seeded feedback, command echo and timed changes."""

    def __init__(
        self, scenario: dict[str, Any], *, echo: bool = True, all_joins: bool = False
    ) -> None:
        self.scenario = validate_scenario(scenario)
        self.echo = echo
        self.values = {kind: dict(self.scenario[kind]) for kind in ENCODERS}
        if all_joins:
            for kind, maximum, default in (
                ("digital", 4096, True),
                ("analog", 1024, 1),
                ("serial", 1024, "Simulated"),
            ):
                self.values[kind] = {
                    join: self.values[kind].get(join, default)
                    for join in range(1, maximum + 1)
                }
        self.writer: asyncio.StreamWriter | None = None
        self.commands: list[tuple[str, int, Any]] = []
        self.quit = False
        self._write_lock = asyncio.Lock()

    async def run_session(
        self, host: str, port: int, *, interactive: bool = False
    ) -> None:
        """Connect until the server, scenario or console disconnects this peer."""
        reader, writer = await asyncio.open_connection(host, port)
        self.writer = writer
        print("Connected; waiting for the integration's update request", flush=True)
        scheduled = asyncio.create_task(self._scheduled_events())
        console = asyncio.create_task(self._console()) if interactive else None
        try:
            while True:
                kind, join, value = await xsig.read_message(reader)
                if kind == "update":
                    await self.seed_feedback()
                elif kind in ENCODERS:
                    # The bounded command history is for local test assertions;
                    # values are intentionally omitted from user-visible output.
                    self.commands.append((kind, join, value))
                    del self.commands[:-100]
                    print(f"Command: {kind} join {join}", flush=True)
                    if self.echo:
                        if (
                            kind == "digital"
                            and join in self.scenario["pulse_feedback"]
                        ):
                            if value:
                                target, latched = self.scenario["pulse_feedback"][join]
                                await self.send_feedback(kind, target, latched)
                        else:
                            target = self.scenario["feedback"].get((kind, join), join)
                            await self.send_feedback(kind, target, value)
        except asyncio.IncompleteReadError:
            print("Disconnected", flush=True)
        finally:
            scheduled.cancel()
            if console:
                console.cancel()
            await asyncio.gather(
                scheduled, *([console] if console else []), return_exceptions=True
            )
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()
            self.writer = None

    async def seed_feedback(self) -> None:
        """Answer a 0xFD update request as the XSIG symbol does.

        Only signals that are not zero are re-transmitted; a low digital, zero
        analog or empty serial join is never part of a refresh.
        """
        for kind in ENCODERS:
            for join, value in self.values[kind].items():
                if value:
                    await self._write(ENCODERS[kind](join, value))
        print("Seeded the non-zero feedback joins", flush=True)

    async def send_feedback(self, kind: str, join: int, value: Any) -> None:
        """Update and send a simulated feedback signal."""
        payload = ENCODERS[kind](join, value)
        await self._write(payload)
        self.values[kind][join] = value

    async def _write(self, payload: bytes) -> None:
        async with self._write_lock:
            if self.writer is None or self.writer.is_closing():
                raise ConnectionError("Simulator is disconnected")
            self.writer.write(payload)
            await self.writer.drain()

    async def _scheduled_events(self) -> None:
        start = asyncio.get_running_loop().time()
        try:
            for event in self.scenario["events"]:
                await asyncio.sleep(
                    max(0, start + event["after"] - asyncio.get_running_loop().time())
                )
                kind = event["kind"]
                if kind == "disconnect":
                    assert self.writer is not None
                    self.writer.close()
                    return
                if kind == "sync":
                    await self._write(b"\xfb")
                else:
                    await self.send_feedback(kind, event["join"], event["value"])
        except (ConnectionError, OSError):
            return

    async def _console(self) -> None:
        """Read stdin without a thread that could prevent clean process shutdown."""
        loop = asyncio.get_running_loop()
        lines: asyncio.Queue[str] = asyncio.Queue()
        descriptor = sys.stdin.fileno()
        loop.add_reader(descriptor, lambda: lines.put_nowait(sys.stdin.readline()))
        print(
            "Commands: digital/analog/serial JOIN VALUE, sync, reconnect, quit",
            flush=True,
        )
        try:
            while True:
                line = await lines.get()
                if not line:
                    return
                parts = line.strip().split(maxsplit=2)
                if not parts:
                    continue
                try:
                    if parts[0] in ("quit", "reconnect"):
                        self.quit = parts[0] == "quit"
                        assert self.writer is not None
                        self.writer.close()
                        return
                    if parts[0] == "sync":
                        await self._write(b"\xfb")
                        continue
                    kind, join, value = parts
                    await self.send_feedback(
                        kind, int(join), value if kind == "serial" else int(value)
                    )
                except (ValueError, KeyError):
                    print(
                        "Invalid command (check signal type, join and value)",
                        flush=True,
                    )
        finally:
            loop.remove_reader(descriptor)


async def _main(args: argparse.Namespace) -> None:
    scenario = (
        json.loads(args.scenario.read_text()) if args.scenario else DEFAULT_SCENARIO
    )
    simulator = Simulator(scenario, echo=not args.no_echo, all_joins=args.all_joins)
    while not simulator.quit:
        try:
            await simulator.run_session(
                args.host, args.port, interactive=args.interactive
            )
        except (ConnectionError, OSError, xsig.ProtocolError) as err:
            print(f"Connection ended: {type(err).__name__}", flush=True)
        if args.once or simulator.quit:
            return
        print(f"Reconnecting in {args.reconnect_delay:g} seconds", flush=True)
        await asyncio.sleep(args.reconnect_delay)


def main() -> None:
    """Parse CLI options and run until interrupted."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--reconnect-delay", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-echo", action="store_true")
    parser.add_argument("--all-joins", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not 0 < args.reconnect_delay <= 3600:
        parser.error("port must be 1..65535 and reconnect-delay must be >0 and <=3600")
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass
    except (ValueError, KeyError, TypeError) as err:
        parser.error(str(err))


if __name__ == "__main__":
    main()
