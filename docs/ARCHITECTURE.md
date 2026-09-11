# Architecture

PiSight is a small application with one hard constraint that shapes everything else: it draws a
320x240 display at 30 FPS on a Raspberry Pi that is simultaneously running a packet capture
engine. Every design decision below follows from "do not drop frames, and do not steal CPU from
Kismet".

## Layers

```
src/pisight/
├── models.py            Immutable domain types. No I/O, no pygame, no httpx.
├── config.py            TOML + environment loading, validation. No I/O beyond one file read.
├── sanitize.py          Neutralises untrusted text before it is rendered or logged.
├── logging_setup.py     Logging with token redaction and log-injection defence.
│
├── providers/           Where data comes from
│   ├── base.py          DashboardProvider protocol, NewDeviceTracker, error types
│   ├── parsers.py       Tolerant Kismet response parsing (pure functions)
│   ├── kismet.py        Live read-only REST provider
│   └── mock.py          Deterministic simulation
│
├── polling/             The only place I/O happens
│   ├── coordinator.py   Supervised background worker
│   ├── store.py         Bounded single-slot snapshot handoff
│   └── backoff.py       Exponential backoff with jitter
│
├── health/host.py       Read-only host metrics (sysfs, disk_usage)
│
├── ui/                  Rendering and input
│   ├── layout.py        Fixed 320x240 geometry (pure, pygame-free)
│   ├── theme.py         Palette and type scale
│   ├── fonts.py         Font loading with guaranteed fallback
│   ├── widgets.py       Drawing primitives
│   ├── chrome.py        Status bar and navigation bar
│   ├── navigation.py    Screen state and touch debounce (pygame-free)
│   ├── app.py           Event loop, scaling, frame composition
│   └── screenshots.py   Deterministic PNG generation
│
├── screens/             The four screens
│   ├── base.py          RenderContext and the Screen protocol
│   ├── common.py        Shared formatting and empty states
│   ├── overview.py      Screen 1
│   ├── channels.py      Screen 2
│   ├── devices.py       Screen 3
│   └── alerts_system.py Screen 4
│
├── doctor.py            Read-only environment diagnostic
└── cli.py               Argument parsing and command dispatch
```

The dependency direction is strictly downward: `screens` may import `ui` and `models`; `ui` may
import `models`; `models` imports nothing from PiSight. Nothing in `ui` or `screens` imports
`providers` or `polling` except `app.py`, which is the single wiring point.

## The threading model

Two threads, one direction of data flow.

```
  polling thread                      single-slot store                render thread
  ──────────────                      ─────────────────                ─────────────
  provider.fetch()   ── publish() ──▶  [ newest snapshot ]  ── get() ──▶  draw frame
  host_health.collect()                                                  present()
  backoff.wait()                                                         clock.tick(30)
```

**The render thread performs no I/O whatsoever.** It never opens a socket, never reads sysfs,
never calls `disk_usage`. A 4-second HTTP read timeout on the render thread would be a 4-second
frozen display; instead it is a snapshot that quietly ages and shows STALE.

**The polling thread never touches pygame.** It produces immutable snapshots and nothing else.

### Why a single slot and not a queue

The renderer only ever wants the *newest* snapshot. A frame drawn from three-second-old data is
worthless the moment fresher data exists. A queue would accumulate snapshots nobody will ever
draw, growing memory whenever the poller outruns the renderer.

`SnapshotStore` therefore holds exactly one snapshot. Publishing overwrites. Superseded snapshots
are discarded at the moment they are superseded, and counted so the behaviour is observable.
Memory is constant regardless of relative thread speed — `test_store_is_bounded_to_a_single_slot`
publishes a thousand snapshots and asserts that 999 were dropped and one is retained.

### Cancellation

All waiting goes through `threading.Event.wait()`, never `time.sleep()`. This gives two
properties: the thread consumes no CPU between polls, and `stop()` takes effect immediately rather
than waiting out a delay that may be 30 seconds into a backoff.
`test_a_persistently_failing_provider_stops_promptly` asserts exactly that.

## Failure handling

The rule: **a failed poll degrades the display; it never ends the session.**

| Situation | Behaviour |
|---|---|
| Poll succeeds | Publish, reset backoff, sleep the poll interval |
| Poll fails, previous snapshot exists | Keep the observations on screen, re-mark capture status offline, back off |
| Poll fails, no snapshot yet | Record the failure; screens show their "waiting for data" state |
| Auth failure (401/403) | Same, but logged at ERROR with a message naming the env var — and never the token |
| Malformed response | Treated as a failed poll; the previous snapshot is retained |
| Unexpected exception in a provider | Caught, logged with a traceback, treated as a failed poll |

That last row matters: a bug nobody anticipated in a provider must not take the display down.
`test_poll_once_survives_every_failure_kind` covers all four error classes including a bare
`RuntimeError`.

When a snapshot is retained after a failure, its `monotonic_at` is deliberately **not** reset.
If it were, the retained snapshot would look permanently fresh and the staleness indicator would
never engage — the display would confidently show data from ten minutes ago.
`test_snapshot_keeps_ageing_while_offline` guards this.

### Critical versus optional endpoints

Not every Kismet endpoint is load-bearing:

- **Critical** (`/system/status.json`, the device view): failure fails the whole poll.
- **Optional** (channels, datasources, alerts, packet stats): failure leaves that panel empty and
  records a visible warning on the Alerts/System screen.

Blanking the entire dashboard because the channel endpoint hiccuped would be worse than showing
three of four panels. An auth failure is the exception — it propagates from any endpoint, because
a revoked token must surface rather than hide behind a degraded panel.

## Staleness versus offline

Two distinct states, deliberately:

- **STALE** (default: > 10 s): data has aged past the threshold. It may still be broadly correct.
- **OFFLINE** (default: > 30 s, or the source told us it is down): stop implying the display is
  live.

Both derive from `monotonic_at`, never wall-clock time, so an NTP step or a manual clock change
cannot make fresh data look stale or vice versa.

## The provider boundary

```python
class DashboardProvider(Protocol):
    name: str

    def fetch(self) -> DashboardSnapshot: ...
    def close(self) -> None: ...
```

That is the entire contract. Screens receive a `DashboardSnapshot` and cannot tell where it came
from — which is what makes the whole UI developable and testable on a desktop with no radio.

Mock mode is a first-class implementation of this protocol, not a stub. It simulates the failure
modes too, and `simulate_malformed_response()` pushes a genuinely invalid payload through the
*real* parsers, so recovery is exercised end to end rather than faked.

## Parsing untrusted data

Kismet returns namespaced fields (`kismet.device.base.macaddr`) that may arrive flat-dotted or
nested depending on endpoint and field simplification. `providers/parsers.py` is built to survive
that:

- `field_value()` resolves a path whether the record is flat, nested, or partially nested.
- Every optional field has a default; nothing raises `KeyError`.
- Unknown device types and out-of-range severities normalize rather than raise.
- Values are sanity-checked: a reported signal of `0` means "never measured", not "extremely
  strong"; a frequency is normalized whether it arrives in kHz, MHz or GHz.
- **Structurally wrong responses are rejected.** An object where a list was expected raises
  `MalformedResponse` rather than being read as one empty record — quietly rendering a Kismet
  error body as "nothing observed" would tell the operator the air is quiet when the query failed.

Device names are attacker-controlled. Every string from Kismet passes through `sanitize_text()`
before it is rendered or logged: control characters and bidi overrides are replaced, newlines
collapse to spaces, and length is bounded. A crafted SSID cannot forge a log line or scramble the
display.

## Bounding

A busy environment must not grow PiSight's memory. Bounds exist at four levels:

1. **Query**: Kismet's `last-time` views are asked for a recent window (a negative timestamp is
   Kismet's "this many seconds ago"), never the unbounded all-devices dump.
2. **Response**: field simplification requests only the dozen fields the screens render.
3. **Parse**: `parse_devices(limit=...)` and `parse_alerts(limit=...)` truncate after sorting.
4. **Store**: one snapshot slot.

`NewDeviceTracker` is bounded too, evicting oldest-first past 4096 keys. The only consequence of
eviction is that a long-departed device may be flagged "new" again if it returns.

## Rendering

Everything is drawn to a 320x240 `Surface` and then scaled to the window with
`pygame.transform.scale`, which is nearest-neighbour. Consequences:

- The desktop window shows *exactly* the pixels the TFT will show. There is no separate desktop
  layout to keep in sync.
- Only integer scale factors are used. A fractional scale would smear the 1-pixel dividers and the
  9-pixel type into grey.
- Pointer coordinates are divided by the scale and offset, so a click at a given logical point
  means the same thing at 1x and 3x.

Layout geometry lives in `ui/layout.py` as plain integer `Rect`s with no pygame dependency, so
layout invariants are unit-testable without a video driver.

### Testing the rendering

No golden-image comparisons. A full-image hash breaks on any font or antialiasing difference
between machines and teaches you nothing when it fails. Instead:

- every screen renders at exactly 320x240;
- filling the canvas with a marker colour and drawing only the content band proves a screen never
  overdraws the status or navigation bars — and, in the other direction, that it actually drew
  something rather than silently nothing;
- every state renders without raising: no snapshot, empty snapshot, stale, offline, full-MAC mode,
  and a deliberately hostile snapshot with 300-character SSIDs, bidi overrides, billion-count
  channels and 500-character alert text.

## Input

`ui/navigation.py` has no pygame dependency beyond nothing at all, so navigation and debounce are
tested as pure logic.

Touch debounce solves two real problems. A resistive XPT2046 panel bounces, so one physical press
can produce several SDL events within milliseconds. And SDL synthesises a mouse event for every
touch event, so handling both would double every tap. A single 220 ms window across both sources
fixes both. A swallowed press does not reset the timer — holding a finger down must not postpone
the next legitimate press.

## Configuration

Frozen dataclasses throughout. Loading is: packaged defaults → TOML file → environment →
`--mode` flag. Unknown keys and sections are **rejected**, turning a typo into a clear error
instead of a silently ignored setting.

The Kismet API token is deliberately **not** a field on `AppConfig`. It is read from the
environment at the moment of use. Because it is not in the dataclass, it cannot reach a config
dump, a `repr`, a crash report or `pisight config` — and there is a test asserting exactly that.

## Related reading

- [PRODUCT_SCOPE.md](PRODUCT_SCOPE.md) — what is in and out of scope
- [THREAT_MODEL.md](THREAT_MODEL.md) — assets, adversaries, mitigations
- [DECISIONS.md](DECISIONS.md) — why each notable choice was made
