# Changelog

## 0.2.0 — 2026-11-07

Stable release of optional source media bindings, playback controls, and media
browsing, following owner-confirmed production testing of `v0.2.0b3`.

### Added

- Bind analog source numbers or digital source joins to Home Assistant media
  players, such as Sonos. Sources without bindings keep their existing behavior.
- Display the selected player's playback state, title, artist, album, artwork,
  duration, and position while Crestron controls zone power, source, volume,
  mute, and availability.
- Forward supported playback controls only to the currently selected, loaded,
  available provider. Clear metadata and hide forwarded controls immediately
  during source/power transitions or when the provider becomes unavailable.
- Browse a supported provider's folders, albums, and favorites through the
  Crestron zone, retaining provider artwork and exact playback identifiers.
- Keep provider-specific browser selections tied to their original source so
  they cannot accidentally play on a different bound player. Native Home
  Assistant media-source IDs retain their existing behavior.
- Configure bindings through standalone entity forms, device features, or bulk
  configuration; edits apply live.

### Upgrade

Install `v0.2.0` through HACS and restart Home Assistant. Existing bridges,
bindings, entity IDs, and saved browser selections remain valid. No remapping or
config-entry migration is required when upgrading from `0.1.1` or a `0.2.0`
prerelease. The implementation is unchanged from production-tested `v0.2.0b3`.

Home Assistant 2026.9.4 or newer is required. Provider-specific search, grouping,
and sound modes remain on the source entity. Availability comes from the source
integration; only its supported capabilities are exposed.
