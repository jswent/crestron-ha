"""Regenerate English labels after changing the dynamic mapping schemas."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.crestron.schema import PLATFORM_NAMES, entity_fields

root = Path(__file__).resolve().parents[1] / "custom_components" / "crestron"
labels = {
    str(k): str(k).replace("_", " ").capitalize()
    for platform in PLATFORM_NAMES
    for k in entity_fields(platform)
}
labels.update(
    {
        "join": "Join number",
        "join_type": "Join type",
        "value_template": "Value template",
        "actions": "Actions",
        "ignore_release": "Ignore digital release (off)",
        "host": "Listen address",
        "port": "Listen port",
        "name": "Name",
        "platform": "Entity type",
        "manufacturer": "Manufacturer (optional)",
        "model": "Model (optional)",
        "entities": "Entity features",
        "source_bindings": "Source media player bindings (optional)",
    }
)
entity_description = "Create a standalone signal without a device. Use Physical device to group several features of the same hardware. Map the joins wired in your Crestron program. Source maps pair each source number or digital join with a name, one per line, for example `1: Music` and `2: TV`. Optional source bindings pair those same numbers with media player entity IDs, for example `1: media_player.sonos_1`. Unbound sources need no entry. Analog and serial joins: 1–1024; digital joins: 1–4096. Feedback determines state."
bridge_description = "A bridge is one independent XSIG listener. Crestron connects to Home Assistant on this port. Use a different port for every bridge; 0.0.0.0 listens on all local IPv4 interfaces. Listen address belongs to this Home Assistant machine, not the Crestron processor. A processor does not need to be connected to save configuration."
errors = {
    "invalid_mapping": "Check join ranges and required fields. Media players need either an analog source join and source map, or a digital source map. Source names must be unique and the default must exist. Source bindings must use configured source numbers and one media_player entity ID per source. Export joins must be unique and templates valid."
}
strings = {
    "title": "Crestron XSIG",
    "config": {
        "step": {
            k: {
                "title": "Crestron bridge",
                "description": bridge_description,
                "data": {n: labels[n] for n in ("name", "host", "port")},
            }
            for k in ("user", "reconfigure")
        },
        "error": {
            "invalid_config": "Enter a name, a literal listen IP address and a port from 1 to 65535.",
            "port_in_use": "Another Crestron bridge already uses this port.",
        },
        "abort": {"reconfigure_successful": "Bridge configuration updated."},
    },
    "config_subentries": {},
}
for kind in ("entity", "device", "export", "action"):
    title = {
        "entity": "Standalone entity",
        "device": "Physical device",
        "export": "Export to Crestron",
        "action": "Action from Crestron",
    }[kind]
    description = {
        "entity": entity_description,
        "device": "Group features of one physical product under one device. Entity features is a list of objects, each with a stable id, platform, name and platform join settings, for example `id: level`, `platform: light`, `name: Light` and `brightness_join: 1`. See the repository README for a full example. Keep each id and platform unchanged when editing. Removing a feature here deletes its entity; bulk imports preserve omitted features.",
        "export": "Send a Home Assistant template result to a Crestron join when it changes or the processor reconnects. Unknown or unavailable results are skipped. Digital templates accept true/false or on/off; analog values are 0–65535.",
        "action": "Run actions when this Crestron join arrives. Templates in actions can use value, join, join_type and entry_id. Digital release is ignored by default.",
    }[kind]
    steps = {
        k: {"title": title, "description": description, "data": labels}
        for k in (
            ("user", "mapping", "reconfigure")
            if kind == "entity"
            else ("user", "reconfigure")
        )
    }
    if kind == "entity":
        steps["user"] = {
            "title": "Choose entity type",
            "data": {"platform": labels["platform"]},
        }
    strings["config_subentries"][kind] = {
        "entry_type": title,
        "initiate_flow": {"user": f"Add {title[0].lower()}{title[1:]}"},
        "step": steps,
        "error": errors,
        "abort": {"reconfigure_successful": "Mapping updated."},
    }
strings["options"] = {
    "step": {
        "init": {
            "title": "Bulk mapping configuration",
            "description": "Add or update mappings by stable key. The current inventory is shown below. Collections: entities, devices, exports and actions. Imports preserve omitted mappings and device features; delete mappings through their individual controls. Keep keys and entity feature IDs unchanged to retain entity identity. See the repository README for examples.",
            "data": {"configuration": "Mapping inventory"},
        }
    },
    "error": {
        "invalid_inventory": "The batch was not saved: {error}. Check the indicated mapping and try again."
    },
}
(root / "translations").mkdir(exist_ok=True)
for path in (root / "strings.json", root / "translations" / "en.json"):
    path.write_text(json.dumps(strings, indent=2) + "\n")
