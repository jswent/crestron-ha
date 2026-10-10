# Migrating from hass-crestron-component

The old integration configures a TCP listener and entity platforms in YAML. Multiple processor slots require copying the component under new domains such as `crestron_audio`. In the new design, one installed `crestron` integration contains several named **bridges**, each with its own listener and independent join namespace. Entity mappings belong to a bridge and are maintained through the Home Assistant UI.

There is no automatic import. Your Crestron program and its join numbers stay as they are; the Home Assistant side is entered again, either through the forms or in bulk.

## Prepare an inventory

Keep a copy of the old YAML and component directories. Record each existing domain, listening port, intended bridge name, entity ID, and join mapping. A useful inventory looks like this:

| Old domain | Port | New bridge | Typical mappings |
| --- | --- | --- | --- |
| `crestron` | `8000` | House | Lighting, shades, thermostats |
| `crestron_audio` | `8001` | Audio | Speaker zones and sources |

One bridge represents a logical XSIG connection. Several bridges may connect to the same processor, and join `a1` on one bridge is unrelated to join `a1` on another.

## Replace the old component

Both integrations use the `crestron` domain, so they cannot be installed side by side in one Home Assistant instance. Installing this integration replaces the old `custom_components/crestron` folder.

1. Back up your Home Assistant configuration, including the old YAML and component folders.
2. Remove the `crestron:` block and every `platform: crestron` entry from your YAML configuration.
3. Install this integration as described in [the README](../README.md#installation) and restart Home Assistant.
4. Add one bridge for each old domain, reusing its port so the Crestron program does not need to change.

Copied domains such as `crestron_audio` are separate components and keep working until you remove their YAML and folders, so you can move them over one at a time. Do not run an old listener and a new bridge on the same port at the same time.

To rehearse the migration first, use a separate test instance of Home Assistant. The simulator described in [CONTRIBUTING.md](../CONTRIBUTING.md#simulate-a-processor) can stand in for the processor.

## Choose device boundaries and prepare a bulk import

Use a standalone entity when a mapping represents a signal with no useful physical-device grouping. Use one Physical device subentry for related features of the same product, such as a thermostat and its additional sensors. A one-entity light or shade can still be a device if that makes sense for the installation. A bridge is a logical connection and does not establish physical manufacturer/model metadata.

For hundreds of mappings, convert the inventory into the bridge Configure options' `entities`, `devices`, `exports`, and `actions` collections. [The README](../README.md#bulk-configuration) contains a complete example. Keep a persistent `key` for each record and an `id` for each device feature; do not derive new keys from a name each time you edit it. Retain generated keys when exporting existing UI-created mappings. Mapping names and joins may change; an existing entity's platform may not.

The import adds or updates keyed records after validating the entire batch. Records and device features omitted from a batch remain configured, so a partial import cannot accidentally remove the rest of a large installation. Omitting or emptying an existing device's `entities` list also preserves all of its features; a new device still needs at least one feature. Use the individual mapping's delete control to remove it. To remove only one feature from a device, remove its record in that device's Reconfigure form. Batch conversion is still deliberate: the importer does not directly understand old component YAML, copied integration domains, or legacy rule syntax.

## Upgrading from a pre-release build

This only applies if you ran a development build of this integration before version 0.1.0. Those builds created one device for every entity mapping. On upgrade, the existing entity mappings are marked internally with `legacy_device: true`. Their entity unique IDs and device identifiers do not change, keeping their registry metadata and device references intact. Previously created devices remain visible; newly created standalone mappings do not add devices.

Do not delete and recreate old mappings simply to reduce the device count. There is no automatic merge of existing devices into a newly grouped device; such a move needs an explicit migration to preserve inherited areas and device-based automation references. The migration flag is internal and is not part of the bulk import format.

## Preserve the Crestron program contract

The transport direction stays the same: Home Assistant listens and the Crestron TCP/IP Client connects to it. Keep the TCP/IP Client’s destination and port aligned with the selected bridge. The Crestron program still supplies XSIG digital, analog, and serial joins.

Retain the actual transmitted join numbers. On a mixed XSIG symbol, a digital signal’s transmitted number can reflect its position after analog/serial signals rather than the displayed digital label. Separating digital joins into their own XSIG symbol was the old project’s workaround; the migration does not renumber joins or require a new Crestron program.

Analog joins use unsigned 16-bit values (`0..65535`). A digital join holds a boolean state. Serial joins carry text subject to protocol encoding and length limits. Feedback and commands may share a join only when the Crestron program is wired for that behavior.

## Entity mapping checklist

The legacy field names below describe the behavior that must be carried across. The new UI’s labels may be more readable; compare the mapping values, not just the entity names.

| Platform | Legacy fields | Behavior to verify |
| --- | --- | --- |
| Light | `brightness_join` | Analog brightness maps to Home Assistant brightness; off writes zero. |
| Binary sensor | `is_on_join`, `device_class` | Digital feedback is read-only; the device class changes presentation. |
| Sensor | `value_join`, `divisor`, `device_class`, `unit_of_measurement` | Divide the raw analog value by the divisor; keep units consistent with the actual Crestron value. |
| Switch | `switch_join`, `pulsed` | Level-based switches write the requested state; pulse-based switches trigger the program and wait for feedback. |
| Cover | `pos_join`, `is_opening_join`, `is_closing_join`, `stop_join`, `is_closed_join` | Position `0` means closed and `65535` means open; stop pulses the configured digital join. |
| Climate | `heat_sp_join`, `cool_sp_join`, `reg_temp_join`; mode, fan, and relay joins | Preserve tenths-of-degree scaling, temperature units, and digital feedback for HVAC/fan/action state. |
| Media player | Power, mute, volume, and source joins | Preserve pulse behavior and analog volume scaling; use exactly one source-selection method. |

Sensor examples: divisor `10` converts `754` to `75.4`; divisor `655.35` converts full-scale analog feedback to percent. A zero divisor is invalid. Use Home Assistant units such as `°F` or `°C`, replacing legacy plain `F`/`C` sensor units where necessary. Climate temperatures need particular attention: the old implementation reported the Home Assistant unit system while dividing raw values by ten; a change of Home Assistant unit preference does not change the processor’s transmitted temperature scale.

For media players, preserve either `source_number_join` with the `sources` number-to-name map, or `source_digital_joins` with the digital-join-to-name map. Analog selection writes the source number. Digital selection pulses a source join and identifies the current source from high feedback. Copy `default_source` when used; `source_default` was an older compatibility spelling. Power-off and mute are not interchangeable with level writes: verify the actual programmed pulse semantics.

## Entity IDs and automations

Do not assume that equal friendly names preserve entity IDs. Most of the old platforms did not provide stable unique IDs; the new entities belong to Home Assistant’s entity registry and use their bridge/mapping identity. A copied domain such as `crestron_audio` is also no longer a separate installed integration.

After a mapping is validated, disable its old entity/listener, then use Home Assistant’s entity settings to assign the intended entity ID if it is available. Check dashboards, scripts, groups, scenes, templates, and automations that refer to the old ID. Keep a before/after inventory until migration is complete. Reconfiguring a mapping should preserve its new subentry identity; deleting it and creating another mapping is a different identity.

## Control-surface synchronization

The old `to_joins` section sends Home Assistant state or template results to digital, analog, or serial joins. The old `from_joins` section runs a Home Assistant script when a join changes and ignores digital falling edges. Preserve those semantics when moving control surfaces, including initial synchronization and synchronization after reconnect.

Inventory these rules separately from entities:

| Legacy rule | Information to retain |
| --- | --- |
| `to_joins` with `entity_id` | Bridge, typed join, entity ID, optional attribute, and value conversion. |
| `to_joins` with `value_template` | Bridge, typed join, complete template, and its referenced entities. |
| `from_joins` with `script` | Bridge, typed join, action sequence, and use of the incoming `value`. |

Convert these rules into the bridge's configuration subentries, either through individual forms or the bulk editor. **Export** mappings replace `to_joins`: provide `name`, `join_type`, numeric `join`, and `value_template`. The native template editor accepts Home Assistant templates. A legacy typed join such as `a35` becomes type `analog`, join `35`; `d12` becomes `digital`, `12`; `s4` becomes `serial`, `4`.

For an old direct entity mapping, use a template expressing the intended wire value:

```jinja2
{# Digital export: true when this switch is on #}
{{ is_state('switch.compressor', 'on') }}

{# Analog export: a 0..1 volume attribute becomes XSIG 0..65535 #}
{{ (state_attr('media_player.kitchen', 'volume_level') | float(0) * 65535) | round(0) | int }}

{# Serial export: include the current state in text #}
Current weather: {{ states('weather.home') }}
```

Use one template per mapping. Copy existing `value_template` rules when they already produce the desired wire value, correcting invalid old examples such as `state(...)` to Home Assistant's `states(...)` function. An export template uses normal Home Assistant template context; an incoming join `value` belongs to an action mapping, not an export.

**Action** mappings replace `from_joins`: provide `name`, `join_type`, numeric `join`, and `actions` using the native action editor. The incoming join value is available as `value` in action templates. Keep `ignore_release` enabled for momentary digital controls to preserve the old rising-edge behavior. A legacy rule for `a2` becomes an analog mapping for join 2 with an action sequence such as:

```yaml
- action: input_text.set_value
  target:
    entity_id: input_text.test
  data:
    value: "Master BR temperature is {{ value | float / 10 }}"
```

There is no automatic import of legacy YAML or copied component domains. Repeat this conversion for each bridge, then remove only the corresponding old rules after their replacements have been verified.

Alternatively, Home Assistant automations can listen for `crestron_join_changed` events. Their data contains `entry_id`, `join_type`, `join`, and `value`. Filter by `entry_id` as well as join number so bridges cannot accidentally trigger one another; filter digital releases when only button presses should run actions.

Raw bridge actions are `crestron.set_join`, `crestron.get_join`, `crestron.pulse_join`, and `crestron.request_update`. Each requires an `entry_id` identifying the bridge. The old `get_analog`/`get_digital` actions fired separate response events; `get_join` instead returns response data. Update callers to the new documented action schema instead of retaining the old event wait unchanged.

## Cutover verification

For each bridge, confirm connection, representative feedback, commands, and disconnection/reconnection. Exercise every device type actually used, including media source selection and thermostat units. Confirm that editing and deleting mappings takes effect without restarting Home Assistant or disconnecting the processor. Entity/device edits rebuild entity platforms and preserve running actions. Export/action edits rebuild the mapping runtime and cancel in-flight or queued inbound actions while retaining the connection. A deliberate bridge reload or a listener-address/port change still releases and reopens its listener; confirm that the processor reconnects cleanly.

When the corresponding mappings and synchronization rules are verified, remove the old platform YAML and copied component domains. Keep the original configuration backup until the new setup has completed a normal operating cycle. Only the real processor can verify the wiring and logic inside your Crestron program.

Incoming `value` variables are now typed: digital is a boolean, analog is an integer, and serial is a string. The old callback supplied strings for every join. Change comparisons such as `value == "1"` to `value` for digital joins, and compare analog values numerically. Jinja filters such as `value | float` continue to work.
