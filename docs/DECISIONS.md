# Design decisions

Decisions made while building PiSight v0.1.0, with the reasoning. Recorded so a future maintainer
can tell which choices are load-bearing and which are arbitrary.

Format: **context → decision → why → consequence**.

---

## D1 — Python 3.11 as the compatibility floor

**Context.** The development machine had Python 3.13 and 3.14; Raspberry Pi OS Bookworm ships 3.11.
A virtualenv created with 3.14 still *runs* 3.14 — it does not emulate 3.11.

**Decision.** Install Python 3.11.9 locally and build the development virtualenv with it, matching
the deployment target exactly. `requires-python = ">=3.11"`, `target-version = "py311"`,
`python_version = "3.11"`.

**Why.** Developing on a newer interpreter than you deploy to hides incompatibilities until the
worst possible moment — on the Pi, over SSH, in the field. Matching the target removes that class
of surprise entirely.

**Consequence.** CI runs 3.11, 3.12, 3.13 and 3.14. **3.11 is the required gate**; 3.14 is
`continue-on-error` as a forward-compatibility signal only, so a failure unique to it cannot block
the MVP. A separate `required` job exists for branch protection to point at.

---

## D2 — One background thread, not asyncio

**Context.** Network polling must not block rendering. Options: `asyncio`, a thread pool, or one
supervised thread.

**Decision.** One daemon thread owning all I/O, publishing to a single-slot store.

**Why.** pygame's event loop is synchronous and blocking; marrying it to an event loop means
either running the loop in a thread anyway or pumping it manually each frame. Both are more moving
parts than the problem needs. The workload is *one* HTTP conversation every two seconds — there is
no concurrency to exploit, only a need to keep it off the render thread.

**Consequence.** `httpx.Client` (sync) rather than `AsyncClient`. The polling logic is
straightforward and `poll_once()` is directly testable without threads or an event loop.

---

## D3 — A single-slot store, not a queue

**Context.** The spec allowed "a bounded queue or equivalent latest-value store".

**Decision.** Exactly one snapshot slot. Publishing overwrites; superseded snapshots are discarded
and counted.

**Why.** The renderer only ever wants the newest snapshot — a frame drawn from three-second-old
data is worthless once fresher data exists. A queue of depth *n* would hold *n−1* snapshots nobody
will ever draw, and grows memory whenever the poller outruns the renderer.

**Consequence.** Memory is constant regardless of relative thread speed.
`test_store_is_bounded_to_a_single_slot` publishes a thousand snapshots and asserts 999 dropped,
one retained. The `dropped` counter makes the behaviour observable rather than silent.

---

## D4 — Retained snapshots keep their original age

**Context.** When a poll fails, the last good snapshot stays on screen. Should its timestamp be
refreshed?

**Decision.** No. `monotonic_at` is never reset when a snapshot is re-marked offline.

**Why.** Refreshing it would make the retained snapshot look permanently fresh, so the staleness
indicator would never engage and the display would confidently show ten-minute-old data as
current. For a reconnaissance instrument, confidently wrong is worse than obviously stale.

**Consequence.** `test_snapshot_keeps_ageing_while_offline` guards this. The STALE indicator
appears on schedule, then OFFLINE.

---

## D5 — Staleness uses monotonic time, not wall-clock

**Decision.** `DashboardSnapshot` carries both `generated_at` (wall, for display) and
`monotonic_at` (for age). All freshness logic uses the latter.

**Why.** A Pi in the field may have no RTC and will step its clock when NTP first syncs. With
wall-clock arithmetic, a step forward makes fresh data look hours stale, and a step backward makes
old data look like it arrived in the future.

---

## D6 — Critical versus optional Kismet endpoints

**Context.** `fetch()` calls six endpoints. Should any single failure fail the whole poll?

**Decision.** System status and the device view are critical (failure fails the poll). Channels,
datasources, alerts and packet stats degrade to empty with a visible warning. Auth failures
propagate from *any* endpoint.

**Why.** Blanking the whole dashboard because the channel endpoint hiccuped is worse than showing
three of four panels. But a revoked token must surface immediately rather than hiding behind a
quietly degraded panel.

**Consequence.** `DashboardSnapshot.warnings` carries the degradation to the Alerts/System screen,
so the operator can see *which* panel is not to be trusted.

---

## D7 — A wrong-shaped response is rejected, not read as empty

**Context.** `parse_devices` initially treated an unrecognised object as a single record, which
yielded zero devices. A test caught that a Kismet error body rendered as "no devices observed".

**Decision.** An object where a list was expected raises `MalformedResponse`.

**Why.** This is the worst possible failure for a reconnaissance instrument: a silent false
negative. "No devices observed" is a *meaningful* reading — it means the air is quiet. Producing
it from a failed query tells the operator something false about the physical world.

**Consequence.** Such responses are treated as a failed poll; the previous snapshot is retained
and the display shows offline rather than an empty room.

---

## D8 — Bounded device queries via negative `last-time`

**Context.** The spec forbids fetching an unbounded all-devices response.

**Decision.** POST to `/devices/views/{view}/last-time/{-N}/devices.json` with a `fields`
simplification list, then cap client-side at `max_devices`.

**Why.** Kismet reads a negative `last-time` as "this many seconds ago", which bounds the response
at the server. Field simplification bounds each record to the dozen fields the screens render.
The client-side cap bounds the result even if both fail. Three independent bounds.

**Consequence.** PiSight samples a recent window rather than enumerating everything — documented
as a limitation in the README rather than hidden.

---

## D9 — The token is a cookie, and not in the config object

**Context.** Kismet accepts an API token in the `KISMET` cookie or as a URI parameter.

**Decision.** Cookie only. And the token is never a field on `AppConfig` — it is read from the
environment at point of use.

**Why.** A token in a URL ends up in access logs, proxy logs, `Referer` headers and exception
messages. A token in the config object ends up in `repr()`, `asdict()`, crash reports and the
output of `pisight config`. Keeping it out of the data structure makes leakage structurally
impossible rather than merely unlikely.

**Consequence.** `test_token_is_not_part_of_the_config_object` and
`test_token_is_sent_as_a_cookie_and_never_in_the_url` assert both properties. A redacting log
filter is a third layer of defence.

---

## D10 — Unknown configuration keys are rejected

**Decision.** An unrecognised key or section in `config.toml` is a hard error.

**Why.** The alternative — ignoring it — means `fulscreen = true` silently does nothing, and the
operator concludes the feature is broken. On a headless Pi that debugging session is expensive.

**Consequence.** A per-section schema of coercion functions, which also gives type validation and
clear messages for free.

---

## D11 — `enable_gps` and `allow_uploads` are validated as false

**Decision.** These settings exist but PiSight refuses to start if either is true.

**Why.** They could simply be absent. Making them present-but-rejected documents the guarantee in
the place an operator looks, and means a future change that tries to relax it fails loudly at the
config layer rather than shipping quietly.

---

## D12 — Integer-only nearest-neighbour scaling

**Decision.** Render to a 320x240 surface, then scale by the largest integer factor that fits,
centred.

**Why.** The desktop window then shows *exactly* the pixels the TFT will show — no second layout
to keep in sync, and a screenshot taken on a desktop is a faithful preview. A fractional scale
would smear the 1-pixel dividers and 9-pixel type into grey and make desktop review misleading.

**Consequence.** A 960x720 window at 3x by default. Non-multiple window sizes letterbox rather than
stretch.

---

## D13 — Five device rows, and no scrolling

**Decision.** The Devices screen shows exactly five devices. There is no sixth, and no scroll.

**Why.** 176 pixels of content height divided by five gives a two-line row at 11px and 9px — the
smallest that stays legible at arm's length. A sixth row requires smaller type, and the screen
stops working at the distance it is meant to be read from. Scrolling needs a gesture or a control,
both of which the spec rules out and a resistive panel handles badly.

**Consequence.** Documented as a deliberate limit, not a stub. Sorting is most-recently-active
first, so the five shown are the five that matter.

---

## D14 — A shared peak across both band columns

**Context.** The Channels screen normalised each band's bars against that band's own maximum. A
5 GHz channel with one device drew a bar as long as a 2.4 GHz channel with four.

**Decision.** One peak across both columns.

**Why.** Two columns side by side invite comparison. Per-column normalisation makes a quiet band
look as busy as a loud one — exactly the wrong impression, and the numbers alongside are too small
to correct it at a glance.

**Consequence.** Caught by rendering a screenshot and looking at it, not by a test. Worth
remembering: some defects are only visible.

---

## D15 — MAC addresses masked to four hex characters by default

**Decision.** `··2233` by default; full display requires an explicit opt-in.

**Why.** The last four characters are enough to distinguish devices on screen and to correlate
against Kismet's UI, without being identifying. It means a photograph of the screen, or a
screenshot committed to this repository, discloses nothing about real hardware.

---

## D16 — Sanitize before rendering *and* before logging

**Decision.** Every string from Kismet passes through `sanitize_text()`. The logging filter
sanitizes every record, not just PiSight's own formatted messages.

**Why.** SSIDs are chosen by the people being observed. Newlines forge journal entries; ANSI
sequences colour a terminal; bidi overrides reverse displayed text. Sanitizing at the logging
*handler* rather than at each call site means a future `logger.info(f"...{device.name}")` written
without thinking is still safe.

---

## D17 — Mock mode pushes real bad data through real parsers

**Decision.** `simulate_malformed_response()` feeds an invalid payload to the actual
`parse_devices` rather than raising a synthetic exception.

**Why.** A mock that fakes its failures tests the mock. Routing through the real parser means the
recovery path exercised in mock mode is the same code that runs against a real broken Kismet.

---

## D18 — Deterministic and evolving mock modes

**Decision.** `evolving=False` is fully reproducible; `evolving=True` drifts over time.

**Why.** Tests and screenshots need byte-identical output — otherwise a screenshot diff is noise
and a layout test is flaky. Demonstrations need movement, or the dashboard looks broken.

**Consequence.** `DETERMINISTIC_WALL_TIME` is shared between the mock provider and the screenshot
renderer. Without that, observation ages rendered as "199d13h" because two unrelated fixed
timestamps were being subtracted. Caught by looking at a screenshot.

---

## D19 — No golden-image tests

**Decision.** Test dimensions, layout containment and successful rendering. Never full-image
hashes.

**Why.** A golden image breaks on any font substitution or antialiasing difference between
machines, and when it fails it tells you "something changed" and nothing more. The properties that
actually matter — exactly 320x240, nothing overdrawing the chrome, nothing raising on hostile
input — are directly assertable.

**Consequence.** The containment test fills the canvas with a colour the palette never uses and
checks the chrome bands survive; a companion test checks the content band was actually painted, so
a screen that silently drew nothing cannot pass.

---

## D20 — The security boundary is enforced by tests, not prose

**Decision.** `tests/test_safety.py` parses the source tree with `ast` and fails the build on a
shell invocation, an offensive tool reference, a raw-socket import, a Kismet write endpoint, a
non-loopback URL, a committed credential, a vendor MAC in a fixture, or a leftover TODO.

**Why.** A README promise decays. An executable test does not.

**Consequence.** It must parse rather than grep: PiSight's own modules describe at length what
they refuse to do, and a substring search flags that prose as a violation. Hence "executable
strings only, docstrings excluded". Technique words like *deauth* are deliberately **not** banned —
`DEAUTHFLOOD` is a Kismet alert PiSight exists to *display*, and banning the substring would forbid
reporting an attack. Actually performing one is prevented by the command allowlist and the import
checks.

---

## D21 — The kernel enforces passivity

**Decision.** The systemd unit sets `CapabilityBoundingSet=` and `AmbientCapabilities=` empty and
omits `AF_PACKET` from `RestrictAddressFamilies`.

**Why.** Documentation and tests both depend on the code being what we think it is. A kernel
restriction does not. Without `AF_PACKET` the process **cannot open a raw socket**, so it is
incapable of capturing or injecting packets even if its code were changed to try. This is the
strongest control in the design and the one worth protecting in review.

**Consequence.** Check 8.6 in the hardware validation checklist verifies the sandbox actually
applies, because an unapplied sandbox is indistinguishable from a working one until tested.

---

## D22 — The installer does not touch boot configuration

**Decision.** `install-rpi.sh` never edits `/boot/firmware/config.txt` or any display overlay, and
never installs a kernel driver.

**Why.** Getting an ILI9341 working involves device-tree overlays specific to the exact wiring. An
installer that edits boot configuration can produce a Pi that does not boot, recoverable only by
pulling the card. The user's display already works; the risk is entirely one-sided.

**Consequence.** Display setup is a documented prerequisite. The installer's job is the
application, and `--dry-run` shows every action before any of it happens.

---

## D23 — `Wants=kismet.service`, not `Requires=`

**Decision.** A soft dependency.

**Why.** PiSight is *useful* when Kismet is down — showing OFFLINE is one of its jobs. A hard
dependency would stop PiSight at exactly the moment you most want to see that Kismet died.

---

## D24 — An uninstall script, because rollback docs must match reality

**Context.** `install-rpi.sh`'s summary referenced `scripts/uninstall-rpi.sh`, which did not exist.

**Decision.** Write it, with `--dry-run`, `--purge` and `--keep-user`, and a path guard that
refuses to delete anything other than `/opt/pisight`.

**Why.** Documentation referencing a non-existent file is worse than no documentation — it is
discovered at the moment of maximum stress. `/etc/pisight` is kept by default because it holds the
token, and `--purge` asks for confirmation.

---

## D25 — `.gitattributes` forcing LF

**Decision.** `* text=auto eol=lf`, with explicit entries for `.sh` and `.service`.

**Why.** Development is on Windows; deployment is on Linux. A CRLF shebang makes bash fail with
`bad interpreter: /usr/bin/env bash^M`, and systemd rejects a unit containing CR characters. Both
failures appear only on the Pi, are cryptic, and would otherwise be found in the field.

---

## D26 — Per-test pygame initialisation

**Context.** A session-scoped fixture was faster, but the CLI tests run the real application, which
calls `pygame.quit()` on teardown — invalidating the shared display and every loaded font for
every test that ran afterwards.

**Decision.** An autouse fixture ensures pygame is initialised before each test.

**Why.** A test suite whose results depend on file ordering is not a test suite. The extra
milliseconds per test are worth far more than that.

---

## D27 — `StrEnum` rather than `(str, Enum)`

**Decision.** Use `enum.StrEnum`, available since 3.10.

**Why.** Ruff's `UP042` flags the old idiom, and since 3.11 is the floor there is no reason to
carry it. Behaviour is equivalent for our uses.

---

## Open questions for after hardware validation

- Is 220 ms the right touch debounce for an XPT2046 panel? Unit-tested as logic; the *value* is a
  hardware question.
- Is a 2-second poll interval the right balance against Kismet's CPU on a real Pi 5? Chosen by
  reasoning, not measurement.
- Do 10 s stale and 30 s offline match real Kismet latency under load?
- Is 9px type actually legible at arm's length on a physical ILI9341? It is legible at 3x on a
  desktop monitor, which is not the same claim.

Each of these is a guess made in the absence of hardware. They are recorded here so they get
revisited with evidence rather than being mistaken for considered defaults.


## Follow-up verification decisions (2026-09-13)

- HTTP clients ignore ambient proxy variables: local observations and API cookies must
  travel directly to the configured Kismet host. Custom proxy deployment is not supported.
- A recent-time query does not bound device cardinality. Every HTTP response now has a
  4 MiB decoded-byte ceiling before JSON parsing; oversized responses fail gracefully
  and critical endpoint failures retain the last snapshot. This does not claim server pagination.
- The Overview NEW counter accumulates process-first sightings, while row NEW badges
  describe the current poll. Tracking remains capped at 4096 keys; a returning evicted
  key can be counted again, so totals after eviction are approximate. Restart resets both.
- Restart limits belong in systemd's Unit section. DRM device access uses the char-drm
  group rather than a directory path; actual device access remains hardware-unverified.
- Clone examples select the feature branch while PR #1 is unmerged.
