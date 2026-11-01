# Bound source controls: v0.2.0b2 acceptance

**Prerelease only.** The owner confirmed `0.2.0b1` is working in production and
requested write-through controls while retaining prerelease status. No stable
promotion or merge is authorized by successful testing alone.

## Validation

- 296 automated tests passed, including 40 new tests using loaded HA source
  entities and actual service dispatch. Coverage: 97% overall, 100% media player.
- Ruff lint, formatting, and diff checks passed.
- All nine live acceptance groups passed on Home Assistant 2026.9.4 with the
  persistent Sonos demo and XSIG simulator over actual TCP.
- Commands from the Crestron player reached the selected demo source for
  play/pause, stop, next/previous, seek, shuffle, repeat, queue clearing, and
  play-media. Tests verified rejection while a switch was pending, TV was
  selected, the zone was off, Sonos was unavailable, or the binding was removed.
- Regression tests additionally cover two bound sources, analog and digital
  selectors, capability changes, missing/unloaded/restored entities, operation
  failures, fresh/unchanged report recovery, late failures from old sources,
  original caller context, argument validation, indirect loops, and cleanup.
  A pending command's late failure cannot overwrite a reconfigured entity.

## Production test

1. In HACS, choose Crestron XSIG → **Redownload → Need a different version? →
   Release → v0.2.0b2**, then restart HA. Existing bindings automatically enable
   supported playback controls; no remapping or config migration is needed.
2. With the zone on and the bound Sonos source selected, use the **Crestron media
   player** to play/pause, stop, change tracks, and seek. Try shuffle/repeat and
   play-media if Sonos advertises them. Confirm the actual Sonos player responds
   and both entities show the resulting state.
3. Confirm Crestron volume, mute, power, and source selection still operate the
   zone. Playback changes affect the shared source, so other zones listening to
   that same Sonos player hear the same change.
4. Switch to an unbound source using HA and the physical panel. Confirm playback
   controls disappear immediately and a direct HA playback service call against
   the Crestron entity is rejected without affecting Sonos.
5. Switch back, then test zone power off/on. Controls return only when power and
   the source are confirmed. If another bound source exists, confirm actions
   route only to the selected source and follow its supported capabilities.
6. When practical, make the selected source unavailable and confirm its playback
   controls disappear while Crestron zone controls remain usable. Restore it and
   confirm controls recover. Source availability comes from its owning HA
   integration; there is no separate Crestron-to-Sonos connection probe.
7. Report any control that remains visible when unusable, wrong-source routing,
   errors, or delayed recovery. Production acceptance for `0.2.0b2` is pending.

Remain a prerelease even after this test passes, until the owner explicitly
requests promotion.

## Rollback

Redownload `v0.2.0b1` through HACS and restart HA to retain read-only source
metadata with the same bindings and entity IDs.
