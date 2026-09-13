# Product scope

PiSight v0.1.0 MVP.

## The one-sentence version

PiSight turns a Raspberry Pi 5 running Kismet into a glanceable 320x240 field instrument that
answers *what is on the air here, and is anything wrong* — without opening a laptop.

## Who it is for

Someone conducting **authorized** passive wireless observation who already runs Kismet and wants a
physical readout: a site survey, an authorized assessment, RF troubleshooting, or education. They
already have a laptop and Kismet's own web UI for deep analysis. What they lack is something they
can glance at.

## What PiSight is

**A display and triage client.** Kismet captures; PiSight shows. That division is the product.

| It does | It does not |
|---|---|
| Read Kismet's REST API, read-only | Capture packets itself |
| Render four screens at 320x240 | Provide deep analysis (Kismet's UI does that) |
| Show freshness, staleness and outages honestly | Store history |
| Run unattended and recover from failures | Require interaction to stay useful |
| Work fully in mock mode with no radio | Need hardware to develop or test |

## What PiSight is explicitly not

**Not a capture tool.** It has no packet capture code and, as deployed, no ability to open a raw
socket. If Kismet is not running, PiSight has nothing to show and says so.

**Not an offensive tool.** No injection, deauthentication, probing, association, cracking,
credential collection, exploitation or spoofing. Not now, not as a plugin, not behind a flag. See
[SECURITY.md](../SECURITY.md).

**Not a Kismet configuration UI.** It cannot lock a channel, change a hop rate, start or stop a
datasource, or edit an alert definition. The Channels screen deliberately has no control on it.

**Not a replacement for Kismet's web UI.** Four screens on a 320x240 panel cannot compete with a
full browser interface, and should not try. PiSight answers the glanceable questions; Kismet
answers the detailed ones.

**Not an exfiltration path.** No upload, export, sync or reporting. `allow_uploads` is validated as
`false` and PiSight refuses to start otherwise.

**Not a data manager.** It never reads, moves, rotates or deletes Kismet's logs. It calls
`statvfs` on the log path to show free space, and that is the entirety of its relationship with
your capture data.

## In scope for v0.1.0

- Four finished, navigable screens: Overview, Channels, Devices, Alerts/System
- Live read-only Kismet REST integration behind a provider boundary
- A first-class mock provider, including simulated failure modes
- Graceful degradation: stale and offline states, retained last-known data, backoff with jitter
- Mouse, keyboard and SDL touch input with debounce
- Read-only host health: CPU temperature, storage, uptime, platform
- `pisight doctor`, read-only, with JSON output
- Raspberry Pi install, verify, uninstall scripts and a hardened systemd unit
- Two documented deployment profiles (DRM/KMS console, and desktop session)
- Tests, CI, documentation, and deterministic screenshot generation

## Out of scope for v0.1.0

Not rejected forever — just not this version.

| Deferred | Why |
|---|---|
| GPS and location | Adds a category of sensitive data for little glanceable value |
| Historical graphs and trends | Needs storage; Kismet already stores history |
| Device detail drill-down | Costs a navigation model that 320x240 and touch make awkward |
| Alert filtering and acknowledgement | Needs persistent state PiSight deliberately does not have |
| Multiple simultaneous datasources | One PAU0B is the target hardware |
| 6 GHz | The PAU0B cannot observe it |
| Remote or multi-Pi aggregation | Would require a network destination, which the threat model rules out |
| Web or mobile companion | Contradicts "no web framework, no browser" |
| Configuration from the UI | An on-screen editor on a resistive panel is worse than editing a file over SSH |
| Themes and customization | One palette, tuned for legibility, is a feature |

## Design constraints, and what they forced

**320x240 is small.** At 176 usable pixels of content height, the Devices screen shows five rows.
A sixth would push type below what is legible at arm's length. That is a legibility limit, not
pagination — there is no way to scroll to a sixth device, by design.

**The Pi shares its CPU with Kismet.** PiSight exists to serve Kismet, not to compete with it.
Hence 30 FPS rather than 60, a 2-second poll interval rather than continuous streaming, backoff
with jitter instead of tight retries, and a hard `MemoryMax` in the unit file.

**A resistive touchscreen is imprecise.** Hence 80x40 navigation buttons (double the 40x40
minimum), no gestures, no hover interactions, no virtual keyboard, and a 220 ms debounce.

**Field use means glare, motion and distance.** Hence flat near-black, high contrast, no
gradients, no transparency, and status never conveyed by colour alone — every coloured state also
has a word or a distinct shape.

**Device names come from strangers.** Hence sanitization of every string before rendering or
logging, and width-bounded ellipsized text everywhere.

## Success criteria for the MVP

The desktop MVP is done when:

1. Mock mode launches and all four screens are finished and navigable. ✅
2. Screenshots generate at exactly 320x240. ✅
3. Live Kismet integration exists behind the provider boundary. ✅
4. Errors and stale data are visible but non-fatal. ✅
5. No secrets or real observations are committed. ✅
6. `pisight doctor` works off-Pi without crashing. ✅
7. Raspberry Pi deployment assets exist. ✅
8. Documentation is complete. ✅
9. All local quality gates pass. ✅
10. **Hardware validation is pending.** ⏳ — see [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md)

Item 10 is the honest gap. Everything else is verifiable on a desktop and has been verified;
nothing in this repository has run on a Pi.

## Likely next version

In rough priority order: complete hardware validation; measure real performance and tune the
defaults from evidence rather than guesswork; a device detail view if navigation can be made to
work on a resistive panel; configurable stale thresholds informed by observed Kismet latency; and
a compact historical sparkline for packet rate if it can be done without persistent storage.

Nothing on that list changes the passive-only boundary.
