# Source media bindings: implementation and acceptance plan

Historical plan for `0.2.0b1`. On 2026-10-31 the owner confirmed production is
working and requested continued prerelease status. The next iteration is the
[0.2.0b2 controls plan](source-media-controls-plan.md); stable promotion now
requires a separate explicit request, even after successful production testing.

Branch: `codex/source-media-bindings`

Prerelease target: `0.2.0b1` / `v0.2.0b1`

## Behavior

Each audio zone can optionally map source numbers (analog mode) or source joins
(digital mode) to Home Assistant `media_player` entity IDs using
`source_bindings`. Unbound sources retain the existing behavior. Numbers, rather
than display names, keep bindings stable when a source is renamed.

Crestron feedback remains authoritative for power, selected source, volume,
mute, and availability. The selected bound player supplies playing, paused,
buffering, or idle status and media metadata, including artwork and position.
Bindings observe state only; transport controls are outside this feature.

Media details are cleared synchronously when a source or power command starts.
They cannot return from an old source update while processor confirmation is
pending. Source and power feedback select the current binding without retaining
old fields. Pending source/power-on commands request a refresh, allowing unchanged
non-zero XSIG feedback to confirm the current selection. Off, unknown power/source, conflicting digital source feedback,
disconnect, missing/unavailable targets, and target stop/off clear media details.
Only playing/paused/buffering targets contribute track metadata; idle contributes
status without a retained track. No arbitrary target attributes or capabilities
are copied. Crestron-to-Crestron bindings are ignored to prevent feedback loops.
Subscriptions are removed on unload and replaced on live mapping edits.

## Work sequence

1. Inspect schemas, entity lifecycle, simulator, and release workflow; establish
   the baseline test result and create the feature branch.
2. Add validation and UI/bulk configuration support, implement state observation
   and transition handling, and document the contract and examples.
3. Test analog and digital sources, immediate clearing before feedback, late
   updates, source/power ordering, reconnects, bad/missing targets, artwork,
   live edits, removal, and unchanged unbound behavior in real HA fixtures.
4. Add a persistent, controllable simulated Sonos player to the dev HA config.
   Start the local HA server and XSIG simulator and run an API-level acceptance
   check against both. Leave a repeatable example for manual inspection.
5. Run lint, formatting, the full test suite, and release validation. Publish
   the feature branch and a GitHub **prerelease** with matching version/tag;
   retain `0.1.1` as the stable release.
6. The owner will install the prerelease on production through HACS (confirmed
   2026-10-31). Retain the stable release as the rollback version. Bind a real
   source and confirm actual playback, artwork, source changes,
   power cycles, and unbound sources. **Production testing confirmed by the
   owner is the final success criterion.** No stable release or stable tag until
   that confirmation. Record any required fixes in another prerelease.

## Acceptance evidence

- Baseline: 214 tests passed.
- Automated tests: 256 passed, with 97% integration coverage and 100% statement
  coverage for the media player platform; Ruff lint/format and diff checks pass.
- Running local HA plus simulator: all eight acceptance groups passed on
  Home Assistant 2026.9.4. The persistent `media_player.sonos_1` fixture supports
  real play/pause/stop/track/power actions. Analog and digital selector behavior,
  unchanged feedback refreshes, both feedback orders, concurrent commands, malformed timestamps, direct loops,
  and config/bulk flows are additionally covered by automated tests.
- Prerelease: `v0.2.0b1`; publication follows GitHub CI validation. Stable remains
  `v0.1.1`, and this feature branch remains unmerged pending production testing.
- Production installation and owner-confirmed testing: confirmed working by the
  owner on 2026-10-31. Keep prerelease status per the owner's instruction.
