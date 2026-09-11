# Synthetic Kismet fixtures

Every file in this directory is **invented**. Nothing here came from a real capture.

* SSIDs are made-up strings, not real network names.
* MAC addresses use the locally-administered `02:` prefix, which is never assigned to a
  vendor and therefore cannot collide with real hardware.
* Timestamps are fixed constants, not observation times.
* There are no coordinates, no GPS records and no packet payloads.

This is a hard rule for the repository: real SSIDs, real MAC addresses, real coordinates
and real capture data must never be committed. See `docs/THREAT_MODEL.md`.

The fixtures reproduce Kismet's namespaced field layout (`kismet.device.base.macaddr` and
friends) so the parsers are tested against the shape of a real response without containing
real data. `devices_malformed.json` and `devices_partial.json` deliberately break that
shape to prove the parsers degrade instead of crashing.
