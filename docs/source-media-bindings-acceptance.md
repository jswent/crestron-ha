# Source media bindings: v0.2.0b1 acceptance

This is the historical **0.2.0b1 prerelease** acceptance record. The owner
confirmed production is working on 2026-10-31 and requested continued prerelease
status. See [0.2.0b2 controls acceptance](source-media-controls-acceptance.md) for
the next iteration. Stable promotion requires a separate explicit request.

## Local evidence

- Baseline: 214 passing tests before the change.
- Final suite: 256 passing tests; 97% integration statement coverage and 100%
  media player statement coverage.
- Ruff lint, Ruff formatting, and `git diff --check`: passed.
- Home Assistant 2026.9.4 with the checkout loaded as a custom integration and
  the XSIG simulator connected over actual loopback TCP: passed all eight live
  acceptance groups in `tools/check_media_bindings_live.py`.
- A persistent `media_player.sonos_1` demo entity provides two tracks, artwork,
  and real HA playback actions. Demo audio source 1 binds to it; TV is unbound.
- API checks confirmed reachable artwork, playback and track changes, immediate
  clearing before source feedback, unchanged-feedback refreshes, late-update suppression, latched power
  cycles, unavailable-target recovery, live binding edits without a reconnect,
  and processor disconnect/reconnect behavior.

The live result is saved in the ignored dev config as
`source-binding-acceptance.json`. The automated suite also covers digital source
conflicts, either order of power/source confirmation, overlapping commands,
missing entities, target removal, unload cleanup, direct binding loops, field
removal, bad timestamps, and config/inventory validation.

## Production test

1. In HACS, open Crestron XSIG, choose **Redownload**, expand **Need a different
   version?**, and select `v0.2.0b1` under **Release**. Restart Home Assistant.
   Keep `v0.1.1` available for rollback. Do not remove/re-add the integration.
2. Reconfigure one existing Crestron audio zone. In **Source media player
   bindings**, add the source number and real entity ID, for example:

   ```yaml
   1: media_player.sonos_1
   ```

   Use your actual source number (or digital join) and actual Sonos entity ID.
   Leave another source unbound. Existing power/volume/mute/source joins stay
   as configured.
3. With the zone on and Sonos selected, play, pause, and change tracks on Sonos.
   Confirm the Crestron entity shows current state, title, artist, artwork, and
   position when the real source provides them. Confirm volume/mute still
   operate the Crestron zone independently of Sonos volume.
4. Switch to the unbound source using HA and then using a physical Crestron
   control. Confirm old Sonos details disappear immediately. Change the Sonos
   track while the other source is selected; no Sonos metadata should return.
5. Switch back and confirm current details return. Turn the zone off/on using
   HA and physical controls; confirm details clear when off and return only
   with the correct selected source. If a default source is configured, verify
   that selection as well.
6. Stop Sonos playback, then resume. Check that idle/off/unavailable states do
   not retain an old track on the Crestron entity. Remove and restore the
   binding live and confirm the entity ID and Crestron connection remain stable.
7. Report the tested HA version, zone/source and Sonos entity IDs, outcomes, and
   any stale fields or log errors. Owner-confirmed production success closes
   acceptance; stable promotion requires a separate explicit request.

Bindings only observe the source. Playback controls continue to belong to the
Sonos/source entity; the Crestron player retains its existing controls.

## Rollback

Clear the new bindings in the zone's reconfigure form, redownload `v0.1.1`
through HACS, and restart HA. There is no config-entry migration or entity ID
change in this prerelease.
