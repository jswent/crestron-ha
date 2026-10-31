# Crestron XSIG for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Validate](https://github.com/jswent/crestron-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/jswent/crestron-ha/actions/workflows/validate.yml)
[![Tests](https://github.com/jswent/crestron-ha/actions/workflows/tests.yml/badge.svg)](https://github.com/jswent/crestron-ha/actions/workflows/tests.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Control and monitor a Crestron control system from Home Assistant over a local TCP connection. Your Crestron program exposes digital, analog, and serial joins through an **Intersystem Communications (XSIG)** symbol; this integration maps those joins to Home Assistant lights, shades, thermostats, audio zones, switches, and sensors.

Everything is configured in the Home Assistant UI. There is no YAML to maintain and no restart when you add or change a mapping.

## Features

- **Seven entity types:** light, cover, climate, media player, switch, sensor, and binary sensor.
- **Multiple bridges:** connect several processors or program slots, each with its own port and its own join numbering.
- **Physical devices:** group related entities, such as a thermostat and its extra sensors, under one Home Assistant device.
- **Two-way sync with touchpanels and keypads:** send any Home Assistant state or template to a join, and run Home Assistant actions when a join changes.
- **Live editing:** add, change, and remove mappings without restarting Home Assistant or dropping the processor connection.
- **Bulk configuration:** add or update hundreds of mappings at once from a single YAML document.
- **Honest state:** entities show what the processor reports, become unavailable when it disconnects, and never assume a command worked.
- **Local only:** no cloud service and no extra Python dependencies.

## How it works

Home Assistant listens on a TCP port and the Crestron processor connects to it. Each listener is called a **bridge**. A bridge has its own port and its own join numbers, so join 1 on one bridge has nothing to do with join 1 on another. One processor connects to a bridge at a time.

XSIG carries join numbers and values only. It does not describe what is wired to them, so the integration cannot discover your devices. You tell it which joins belong to which entity.

## Requirements

- Home Assistant 2026.9.4 or newer.
- A Crestron processor whose program you can edit to add a TCP/IP Client and an Intersystem Communications symbol.
- Network access from the processor to Home Assistant on the port you choose (16384 by default).

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jswent&repository=crestron-ha&category=integration)

1. In Home Assistant, open **HACS**, select the three-dot menu in the top right, and choose **Custom repositories**.
2. Enter `https://github.com/jswent/crestron-ha`, choose the type **Integration**, and select **Add**.
3. Search HACS for **Crestron XSIG** and select **Download**.
4. Restart Home Assistant.

### Manual

1. Download this repository.
2. Copy the `custom_components/crestron` folder into the `custom_components` folder of your Home Assistant configuration directory.
3. Restart Home Assistant.

> **Already using `hass-crestron-component`?** This integration uses the same `crestron` domain and replaces it. Old YAML configuration is not imported. Read the [migration guide](docs/migration.md) before installing.

## Set up the Crestron program

1. Add a **TCP/IP Client** device to the control system. Set its IP address to your Home Assistant machine and its port to the bridge's listen port.
2. Hold the client's **Connect** signal high (`1`) to stay connected, or drive it from your own logic.
3. Add an **Intersystem Communications** symbol (quick key `xsig`) and connect its TX and RX to the TCP/IP Client.
4. Attach the digital, analog, and serial signals you want to share with Home Assistant to the symbol's joins.

You can attach several XSIG symbols to one TCP/IP Client.

> **Digital join numbers on a mixed symbol.** When one XSIG symbol carries both analog/serial and digital signals, digital joins are sent by their position in the whole signal list, not by the number shown on the symbol. With 25 analog signals followed by 10 digital signals, the digitals are labeled 1–10 but arrive as joins 26–35. Either enter the offset numbers in Home Assistant, or use one XSIG symbol for digital signals and another for analog and serial signals.

## Set up Home Assistant

### Add a bridge

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=crestron)

1. Go to **Settings → Devices & services → Add integration** and choose **Crestron XSIG**.
2. Enter a **name** for the bridge, such as House or Audio.
3. Set the **listen address**. This is an IP address of the Home Assistant machine, not the processor. The default `0.0.0.0` listens on every IPv4 interface.
4. Set the **listen port**. Every bridge needs its own port.

The processor does not need to be connected to save a bridge. Repeat these steps to add more bridges.

### Add entities

On the integration page, choose **Add standalone entity** on your bridge, pick the entity type, and enter the join numbers from your Crestron program. See the [entity reference](#entity-reference) for every field.

A standalone entity is a single signal with no device behind it. You can still assign it to an area.

### Group entities into a physical device

Choose **Add physical device** when several entities belong to one product. Give the device a name, optionally a manufacturer and model, and list its entities in **Entity features**:

```yaml
- id: power
  platform: switch
  name: Power
  switch_join: 30
- id: temperature
  platform: sensor
  name: Temperature
  value_join: 21
  divisor: 10
  device_class: temperature
  unit_of_measurement: "°F"
```

Each feature needs an `id`, a `platform`, a `name`, and the join fields for that platform. Keep the `id` and `platform` the same when you edit a feature later, so Home Assistant keeps the same entity.

### Send Home Assistant state to Crestron

Choose **Add export to Crestron** to send the result of a [template](https://www.home-assistant.io/docs/configuration/templating/) to a join. The value is sent whenever the result changes and again each time the processor connects.

| Join type | Template result |
| --- | --- |
| Digital | `true`/`false` or `on`/`off` |
| Analog | A whole number from 0 to 65535 |
| Serial | Text, up to 252 bytes of UTF-8 |

```jinja2
{# Digital: high while the porch light is on #}
{{ is_state('light.porch', 'on') }}

{# Analog: a 0–1 volume level scaled to 0–65535 #}
{{ (state_attr('media_player.kitchen', 'volume_level') | float(0) * 65535) | round(0) | int }}

{# Serial: text for a touchpanel #}
Outside: {{ states('sensor.outdoor_temperature') }}°
```

Results of `unknown`, `unavailable`, or `None` are not sent. Each join on a bridge can have one export.

### Run Home Assistant actions from Crestron

Choose **Add action from Crestron** to run a sequence of actions when a join arrives. This is how a keypad button or touchpanel slider controls something in Home Assistant.

The actions can use these template variables:

| Variable | Contents |
| --- | --- |
| `value` | `true`/`false` for digital, a number for analog, text for serial |
| `join` | The join number |
| `join_type` | `digital`, `analog`, or `serial` |
| `entry_id` | The ID of the bridge that received the join |

**Ignore digital release** is on by default, so a button press runs the actions once when the join goes high and not again when it goes low.

### Edit and remove

- Use **Reconfigure** on a bridge or mapping to change it. Names and join numbers can change freely without replacing the entity.
- Use **Delete** on a mapping to remove it and its entity.
- To remove one feature from a physical device, reconfigure the device and delete that feature from the list.

Mapping changes apply immediately and keep the processor connected. Changing a bridge's listen address or port reconnects it. Editing an export or action mapping cancels any actions from Crestron that are still running on that bridge.

## Entity reference

Digital joins range from 1 to 4096. Analog and serial joins range from 1 to 1024. Analog values are 0 to 65535. Unless noted, a join is used for both the command and its feedback.

### Light

A dimmable light.

| Field | Join | Required | Description |
| --- | --- | --- | --- |
| `brightness_join` | Analog | Yes | Brightness. 0 is off and 65535 is full. |
| `brightness_default` | | No | Brightness from 1 to 255 used when the light is turned on without a level. Defaults to 230. |

### Switch

| Field | Join | Required | Description |
| --- | --- | --- | --- |
| `switch_join` | Digital | Yes | The switch state. |
| `pulsed` | | No | Off (default): Home Assistant holds the join high or low. On: Home Assistant pulses the join to toggle, and only when the feedback differs from the requested state. |
| `device_class` | | No | `switch` (default) or `outlet`. |

### Binary sensor

| Field | Join | Required | Description |
| --- | --- | --- | --- |
| `is_on_join` | Digital | Yes | The sensor state. |
| `device_class` | | No | A Home Assistant [binary sensor device class](https://www.home-assistant.io/integrations/binary_sensor/#device-class), such as `door`, `motion`, or `occupancy`. |

### Sensor

| Field | Join | Required | Description |
| --- | --- | --- | --- |
| `value_join` | Analog or serial | Yes | The value to report. |
| `join_type` | | No | `analog` (default) or `serial`. A serial sensor reports text. |
| `divisor` | | No | The analog value is divided by this number. Use `10` when the program sends tenths, so 754 becomes 75.4. Defaults to 1. |
| `device_class` | | No | A numeric Home Assistant [sensor device class](https://www.home-assistant.io/integrations/sensor/#device-class), such as `temperature` or `humidity`. |
| `unit_of_measurement` | | No | The unit, such as `°F` or `%`. When a device class is set, the unit must be one Home Assistant accepts for that class. |

Serial sensors cannot have a device class or unit.

### Cover

A shade, blind, or other cover with position control.

| Field | Join | Required | Description |
| --- | --- | --- | --- |
| `pos_join` | Analog | Yes | Position. 0 is closed and 65535 is fully open. |
| `stop_join` | Digital | Yes | Pulsed to stop movement. |
| `is_opening_join` | Digital | No | High while the cover is opening. |
| `is_closing_join` | Digital | No | High while the cover is closing. |
| `is_closed_join` | Digital | No | High when the cover is closed. Without it, a position of 0 means closed. |
| `type` | | No | `shade` (default), `blind`, `curtain`, `shutter`, `window`, `door`, `garage`, `gate`, `awning`, or `damper`. |

### Climate

A thermostat with heat, cool, auto, and off modes and a fan setting. Temperatures are sent in tenths of a degree, so 72.5° is 725. Mode and fan joins are pulsed to select that mode and read high while it is active.

| Field | Join | Required | Description |
| --- | --- | --- | --- |
| `heat_sp_join` | Analog | Yes | Heating setpoint. |
| `cool_sp_join` | Analog | Yes | Cooling setpoint. |
| `reg_temp_join` | Analog | Yes | Current room temperature. |
| `mode_heat_join` | Digital | Yes | Heat mode. |
| `mode_cool_join` | Digital | Yes | Cool mode. |
| `mode_auto_join` | Digital | Yes | Auto (heat/cool) mode. |
| `mode_off_join` | Digital | Yes | Off. |
| `fan_on_join` | Digital | Yes | Fan on. |
| `fan_auto_join` | Digital | Yes | Fan auto. |
| `h1_join`, `h2_join` | Digital | No | High while heating stage 1 or 2 is running. |
| `c1_join`, `c2_join` | Digital | No | High while cooling stage 1 or 2 is running. |
| `fa_join` | Digital | No | High while the fan is running. |
| `temperature_unit` | | No | `F` (default) or `C`. This is the unit your Crestron program sends. |

The optional stage and fan joins let Home Assistant show whether the system is heating, cooling, or idle. Setpoints are limited to 44.6–95 °F (7–35 °C).

### Media player

An audio zone with power, volume, mute, and source selection.

| Field | Join | Required | Description |
| --- | --- | --- | --- |
| `power_on_join` | Digital | Yes | Pulsed to turn the zone on. Its feedback is the zone's power state. |
| `power_off_join` | Digital | Yes | Pulsed to turn the zone off. |
| `mute_join` | Digital | Yes | Pulsed to toggle mute. Its feedback is the mute state. |
| `volume_join` | Analog | Yes | Volume. 0 is silent and 65535 is full. |
| `source_number_join` | Analog | See below | The number of the selected source. |
| `sources` | | See below | Source numbers and names, such as `{"1": "Music", "2": "TV"}`. |
| `source_digital_joins` | | See below | Digital joins and source names, such as `{"51": "Music", "52": "TV"}`. |
| `default_source` | | No | The source number or join to select when the zone turns on. |
| `source_bindings` | | No | Source numbers or digital source joins mapped to another `media_player` entity. Supplies media details and supported playback controls for the selected, available source. |

Choose one way to select sources:

- **By number:** set `source_number_join` and `sources`. Home Assistant writes the source number to the analog join.
- **By digital join:** set `source_digital_joins`. Home Assistant pulses the join for the chosen source, and the join that reads high is the current source.

#### Show media from a source player

Optionally enter **Source media player bindings** when adding or reconfiguring an
audio zone. Use the same numbers as your source map, with one entity ID per
bound source. For example, with sources `1: Sonos 1` and `2: TV`:

```yaml
source_bindings:
  "1": media_player.sonos_1
```

In the standalone form, enter just the map (`1: media_player.sonos_1`) in the
bindings field. In physical-device features or bulk configuration, include the
`source_bindings` key as shown above. For digital sources use the digital join
number, for example `51: media_player.sonos_1`. Omit TV if it has no binding.
Several zones can observe the same source player. Renaming a source preserves
its binding because the number is the key. Clear the field or use an empty map
to remove bindings during reconfiguration.

When the powered-on zone selects Sonos 1, it follows that player's playing,
paused, buffering, or idle status. Playing, paused, and buffering players supply
their current title, artist, album, artwork, duration, position, and other media
details. Updates arrive automatically; editing bindings takes effect immediately.
Crestron still controls power, source, volume, and mute.

Starting with `0.2.0b2`, the selected binding also enables the source player's
supported playback controls: play/pause, stop, previous/next track, seek,
shuffle, repeat, clear playlist, and play-media. Play-media forwards enqueue and
announcement options only when the source supports them. Grouping, sound modes,
media browsing, and search remain on the source entity. Several zones selecting
the same player control that shared player's playback.

Playback controls require a connected, powered-on Crestron zone with confirmed,
unambiguous source feedback and a loaded, available source entity with a known
state. They disappear immediately during source/power changes and when the
source is missing, unknown, unavailable, or unbound. An idle or off source may
still expose Play if its integration reports it available and supports Play.
The source integration manages and reports its physical connection; Crestron
does not open a second connection to the device or assume a retained HA state
means it is reachable.

Each command rechecks the active binding and capabilities before forwarding
through Home Assistant. Commands are never queued for a later selection, and
state still follows source feedback. A failed operation hides playback controls
until a fresh source state change or report allows recovery. Invalid arguments
are reported without treating them as a connection failure. A command already
dispatched belongs to its original source even if selection changes afterward.

Media details disappear immediately when a source or power command starts and
stay hidden until the processor confirms it. Power-on with a default source
requires both power and source feedback, in either order. A command without
feedback leaves the details hidden; pending source/power-on commands request a
processor refresh so an unchanged selection can still be confirmed. Late updates
from the old player cannot bring them back. External source/power changes and
reconnects use current processor feedback and a fresh snapshot of the selected player. Source conflicts,
an unbound/unknown source, a missing/off/unavailable player, and a disconnected
or powered-off zone clear media details. An idle player supplies idle status
without retaining its previous track. Missing fields in a new track replace the
previous values with empty fields.

Bindings to this or another Crestron media player are ignored to prevent direct
feedback loops. Use the original source player, such as the Sonos integration's
entity. A bound player's failure does not disable the zone's Crestron controls.

## Bulk configuration

For large installations, select **Configure** on a bridge. The editor shows everything currently configured on that bridge as one document with four lists: `entities`, `devices`, `exports`, and `actions`. Edit it, or paste in records, and submit.

```yaml
entities:
  - key: outdoor_temperature
    platform: sensor
    name: Outdoor temperature
    value_join: 20
    divisor: 10
    device_class: temperature
    unit_of_measurement: "°F"
devices:
  - key: patio_heater
    name: Patio heater
    entities:
      - id: power
        platform: switch
        name: Power
        switch_join: 30
      - id: temperature
        platform: sensor
        name: Temperature
        value_join: 21
        divisor: 10
        device_class: temperature
        unit_of_measurement: "°F"
exports:
  - key: porch_light_feedback
    name: Porch light feedback
    join_type: digital
    join: 40
    value_template: "{{ is_state('light.porch', 'on') }}"
actions:
  - key: porch_light_button
    name: Porch light button
    join_type: digital
    join: 41
    ignore_release: true
    actions:
      - action: light.toggle
        target:
          entity_id: light.porch
```

These join numbers are examples. Use the ones from your own program. A device can also have an optional `manufacturer` and `model`.

How it behaves:

- **Records are matched by `key`.** A record with an existing key updates that mapping. A new key adds a mapping. Device features are matched by `id` in the same way.
- **Nothing is deleted by leaving it out.** Records and device features that you omit stay configured. Delete them with their own **Delete** or **Reconfigure** controls.
- **The whole document is checked first.** If any record is invalid, nothing is saved and the error names the record.
- **Keep keys and IDs stable.** A key or ID is 1 to 100 letters, digits, underscores, or hyphens, starting with a letter or digit. Changing one creates a new mapping instead of updating the old one. An existing key or ID cannot change platform. Mappings created through the forms have a generated key; leave it as it is.

Copy the document out of the editor to keep a backup of a bridge's mappings.

## Actions and events

These actions are available under **Developer tools → Actions** and in automations and scripts. Each one targets a single bridge.

| Action | Description |
| --- | --- |
| `crestron.set_join` | Send a digital, analog, or serial value to a join. |
| `crestron.get_join` | Return the current feedback value of a join. |
| `crestron.pulse_join` | Set a digital join high, then low again. |
| `crestron.request_update` | Ask the processor to resend its current non-zero values. |

```yaml
action: crestron.set_join
data:
  entry_id: YOUR_BRIDGE_ENTRY_ID
  join_type: analog
  join: 12
  value: 32768
```

`pulse_join` takes an optional `seconds` from 0.01 to 5 and defaults to 0.05. `get_join` responds with `{"value": ..., "available": ...}`, where `value` is `null` if the processor has not reported that join since it connected.

To find a bridge's `entry_id`, pick the bridge in the action's visual editor and then switch to YAML mode.

Every join the processor sends also fires a `crestron_join_changed` event with `entry_id`, `join_type`, `join`, and `value`. You can trigger automations from it:

```yaml
triggers:
  - trigger: event
    event_type: crestron_join_changed
    event_data:
      entry_id: YOUR_BRIDGE_ENTRY_ID
      join_type: digital
      join: 41
      value: true
```

Include `entry_id` so that the same join number on another bridge does not trigger the automation.

## How state works

- **Unavailable** means no processor is connected to the bridge.
- **Unknown** is brief. Each time the processor connects, Home Assistant asks for its current values, and the processor answers with only the signals that are not zero. About a second later, every digital or analog join it did not send reads as off or 0. A text (serial) join stays unknown until the processor sends it.
- **State follows feedback.** After a command, an entity changes only when the processor reports the new value. If nothing in your Crestron program feeds the join back, the entity will not update.
- **Pulsed switches and mute follow feedback.** Home Assistant pulses them only when the feedback differs from the requested state. A join with no feedback reads as off, so turning it on always pulses and turning it off does nothing. During the first second after the processor connects, the state is unknown and Home Assistant will not toggle them.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| The bridge fails to start | The listen port is used by another program, or the listen address is not an address of the Home Assistant machine. Use `0.0.0.0` and a free port. |
| Entities stay unavailable | The processor is not connected. Check the TCP/IP Client's IP address, port, and Connect signal, and any firewall in between. If Home Assistant runs in a container without host networking, publish the bridge port. |
| An entity stays off or at 0 | The processor is connected but is not sending that join. Check the join number and type, and that your Crestron program feeds the signal back. |
| A text sensor stays unknown | The processor resends a serial signal only when it changes, unless the program makes the string permanent. |
| Digital joins are off by a fixed amount | The XSIG symbol mixes digital signals with analog or serial ones. See [Set up the Crestron program](#set-up-the-crestron-program). |
| A second processor cannot connect | A bridge accepts one connection at a time. Add another bridge on a different port. |

To see what a join is doing, listen for `crestron_join_changed` under **Developer tools → Events**, or call `crestron.get_join`.

For more detail, open the integration's menu and choose **Enable debug logging** or **Download diagnostics**. The integration's own logs and diagnostics leave out join values, template text, action contents, and the processor's address. `crestron_join_changed` events do contain values, and Home Assistant's own debug logs for templates and events can as well, so check those before sharing them.

Found a bug or have a question? [Open an issue](https://github.com/jswent/crestron-ha/issues).

## Migrating from hass-crestron-component

This integration replaces the YAML-configured [`hass-crestron-component`](https://github.com/npope/home-assistant-crestron-component). Your Crestron program and join numbers stay the same, but the Home Assistant side is set up again through the UI. The [migration guide](docs/migration.md) covers converting copied domains into bridges, moving `to_joins` and `from_joins` rules, and keeping entity IDs.

## Security

XSIG is an unencrypted, unauthenticated protocol. Anyone who can reach a bridge's port can read and send joins. Keep bridges on a trusted control network and do not expose their ports to the internet.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the local Home Assistant sandbox, and the XSIG simulator.

## License and credits

Released under the [Apache License 2.0](LICENSE). This integration builds on the original [`hass-crestron-component`](https://github.com/npope/home-assistant-crestron-component) by npope and jswent.

This project is not affiliated with or endorsed by Crestron Electronics, Inc. Crestron is a trademark of its owner.
