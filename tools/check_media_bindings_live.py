#!/usr/bin/env python3
"""Exercise source bindings against local HA:8124 and the real XSIG simulator.

Start tools/dev.py --media-demo, complete onboarding, then provide HA_TOKEN.
Uses only the Demo bridge on loopback port 16384. Stop other simulators first.
"""

import asyncio
import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.seed_demo import main as seed_demo
from tools.xsig_simulator import Simulator

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8124"
ZONE = "media_player.demo_audio"
SONOS = "media_player.sonos_1"


async def main():
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise SystemExit("Set HA_TOKEN to a token from the local dev instance")
    checks = []
    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {token}"}
    ) as session:

        async def request(method, path, data=None):
            async with session.request(method, BASE + path, json=data) as response:
                response.raise_for_status()
                return await response.json()

        async def action(entity, name, **data):
            return await request(
                "POST",
                "/api/services/media_player/" + name,
                {"entity_id": entity, **data},
            )

        async def state(entity=ZONE):
            return await request("GET", "/api/states/" + entity)

        async def expect(expected, *, title=None, clear=False, source=None):
            async with asyncio.timeout(5):
                while True:
                    current = await state()
                    attrs = current["attributes"]
                    clean = not any(
                        key.startswith("media_")
                        or key in ("entity_picture", "app_id", "app_name")
                        for key in attrs
                    )
                    if (
                        current["state"] == expected
                        and (title is None or attrs.get("media_title") == title)
                        and (not clear or clean)
                        and (source is None or attrs.get("source") == source)
                    ):
                        return current
                    await asyncio.sleep(0.02)

        def passed(label):
            checks.append(label)
            print("PASS:", label, flush=True)

        entries = await request("GET", "/api/config/config_entries/entry")
        if not any(
            e["domain"] == "crestron" and e["title"] == "Demo bridge" for e in entries
        ):
            await seed_demo()
        entries = await request("GET", "/api/config/config_entries/entry")
        entry = next(
            e
            for e in entries
            if e["domain"] == "crestron" and e["title"] == "Demo bridge"
        )
        config = await request("GET", "/api/config")
        simulator = Simulator(json.loads((ROOT / "dev/scenario.json").read_text()))
        peer = asyncio.create_task(simulator.run_session("127.0.0.1", 16384))
        try:
            await action(SONOS, "media_play")
            first_title = (await state(SONOS))["attributes"]["media_title"]
            current = await expect("playing", title=first_title)
            assert current["attributes"]["source"] == "Sonos 1"
            assert current["attributes"]["volume_level"] == 16384 / 65535
            picture = current["attributes"]["entity_picture"]
            assert picture == (await state(SONOS))["attributes"]["entity_picture"]
            async with session.get(BASE + picture) as response:
                response.raise_for_status()
                assert "svg" in response.headers["Content-Type"]
            passed("Bound playback, metadata, reachable artwork and Crestron volume")

            simulator.echo = False
            await action(ZONE, "select_source", source="Sonos 1")
            await expect("playing", title=first_title)
            await action(ZONE, "turn_on")
            await expect("playing", title=first_title)
            simulator.echo = True
            passed("Unchanged source/power confirmed by explicit XSIG refresh")

            await action(SONOS, "media_pause")
            await expect("paused", title=first_title)
            await action(SONOS, "media_stop")
            await expect("idle", clear=True)
            await action(SONOS, "media_next_track")
            next_title = (await state(SONOS))["attributes"]["media_title"]
            assert next_title != first_title
            await expect("playing", title=next_title)
            passed("Pause, stop with retained provider title, and next-track updates")

            simulator.echo = False
            await action(ZONE, "select_source", source="TV")
            current = await expect("on", clear=True)
            assert current["attributes"]["source"] == "Sonos 1"
            await action(SONOS, "media_next_track")
            await expect("on", clear=True)
            await simulator.send_feedback("analog", 21, 2)
            current = await expect("on", clear=True, source="TV")
            assert current["attributes"]["source"] == "TV"
            passed(
                "Immediate clearing before source feedback; late old-player updates ignored"
            )

            simulator.echo = True
            await action(ZONE, "select_source", source="Sonos 1")
            current_title = (await state(SONOS))["attributes"]["media_title"]
            await expect("playing", title=current_title)
            await action(ZONE, "turn_off")
            await expect("off", clear=True)
            await action(SONOS, "media_next_track")
            await expect("off", clear=True)
            await action(ZONE, "turn_on")
            current_title = (await state(SONOS))["attributes"]["media_title"]
            await expect("playing", title=current_title)
            passed("Power off/on with latched simulator feedback and default source")

            await request(
                "POST",
                "/api/states/" + SONOS,
                {
                    "state": "unavailable",
                    "attributes": (await state(SONOS))["attributes"],
                },
            )
            await expect("on", clear=True)
            await action(SONOS, "media_play")
            await expect("playing", title=current_title)
            passed(
                "Unavailable target clears metadata without disabling the zone; recovery works"
            )

            # Reconfigure the actual mapping through the same API as the HA UI.
            async with session.ws_connect(BASE + "/api/websocket") as ws:
                await ws.receive_json()
                await ws.send_json({"type": "auth", "access_token": token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                await ws.send_json({"id": 1, "type": "config/entity_registry/list"})
                entities = (await ws.receive_json())["result"]
            mapping_id = next(
                item["config_subentry_id"]
                for item in entities
                if item["entity_id"] == ZONE
            )
            demo = next(
                item
                for item in json.loads((ROOT / "dev/entities.json").read_text())
                if item["platform"] == "media_player"
            )
            mapping = {key: value for key, value in demo.items() if key != "platform"}
            original_writer = simulator.writer
            for bindings in ({}, {"1": SONOS}):
                flow = await request(
                    "POST",
                    "/api/config/config_entries/subentries/flow",
                    {
                        "handler": [entry["entry_id"], "entity"],
                        "subentry_id": mapping_id,
                        "show_advanced_options": False,
                    },
                )
                result = await request(
                    "POST",
                    f"/api/config/config_entries/subentries/flow/{flow['flow_id']}",
                    {**mapping, "source_bindings": bindings},
                )
                assert (
                    result["type"] == "abort"
                    and result["reason"] == "reconfigure_successful"
                ), result
                if bindings:
                    await expect("playing", title=current_title)
                else:
                    await expect("on", clear=True)
                assert simulator.writer is original_writer
            passed(
                "Live binding removal/restoration preserves entity ID and processor connection"
            )

            simulator.writer.close()
            await peer
            await expect("unavailable", clear=True)
            peer = asyncio.create_task(simulator.run_session("127.0.0.1", 16384))
            await expect("playing", title=current_title)
            passed(
                "Processor disconnect clears metadata; reconnect restores current source"
            )

            result = {
                "completed_at": datetime.now(UTC).isoformat(),
                "home_assistant": config["version"],
                "integration": json.loads(
                    (ROOT / "custom_components/crestron/manifest.json").read_text()
                )["version"],
                "checks": checks,
            }
            (ROOT / "dev/config/source-binding-acceptance.json").write_text(
                json.dumps(result, indent=2) + "\n"
            )
            print(f"Completed {len(checks)} live acceptance groups.", flush=True)
        finally:
            if simulator.writer:
                simulator.writer.close()
            with suppress(asyncio.TimeoutError, ConnectionError):
                await asyncio.wait_for(peer, 3)


if __name__ == "__main__":
    asyncio.run(main())
