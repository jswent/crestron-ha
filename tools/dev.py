#!/usr/bin/env python3
"""Prepare a disposable config and run local Home Assistant on localhost:8124."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare_config(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "custom_components"
    source = ROOT / "custom_components"
    if not target.exists():
        target.symlink_to(source, target_is_directory=True)
    elif target.resolve() != source.resolve():
        raise SystemExit(f"Refusing to replace existing custom_components at {target}")
    config = directory / "configuration.yaml"
    if not config.exists():
        config.write_text("""homeassistant:
  name: Crestron development
  latitude: 0
  longitude: 0
  elevation: 0
  unit_system: metric
  time_zone: America/New_York
http:
  server_host: 127.0.0.1
  server_port: 8124
frontend:
config:
api:
logger:
  default: warning
  logs:
    custom_components.crestron: debug
automation: []
script: {}
""")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "dev" / "config")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    directory = args.config.resolve()
    prepare_config(directory)
    print(f"Development config: {directory}\nUI: http://127.0.0.1:8124", flush=True)
    if not args.prepare_only:
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-m",
                "homeassistant",
                "--config",
                str(directory),
                "--skip-pip",
            ],
        )


if __name__ == "__main__":
    main()
