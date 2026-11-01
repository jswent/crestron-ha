# Bound source controls: 0.2.0b2

Continue on `codex/source-media-bindings` and keep PR #1 a draft. The owner
confirmed the read-only 0.2.0b1 behavior works in production on 2026-10-31 and
explicitly requested that this release remain a prerelease.

## Contract

- Existing optional source bindings also expose playback controls. Only the
  selected source can receive them, while the Crestron zone is connected, on,
  and has no pending source/power transition or conflicting source feedback.
- Home Assistant's source integration owns the physical connection. Require an
  actual loaded media-player entity, a known/non-unavailable state, and current
  availability. A retained or manually created state alone is insufficient.
- Advertise only the intersection of supported source features and implemented
  playback controls: play/pause, stop, previous/next, seek, play-media (including
  supported enqueue/announce options), clear playlist, shuffle, and repeat.
  Crestron owns power, source selection, mute, and volume. Grouping, sound mode,
  media browsing, and search are not forwarded.
- Recheck source, availability, and capability when each command is invoked.
  Use HA services with the caller context, wait for the result, and never queue
  a command for a future source or optimistically change playback state.
- Connection/operation failures remove forwarded controls until a fresh source
  state update allows recovery. Validation errors remain visible without treating
  them as connection failures. A late failure from an old source must not disable
  the new source. Guard indirect forwarding loops.

## Work and acceptance

1. Extend the player lifecycle with dynamic features and guarded forwarding.
2. Add real HA entity/service tests for capabilities, routing, unavailable/missing
   targets, transitions, live edits, failures/recovery, context, and races.
3. Extend the persistent Sonos demo and live HA/TCP simulator checks, including
   rejected commands while TV is selected or Sonos is offline.
4. Run the full suite, lint/format, live acceptance, and GitHub validation. Publish
   `v0.2.0b2` as a prerelease, retaining `v0.1.1` as stable and `v0.2.0b1` for rollback.
5. Owner installs through HACS and confirms production write-through behavior.
   Even after confirmation, keep this a prerelease until explicitly asked to
   promote it.

## Evidence

- 0.2.0b1 production test: owner confirmed working; retain prerelease status.
- 0.2.0b2 automated acceptance: 296 tests passed; 97% overall and 100% media-player
  statement coverage. Ruff lint/format passed.
- 0.2.0b2 live acceptance: all nine groups passed on HA 2026.9.4 with a loaded
  Sonos demo and actual XSIG TCP simulator. Includes forwarding all implemented
  demo controls and rejecting commands while pending, unbound, off, unavailable,
  or after binding removal.
- 0.2.0b2 production acceptance: pending.
