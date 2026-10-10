#!/usr/bin/env python3
"""Create demo bridge/entity mappings through the local HA config-flow API.

Complete onboarding at localhost:8124 and create a long-lived access token in
Profile > Security. Supply it through HA_TOKEN; never place it in arguments.
"""

import asyncio
import json
import os
from pathlib import Path

import aiohttp

BASE = "http://127.0.0.1:8124"


async def main():
    token = os.environ.get("HA_TOKEN")
    if not token:
        raise SystemExit(
            "Set HA_TOKEN to a token from the disposable local HA instance"
        )
    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {token}"}
    ) as session:

        async def request(method, path, data=None):
            async with session.request(method, BASE + path, json=data) as response:
                response.raise_for_status()
                return await response.json()

        entries = await request("GET", "/api/config/config_entries/entry")
        if any(
            e["domain"] == "crestron" and e["title"] == "Demo bridge" for e in entries
        ):
            raise SystemExit(
                "Demo bridge already exists; use the UI to edit or remove it"
            )
        flow = await request(
            "POST",
            "/api/config/config_entries/flow",
            {"handler": "crestron", "show_advanced_options": False},
        )
        result = await request(
            "POST",
            f"/api/config/config_entries/flow/{flow['flow_id']}",
            {"name": "Demo bridge", "host": "127.0.0.1", "port": 16384},
        )
        if result["type"] != "create_entry":
            raise SystemExit(
                f"Bridge flow did not finish: {result.get('errors', result['type'])}"
            )
        entry_id = result["result"]["entry_id"]
        entities = json.loads(
            (Path(__file__).resolve().parents[1] / "dev" / "entities.json").read_text()
        )
        for entity in entities:
            flow = await request(
                "POST",
                "/api/config/config_entries/subentries/flow",
                {"handler": [entry_id, "entity"], "show_advanced_options": False},
            )
            flow = await request(
                "POST",
                f"/api/config/config_entries/subentries/flow/{flow['flow_id']}",
                {"platform": entity["platform"]},
            )
            result = await request(
                "POST",
                f"/api/config/config_entries/subentries/flow/{flow['flow_id']}",
                {k: v for k, v in entity.items() if k != "platform"},
            )
            if result["type"] != "create_entry":
                raise SystemExit(
                    f"Mapping failed for {entity['name']}: {result.get('errors', result['type'])}"
                )
        print(
            f"Created Demo bridge with {len(entities)} entities. Run the XSIG simulator on port 16384."
        )


if __name__ == "__main__":
    asyncio.run(main())
