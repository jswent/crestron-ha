# Bound source browser: v0.2.0b3 acceptance

This remains a **prerelease**. The owner installs via HACS and performs production
acceptance. Stable promotion requires a separate explicit request.

## Validation

- 333 tests passed, including 37 new browse tests. Statement coverage: 97%
  overall, 100% media player. Ruff lint/format and diff checks passed.
- All 11 live acceptance groups passed with Home Assistant 2026.9.4, the XSIG
  TCP simulator, and two loaded Sonos demo players. Browser checks use HA's real
  WebSocket API and source artwork proxy HTTP endpoint.
- Verified root and nested catalogs, playable albums and tracks, exact provider
  content types/IDs (including Sonos favorite IDs), legacy dictionary results,
  artwork, immutable cached catalogs, and saved selections after entity reload.
- Verified unavailable/unbound/unsupported sources, read-only catalogs without
  Play Media, in-flight source/power/connection changes, provider replacement,
  browse failures, malformed scoped IDs, and indirect loops.
- Native HA media-source IDs are passed through unchanged so the provider owns
  resolution and the frontend retains TTS/media-source UI behavior. Provider-
  specific search is not forwarded; its search affordances are hidden.

Research used the supplied core, frontend, and documentation checkouts, plus the
project's pinned HA installation. See [the plan](source-media-browse-plan.md) for
the researched commits and interface decisions. Those checkouts were not edited.

## Production test

1. In HACS, open Crestron XSIG → **Redownload → Need a different version? →
   Release → v0.2.0b3**, then restart HA. Existing bindings need no changes.
2. Turn the Crestron zone on and select its Sonos source. Open **Media** and
   select the Crestron zone. Confirm Sonos's available folders/favorites appear.
3. Open a folder or album, confirm artwork renders, and choose a track or
   favorite. Confirm the actual Sonos entity plays the requested item and the
   Crestron entity follows its media information. Test album playback where
   the source marks an album playable.
4. Keep the browser open and switch to an unbound input, then to another bound
   source if available. Browsing must become unavailable for the unbound input;
   provider-specific items from the old source must not play on a different
   provider. Reopen browsing and select an item from the new provider instead.
5. Test power off/on and a temporarily unavailable source. Browse should only
   return after the zone/source are confirmed and the provider is available.
   A bound player that lacks browse support must not expose Browse Media.
6. If used, test local HA media/TTS via the source's browser. Native global media
   IDs intentionally route to the current eligible source. Save a provider item
   in a script with the media picker and test it again after an HA restart while
   its originating provider is selected. Keep the provider entity ID unchanged.
7. Report the tested provider/zone, browsing and playback outcomes, and any
   missing artwork or provider-specific errors. Production acceptance is pending.

## Rollback

Redownload `v0.2.0b2` and restart HA to retain source metadata and guarded playback
controls. Bindings and entity IDs are unchanged. Provider-specific selections
saved as `crestron-source://v1/...` require b3 or newer; on rollback, pick the item
directly from the original source entity again or use its original content ID.
