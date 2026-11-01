# Bound source browsing: 0.2.0b3

Continue `codex/source-media-bindings` and draft PR #1. Publish only a prerelease;
the owner installs through HACS and performs production acceptance. Stable
promotion still requires an explicit request.

## Research

Read the supplied local repositories without changing them:

- Core `56d268c2a67`: media-player WebSocket browse handler, `BrowseMedia`,
  Universal delegation, Sonos browsing/playback and artwork, media-source models.
- Frontend `87babb1a3`: media picker navigation, play-media dispatch, and special
  handling of native `media-source://` items (including TTS).
- Documentation `d8f04205d2`: media player, media source, Universal, and Sonos pages.
- Checked the same interfaces in the project's pinned Home Assistant 2026.9.4.

## Contract and implementation

1. Expose Browse Media only for the selected, loaded, available bound player
   advertising `BROWSE_MEDIA`, under the existing zone/pending-feedback guards.
2. Delegate root and nested browsing to the provider and preserve its catalog,
   titles, content types, artwork proxy URLs, expandable/playable flags, and
   child classes. Never mutate a provider's cached tree. Disable item playback
   when the provider lacks `PLAY_MEDIA`. Suppress provider search affordances
   because `SEARCH_MEDIA` is not forwarded in this iteration.
3. Scope provider-specific browse IDs to the originating entity using stable,
   versioned IDs. Unwrap them before forwarding browse/play calls, preserving
   exact provider IDs and types (including Sonos favorites). Reject an item when
   a different bound player is selected. Stable IDs remain usable after a
   restart and can be saved in scripts; they also work on another zone actively
   bound to the same provider.
4. Keep globally meaningful `media-source://` IDs native so HA's TTS/media-source
   UI and URI resolution remain intact. They route to the current eligible
   source, whose integration resolves and filters them. Raw play-media service
   calls keep their existing behavior.
5. Discard an in-flight browse result if zone/source eligibility changed while
   awaiting it, including switching away and back. Handle provider BrowseError
   without disabling playback. Connection failures use existing recovery rules;
   falsely advertised/unimplemented browsing is hidden until provider reload.
6. Add real HA WebSocket/service and artwork-proxy tests, plus a nested demo
   catalog for live browsing and selection through the actual HA API/simulator.
7. Run regression tests, lint/format, live checks, and GitHub validation before
   publishing `v0.2.0b3`. Production browsing/selection is the final acceptance
   gate; do not promote to stable afterward without a separate explicit request.

## Evidence

- Automated validation: 333 tests passed (37 new browse tests), Ruff lint/format
  and diff checks passed. Overall statement coverage 97%, media player 100%.
- Live validation: all 11 acceptance groups passed on HA 2026.9.4 with actual
  XSIG TCP, two loaded demo sources, WebSocket browse/select calls, and artwork
  HTTP requests. Wrong-provider selections were rejected after a source switch.
- Production browser, playback, and artwork acceptance: pending.
