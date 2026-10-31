#!/usr/bin/env python3
"""Prepare a disposable config and run local Home Assistant on localhost:8124."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare_config(directory: Path, *, media_demo: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "custom_components"
    source = ROOT / "custom_components"
    if target.is_symlink():
        if target.resolve() != source.resolve():
            raise SystemExit(
                f"Refusing to replace existing custom_components at {target}"
            )
        target.unlink()
    target.mkdir(exist_ok=True)
    # Make this a regular package so the repository's package cannot shadow it.
    (target / "__init__.py").touch(exist_ok=True)
    components = {"crestron": source / "crestron"}
    if media_demo:
        components["crestron_media_demo"] = ROOT / "dev" / "crestron_media_demo"
    for name, path in components.items():
        link = target / name
        if not link.exists():
            link.symlink_to(path, target_is_directory=True)
        elif link.resolve() != path.resolve():
            raise SystemExit(f"Refusing to replace existing component at {link}")
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
    if media_demo:
        # A separate included platform avoids changing existing media players.
        import yaml

        document = yaml.safe_load(config.read_text())
        players = document.get("media_player", [])
        if not isinstance(players, list):
            raise SystemExit(
                "Add the crestron_media_demo platform to your media_player include manually"
            )
        if not any(
            player.get("platform") == "crestron_media_demo" for player in players
        ):
            document["media_player"] = [*players, {"platform": "crestron_media_demo"}]
            config.write_text(yaml.safe_dump(document, sort_keys=False))
        (directory / "www").mkdir(exist_ok=True)
        (directory / "www" / "crestron-demo-album.svg").write_text(
            (ROOT / "dev" / "crestron-demo-album.svg").read_text()
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "dev" / "config")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--media-demo",
        action="store_true",
        help="Add a controllable Sonos 1 demo media player",
    )
    args = parser.parse_args()
    directory = args.config.resolve()
    prepare_config(directory, media_demo=args.media_demo)
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
