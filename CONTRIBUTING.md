# Contributing

Issues and pull requests are welcome. This guide covers the local development setup. You do not need Crestron hardware or an existing Home Assistant installation: the tests and the sandbox use a bundled XSIG simulator.

## Set up

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then from the repository root:

```sh
uv sync --locked
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

The lockfile pins Home Assistant and the matching `pytest-homeassistant-custom-component` release. Upgrade the two together.

CI runs the same commands, plus Home Assistant's hassfest validator and the HACS validation action.

## Run a local Home Assistant

```sh
uv run python tools/dev.py
```

For source media binding work, use `uv run python tools/dev.py --media-demo`.
This adds `media_player.sonos_1`, a development-only player with two sample
tracks, artwork, and working play, pause, stop, next, previous, and power actions.
The demo audio mapping binds source 1 to it and leaves TV unbound. The player is
loaded after each dev HA restart; it does not need Sonos hardware. It is not
included in the production `custom_components/crestron` integration.

Open http://127.0.0.1:8124 and complete onboarding. The instance keeps its configuration in `dev/config/`, which Git ignores, and loads the integration straight from this checkout. It listens on loopback only. Stop it with Ctrl-C.

Changes to Python source need an integration reload or a restart of this process. Mapping edits apply on their own.

## Simulate a processor

First create a bridge in the local instance. Either seed a demo bridge with eight sample entities, or add one by hand.

To seed it, create a long-lived access token in the local instance's user profile, export it as `HA_TOKEN`, and run:

```sh
uv run python tools/seed_demo.py
```

The seeder only contacts `127.0.0.1:8124`. It creates a bridge named Demo bridge on `127.0.0.1:16384` and will not create a second one.

To add one by hand, add **Crestron XSIG** under Settings → Devices & services with the listen address `127.0.0.1` and port `16384`. `dev/entities.json` holds the sample mappings.

Then start the simulator:

```sh
uv run python tools/xsig_simulator.py --host 127.0.0.1 --port 16384 --scenario dev/scenario.json --interactive
```

The simulator connects to the bridge, seeds the non-zero values in the scenario file, and echoes commands back as feedback. Like a real processor, it leaves low, zero, and empty joins out of that refresh. Its interactive console accepts:

```text
analog 1 65535
digital 1 0
serial 1 A simulated message
sync
reconnect
quit
```

Useful options:

- `--no-echo` checks behavior when the processor sends no feedback.
- `--all-joins` seeds every legal join with a non-zero value.
- Scenario files can redirect feedback to another join and schedule timed changes and disconnects. The format is documented at the top of `tools/xsig_simulator.py`.

Run several simulators on different ports to exercise several bridges. The echo is generic: pulsed commands release to false, so latched behavior such as a thermostat mode needs feedback injected from the console or a scenario.

The demo scenario uses `pulse_feedback` to latch audio power: join 21 sets power
feedback high and join 22 sets it low; button releases do not change power.

For a repeatable source binding acceptance run, start HA with `--media-demo`,
stop any separate simulator, set the local `HA_TOKEN`, and run:

```sh
uv run python tools/check_media_bindings_live.py
```

The check seeds the Demo bridge if needed and runs its own TCP simulator. It
checks playback/artwork, pause/stop/track changes, delayed source confirmation,
late updates, unbound TV, latched power cycles, an unavailable target, live
binding edits, and reconnects through HA's real APIs. Results are saved to
`dev/config/source-binding-acceptance.json`. Start the interactive simulator
again afterward to inspect the example in the UI.

## Debug in VS Code

`.vscode/launch.json` has **Home Assistant dev** and **XSIG dummy** configurations. Run `uv run python tools/dev.py --prepare-only` once and select the `.venv` interpreter first.

## Layout

| Path | Contents |
| --- | --- |
| `custom_components/crestron/` | The integration: config flows, entity platforms, bridge runtime, XSIG transport, diagnostics. |
| `tests/` | Protocol vectors, loopback TCP peers, and Home Assistant flow, entity, action, and lifecycle tests. |
| `tools/` | Local launcher, demo seeder, simulator, and translation generator. |
| `dev/` | Demo mappings and simulator scenario. `dev/config/` is the ignored sandbox configuration. |
| `docs/` | User guides. |

## Guidelines

- **Test against the real thing.** Use Home Assistant fixtures for integration behavior and real loopback TCP peers for wire and lifecycle behavior. Tests must not connect to physical control systems.
- **One bridge per config entry.** Mappings are subentries of their bridge, and runtime objects live in `entry.runtime_data`.
- **Feedback is the source of truth.** Keep processor feedback separate from outbound commands. Entities do not update optimistically. A processor refresh carries non-zero signals only, so an unreported digital or analog join reads as zero once the refresh has settled.
- **Keep entity identity stable.** Editing a name, join, or port must not replace an entity.
- **Clean up everything.** Sockets, subscriptions, script runs, and pending work must be released on every unload, failure, and cancellation path.
- **Keep payloads out of logs and diagnostics.** No serial contents, template bodies, action sequences, peer addresses, or join values. The `get_join` action and `crestron_join_changed` events are the interfaces for inspecting values.
- **Keep everything aligned.** Source, tests, docs, English translations, and `services.yaml` change together. After editing the form schemas, regenerate translations:

  ```sh
  uv run python tools/write_translations.py
  ```

## Releases

1. Update `version` in `custom_components/crestron/manifest.json` and `pyproject.toml`, then run `uv lock`.
2. Publish a GitHub release with a matching tag, such as `v0.1.0`. HACS offers published releases to users; a tag without a release is not enough.
