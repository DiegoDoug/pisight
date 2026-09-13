# Changelog

All notable changes to PiSight are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-11

First MVP release. **Desktop-complete; not yet validated on Raspberry Pi hardware.**

### Added

**Application**
- Four finished, navigable screens on a fixed 320x240 logical canvas: Overview, Channels,
  Devices, and Alerts/System.
- A 24px status bar (name, capture state, channel/hop, clock, CPU temperature, stale/offline
  indicator) and a 40px four-button navigation bar, both drawn on every frame.
- Integer nearest-neighbour scaling to larger development windows, so the desktop shows exactly
  the pixels the TFT will show.
- Mouse, keyboard (`1`-`4`, arrows, `Esc`) and SDL touch input, with a 220 ms debounce that
  absorbs both resistive-panel contact bounce and SDL's synthesised mouse events.

**Data**
- `DashboardProvider` protocol with two implementations: a read-only Kismet REST provider and a
  first-class deterministic mock provider. The UI cannot tell them apart.
- Tolerant parsers for Kismet's namespaced fields, handling flat-dotted and nested forms, absent
  optional fields, unknown device types, out-of-range severities, and mixed numeric encodings.
- Bounded queries: a recent-window `last-time` view, field simplification, and client-side caps.
- Mock mode simulates realistic 2.4/5 GHz channel plans, APs, clients and bridged devices, a
  drifting packet rate, moving signals, arriving devices, alerts across all five severities, plus
  Kismet outage, malformed response, stale data and low-storage conditions.

**Concurrency**
- One supervised background polling thread; the render thread performs no I/O at all.
- Bounded single-slot snapshot store — memory is constant regardless of thread speeds.
- Exponential backoff with jitter and a hard ceiling; cancellation via `Event.wait`, so shutdown
  never waits out a backoff.
- Last-known observations are retained and visibly marked offline when polling fails, and keep
  ageing so the staleness indicator engages on schedule.

**Host and diagnostics**
- Read-only host health: CPU temperature from sysfs, storage via `shutil.disk_usage`, process
  uptime, platform and architecture, with graceful `None` values off-Pi.
- `pisight doctor` and `pisight doctor --json`: read-only environment diagnostic covering OS,
  architecture, Python, display environment, DRM/framebuffer nodes, SDL, input devices, USB,
  wireless interfaces and drivers, apparent monitor-mode support, Kismet reachability and token
  acceptance, storage, and group membership. Never uses a shell and changes nothing.

**Configuration and security**
- TOML configuration with environment overrides and documented precedence; unknown keys are
  rejected rather than ignored.
- The Kismet API token is read from `PISIGHT_KISMET_API_TOKEN` only, sent in the `KISMET` cookie,
  and is deliberately not a field on the config object.
- Sanitization of all untrusted text before rendering and before logging, with a redacting log
  filter that also flattens every record to one line.

**Deployment**
- `scripts/install-rpi.sh` (idempotent, `--dry-run`), `scripts/verify-rpi.sh` (read-only),
  `scripts/uninstall-rpi.sh` (`--dry-run`, `--purge`).
- A hardened `systemd/pisight.service`: unprivileged user, no Linux capabilities, no `AF_PACKET`,
  read-only filesystem, journald logging, bounded memory.
- Configuration and environment templates.

**Quality**
- 515 tests covering configuration precedence, parsers against synthetic namespaced fixtures,
  HTTP failure modes, backoff, staleness, store bounding, mock determinism, MAC masking, SSID
  sanitization, severity and channel normalization, navigation, touch debounce, layout bounds,
  empty states, all four screens at exactly 320x240, and clean shutdown. 89% coverage.
- `tests/test_safety.py` parses the source tree and fails the build on shell invocation, offensive
  tool references, raw-socket imports, Kismet write endpoints, non-loopback destinations,
  committed credentials, vendor MACs in fixtures, or leftover TODO markers.
- GitHub Actions CI across Python 3.11-3.14 (3.11 required, 3.14 advisory) running format, lint,
  mypy, tests, package build, headless smoke test and screenshot generation.
- `pisight --mode mock --smoke-seconds N` and `pisight screenshots --output DIR`.

**Documentation**
- README, SECURITY, and docs for architecture, product scope, threat model, Pi deployment,
  hardware validation, rollback, decisions and MVP acceptance.

### Security

- Passive-only by construction: no injection, deauthentication, probing, association, cracking,
  credential collection, exploitation or spoofing. Kismet access is read-only and uses a
  `readonly`-role token; the administrator password is never required or stored.
- The deployed process holds no Linux capability and cannot open a raw socket, so it is incapable
  of capturing or injecting packets even if its code were changed to try.

### Known limitations

- **Not validated on real hardware.** See `docs/HARDWARE_VALIDATION.md`.
- One PAU0B listens on one channel at a time; channel hopping creates observation gaps, so
  per-channel counts are a sample rather than a census.
- Wi-Fi payloads are usually encrypted; PiSight shows metadata and never attempts decryption.
- "New" means new to the current PiSight process — **the counter resets on every restart**.
- The Devices screen shows five devices at a time, with no scrolling, by design.

[0.1.0]: https://github.com/DiegoDoug/pisight/releases/tag/v0.1.0
