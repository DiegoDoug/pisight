# MVP acceptance record

A requirement-by-requirement review of PiSight v0.1.0 against its specification.

**Legend:** ✅ done and verified · ⚠️ done with a caveat · ⏳ blocked on hardware · ❌ not done

**Verification environment:** Windows 11 desktop, Python 3.11.9 (matching the Raspberry Pi OS
Bookworm target), SDL dummy video driver. **No Raspberry Pi, display, touch panel or Wi-Fi adapter
was involved in any check below.**

---

## 1. Passive-only and authorization boundary

| Requirement | Status | Evidence |
|---|---|---|
| No packet injection | ✅ | No capture/transmit code exists. `test_no_raw_socket_or_packet_capture_import` forbids `socket`, `scapy`, `pcapy`, `pyshark`, `dpkt` imports package-wide |
| No deauth / disassociation | ✅ | Not implemented. `test_no_offensive_tool_is_referenced_in_code` |
| No active probing, auth attempts, association | ✅ | PiSight has no radio access path at all |
| No cracking / credential collection / exploitation / spoofing | ✅ | `FORBIDDEN_TOOLS` guard covers aircrack, wifite, airgeddon, bettercap, hashcat, reaver and others |
| No arbitrary shell from the UI | ✅ | `test_no_call_uses_shell_true`, `test_no_shell_helper_is_called`, `test_subprocess_is_only_imported_by_the_doctor` |
| No Kismet configuration mutation | ✅ | `test_no_kismet_write_or_control_endpoint_is_referenced`, `test_default_endpoints_are_all_read_paths`, `test_only_read_methods_are_used` |
| No automatic upload | ✅ | `allow_uploads` validated false; `test_no_outbound_upload_destination_is_configured` asserts every URL literal is loopback |
| No OS shutdown or data deletion | ✅ | No such code. Kismet logs are only `statvfs`-ed, never opened |
| Kismet integration is read-only | ✅ | Only GET, plus POST to Kismet's documented *query* endpoints. `test_only_read_methods_are_used` |
| Uses a `readonly` API key | ✅ | Documented in README, SECURITY.md, RPI5_DEPLOYMENT.md, `pisight.env.example` |
| Never requires the admin password | ✅ | No password field exists anywhere |
| No offensive tools added | ✅ | `test_no_offensive_runtime_dependency_is_declared` |
| Authorization notice | ✅ | README (prominent), SECURITY.md, PRODUCT_SCOPE.md, and `pisight --help` |

**Beyond the requirement:** the systemd unit drops every Linux capability and omits `AF_PACKET`
from `RestrictAddressFamilies`, so the deployed process is *kernel-prevented* from opening a raw
socket. Verification that the sandbox actually applies is check 8.6 of the hardware checklist. ⏳

---

## 2. MVP outcome

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | 320x240 desktop simulator with mock data | ✅ | `pisight --mode mock`; 151 frames in a 5 s smoke run |
| 2 | Four finished screens | ✅ | Overview, Channels, Devices, Alerts/System — see `docs/screenshots/` |
| 3 | Reads live Kismet REST in live mode | ✅ | `KismetDashboardProvider`, 40 tests against `httpx.MockTransport` |
| 4 | Operates when Kismet is unavailable | ✅ | `test_failure_retains_the_previous_snapshot_and_marks_it_offline` |
| 5 | Mouse, keyboard and SDL touch input | ✅ | `test_keyboard_navigation`, `test_window_coordinates_translate_to_canvas_coordinates`, FINGERDOWN handling |
| 6 | Pi installation and systemd assets | ✅ | `scripts/`, `systemd/pisight.service`, `config/` |
| 7 | Read-only hardware diagnostic | ✅ | `pisight doctor`, `pisight doctor --json` |
| 8 | Tests, CI, documentation, screenshots | ✅ | 515 tests, 89% coverage, GitHub Actions, 9 documents, 4 PNGs |
| 9 | Ready to clone and test on the Pi | ✅ | Instructions verified for internal consistency; **not executed on hardware** ⏳ |
| 10 | Hardware validation marked pending | ✅ | README banner, `HARDWARE_VALIDATION.md`, `RPI5_DEPLOYMENT.md` banner |

---

## 3. Technology

| Requirement | Status | Notes |
|---|---|---|
| Python 3.11+ | ✅ | `requires-python = ">=3.11"`; dev venv is 3.11.9 |
| pygame-ce imported as pygame | ✅ | pygame-ce 2.5.8 |
| httpx | ✅ | 0.28.1 |
| dataclasses and typing | ✅ | All models frozen dataclasses; mypy strict passes |
| tomllib | ✅ | `config.py` |
| pytest, pytest-cov | ✅ | Dev extra |
| Ruff | ✅ | Format + lint, both clean |
| mypy | ✅ | `strict = true`, clean on 35 files |
| setuptools via pyproject | ✅ | `python -m build` produces wheel and sdist |
| stdlib logging | ✅ | `logging_setup.py` |
| Minimal runtime deps | ✅ | Exactly two: pygame-ce, httpx |
| No web framework / browser / Node / DB / Redis / Docker / Electron / cloud | ✅ | `test_no_offensive_runtime_dependency_is_declared` plus dependency list |

---

## 4. Architecture

| Requirement | Status | Location |
|---|---|---|
| src layout | ✅ | `src/pisight/` |
| Domain models | ✅ | `models.py` |
| Provider protocols | ✅ | `providers/base.py` |
| Live Kismet provider | ✅ | `providers/kismet.py` |
| Mock provider | ✅ | `providers/mock.py` |
| Host-health provider | ✅ | `health/host.py` |
| Immutable snapshots | ✅ | All frozen dataclasses with `slots=True` |
| Background polling coordinator | ✅ | `polling/coordinator.py` |
| Snapshot store | ✅ | `polling/store.py` |
| Pygame rendering | ✅ | `ui/` |
| Screens and components | ✅ | `screens/`, `ui/widgets.py` |
| Input/navigation | ✅ | `ui/navigation.py` |
| Configuration | ✅ | `config.py` |
| CLI and diagnostics | ✅ | `cli.py`, `doctor.py` |
| All 7 required models | ✅ | DeviceSummary, ChannelSummary, AlertSummary, DatasourceSummary, HostHealth, CaptureStatus, DashboardSnapshot |
| `DashboardProvider` protocol | ✅ | Both providers implement it |
| UI cannot tell the source apart | ✅ | Screens receive only `DashboardSnapshot` |

---

## 5. Networking and concurrency

| Requirement | Status | Evidence |
|---|---|---|
| No HTTP/filesystem work on the render thread | ✅ | All I/O in `PollingCoordinator`; `app.py` only calls `store.get()` |
| One supervised background worker | ✅ | Single daemon thread; `test_start_twice_creates_only_one_thread` |
| Immutable snapshots via a bounded store | ✅ | `SnapshotStore.CAPACITY == 1` |
| Renderer consumes newest, discards obsolete | ✅ | `test_store_is_bounded_to_a_single_slot`: 1000 published, 999 dropped, 1 retained |
| Bounded memory | ✅ | Single slot + capped device/alert lists + bounded `NewDeviceTracker` + `MemoryMax=256M` |
| HTTP connection reuse | ✅ | One long-lived `httpx.Client` with keepalive limits |
| Configurable connect and read timeouts | ✅ | `[http]` section; `test_base_url_and_timeouts_are_taken_from_configuration` |
| Exponential backoff with jitter | ✅ | `test_backoff_grows_exponentially_without_jitter`, `test_backoff_jitter_stays_within_the_configured_band` |
| Maximum backoff | ✅ | `test_backoff_is_capped_at_the_maximum` |
| Clean cancellation | ✅ | `test_a_persistently_failing_provider_stops_promptly` — stops in < 2 s despite a 5 s backoff |
| Graceful exit | ✅ | SIGINT/SIGTERM handlers; `test_the_app_tears_down_cleanly_and_can_start_again` |
| Last-known snapshot retained on failure | ✅ | `test_failure_retains_the_previous_snapshot_and_marks_it_offline` |
| Visible stale/offline state | ✅ | Status bar banner + Alerts/System panel |
| No busy loops | ✅ | `Event.wait` only; `test_polling_does_not_busy_loop` |
| No unbounded device/alert collection | ✅ | Time window + field simplification + client-side caps |

---

## 6. Kismet integration

| Requirement | Status | Notes |
|---|---|---|
| System timestamp and connectivity | ✅ | `/system/timestamp.json` |
| System status | ✅ | `/system/status.json` |
| Packet-rate statistics | ✅ | `/packetchain/packet_stats.json` |
| Channels | ✅ | `/channels/channels.json` |
| Datasource state and channel | ✅ | `/datasource/all_sources.json` |
| Bounded/paginated device summaries | ✅ | `/devices/views/{view}/last-time/{-N}/devices.json` with `fields` |
| Recent alerts | ✅ | `/alerts/last-time/{-N}/alerts.json` |
| Field simplification and bounded views | ✅ | `DEVICE_FIELDS`; `test_device_query_is_bounded_and_field_simplified` |
| Never an unbounded all-devices response | ✅ | Same test asserts the path is never `/devices/all_devices` |
| Token from `PISIGHT_KISMET_API_TOKEN` | ✅ | `get_api_token()` |
| Sent via Kismet's cookie mechanism | ✅ | `KISMET` cookie |
| Token never in logs/screenshots/URLs/config/exceptions/git | ✅ | Five separate tests; see §10 |
| Endpoint paths configurable | ✅ | `[kismet.endpoints]`; `test_endpoints_are_configurable_without_code_changes` |
| Parsers tolerate absent optional fields | ✅ | `test_parse_devices_tolerates_partial_records` |
| Handle unknown device types | ✅ | `test_normalize_device_type` with `"Wi-Fi Sasquatch"` |
| Normalize channels and frequencies | ✅ | kHz/MHz/GHz input; 2.4/5/6 GHz channel derivation |
| Normalize signal values | ✅ | `0` treated as "never measured"; implausible values rejected |
| Reject fundamentally malformed responses | ✅ | `MalformedResponse`; see decision D7 |
| Avoid KeyError crashes | ✅ | `test_no_parser_raises_key_error_on_empty_input` |
| Preserve last valid snapshot after a bad response | ✅ | `test_failure_retains_the_previous_snapshot_and_marks_it_offline` |
| Synthetic fixtures only | ✅ | `test_fixture_macs_are_all_locally_administered`; `mock/fixtures/README.md` |

⚠️ **Caveat.** Endpoint *paths* were confirmed against the current official Kismet API
documentation. Some *field names* inside channel and datasource records are not published there,
so those parsers were written tolerantly (multiple candidate keys per value, graceful fallback) and
the endpoint paths were made configurable. This is a deliberate mitigation, but the live field
names remain unverified until section 5 of the hardware checklist runs. ⏳

---

## 7. Configuration

| Requirement | Status |
|---|---|
| TOML file + environment overrides | ✅ |
| Search order: `--config` → `PISIGHT_CONFIG` → `/etc/pisight/config.toml` → defaults | ✅ `test_explicit_path_wins_over_env_and_system` and three companions |
| `config/pisight.example.toml` with all required sections | ✅ mode, display, kismet + endpoints, polling, http, privacy, storage, logging |
| Defaults: 320x240, landscape, 30 FPS, mock, localhost, env-only token, no GPS, no uploads, MACs hidden, no deletion | ✅ `test_defaults_match_the_product_specification` |

---

## 8. UI specification

| Requirement | Status | Evidence |
|---|---|---|
| Exactly 320x240 logical canvas | ✅ | `test_canvas_is_exactly_320x240` |
| 24px status / 176px content / 40px nav | ✅ | `test_the_three_bands_match_the_specification_and_tile_the_canvas` |
| Scales up preserving the coordinate system | ✅ | Integer nearest-neighbour; `test_window_coordinates_translate_to_canvas_coordinates` |
| Near-black background, high-contrast white text | ✅ | `theme.py` |
| Cyan normal, amber warning, red critical, gray secondary | ✅ | `theme.py`, `severity_color()` |
| Flat, no gradients, no heavy transparency | ✅ | Only `draw.rect`/`circle`/`polygon`/`line`/`arc` |
| No network-downloaded assets | ✅ | Icons drawn with primitives; no image files |
| DejaVu Sans with safe fallback | ✅ | `fonts.py`; falls back through a candidate list to pygame's built-in |
| Minimum 40x40 touch targets | ✅ | Nav buttons are 80x40; `test_every_nav_button_meets_the_minimum_touch_target` |
| No virtual keyboard, no hover-only, no h-scroll | ✅ | None implemented |
| Long labels ellipsized | ✅ | `test_ellipsize_shortens_to_fit_and_marks_the_cut`; visible in `devices.png` |
| No text clipping | ✅ | Every `draw_text` call is width-bounded; screenshots inspected |
| Colour is not the only status indicator | ✅ | REC/IDLE text, severity words, `>` channel marker, filled vs hollow dot, stepped signal glyph |

### Screen contents

| Screen | Required elements | Status |
|---|---|---|
| Status bar | Name, capture state, channel/hop, time, CPU temp, stale/offline | ✅ all present |
| Overview | APs, clients, recent devices, new since start, pkt/s, 2.4/5 GHz, alert count, channel visualization, newest observation | ✅ all present |
| Channels | Separate 2.4/5 GHz, per-channel counts, current channel, busiest, compact bars with numeric labels | ✅ all present; read-only, no controls |
| Devices | ≤5 rows; name/SSID, type, signal, channel, security, new/known, last-4 MAC | ✅ all present; `MAX_ROWS == 5` |
| Alerts/System | Recent alerts, severity label + icon, datasource, Kismet state, free storage %, CPU temp, uptime, snapshot age | ✅ all present |

✅ "New" documented as resetting on restart — README Known Limitations, `providers/base.py`
docstring, and the Overview tile label.
✅ Kismet severities mapped to INFO/LOW/MEDIUM/HIGH/CRITICAL (`test_parse_alerts_maps_severity_and_bounds_the_result`).
✅ Raw packet content never exposed — only Kismet's own alert description text is rendered.

---

## 9. Input, mock mode, host health, diagnostics

| Requirement | Status | Evidence |
|---|---|---|
| Mouse click | ✅ | `MOUSEBUTTONDOWN` handling |
| SDL touch as pointer | ✅ | `FINGERDOWN` with normalised-coordinate conversion |
| Keys 1–4 | ✅ | `test_keyboard_navigation` (including keypad) |
| Left/Right | ✅ | Same test; wraps |
| Escape exits in development mode | ✅ | `test_escape_does_not_exit_in_fullscreen` confirms it is ignored on the Pi |
| Touch debounce | ✅ | 4 dedicated tests including the SDL synthetic-mouse case |
| No gestures required | ✅ | None implemented |
| Mock: channels, device types, packet rate, signals, arrivals, severities, healthy source | ✅ | 12 tests in `test_mock_provider.py` |
| Mock: Kismet disconnected, stale, malformed recovery, low storage | ✅ | 4 `simulate_*` methods, each tested |
| Deterministic seeds + optional evolving mode | ✅ | `test_deterministic_seed_reproduces_identical_snapshots` |
| CPU temp from sysfs | ✅ | 5 tests including milli-degree conversion and implausible values |
| Storage via `shutil.disk_usage` | ✅ | `test_disk_usage_walks_up_to_an_existing_parent` |
| Process uptime, architecture, platform, display visibility | ✅ | `test_collect_returns_a_populated_health_record` |
| Fallback "unavailable" off-Pi | ✅ | Verified — the whole suite runs on Windows |
| No root required | ✅ | Only reads |
| `pisight doctor` and `--json` | ✅ | `test_doctor_command_runs_off_pi_without_crashing`, `test_doctor_json_output_is_valid_and_token_free` |
| All 14 required doctor checks | ✅ | OS, arch, Python, display env, /dev/dri, /dev/fb, SDL, input devices, USB, wireless, drivers, monitor-mode support, Kismet API + auth, storage path, free space, groups |
| Read-only, no `shell=True`, short timeouts, missing executables handled | ✅ | `test_every_external_command_is_read_only` allowlists only `lsusb` and `iw list` |
| Auth result without printing the token | ✅ | `test_doctor_reports_token_presence_without_revealing_it` |

---

## 10. Security and privacy

| Threat | Status | Mitigation |
|---|---|---|
| API-token exposure | ✅ | Not in the config object, cookie-only transport, redacting log filter, `0600` env file. 5 tests |
| Overly broad credentials | ⚠️ | Documented `readonly` requirement; PiSight cannot verify a token's role — documented as a known limitation |
| Remote Kismet exposure | ✅ | Loopback default, credential-in-URL rejected, documented |
| Capture-data sensitivity | ✅ | Never reads Kismet logs; no export path |
| Physical device loss | ⚠️ | MAC masking and file permissions help; full-disk encryption is documented as the operator's responsibility |
| Malicious/malformed SSIDs | ✅ | `sanitize_text` + width-bounded rendering; `test_screens_render_hostile_content` |
| Untrusted Kismet responses | ✅ | Tolerant parsers, explicit rejection of wrong-shaped payloads |
| Resource exhaustion | ✅ | Four levels of bounding + `MemoryMax`/`TasksMax` |
| Log injection | ✅ | Every record flattened to one line at the handler |
| Accidental upload/commit of observations | ✅ | `.gitignore`, synthetic-only fixtures, 3 tests |
| Privilege separation | ✅ | Unprivileged user, zero capabilities, no `AF_PACKET`, read-only filesystem |
| Control characters sanitized before render and log | ✅ | `test_sanitize.py` (18 tests) |

---

## 11. Testing and quality gates

Executed on Windows 11, Python 3.11.9, `SDL_VIDEODRIVER=dummy`:

| Gate | Result |
|---|---|
| `python -m pip install -e ".[dev]"` | ✅ |
| `ruff format --check .` | ✅ 59 files already formatted |
| `ruff check .` | ✅ All checks passed |
| `mypy src` | ✅ no issues in 35 source files |
| `pytest` | ✅ **515 passed**, 0 failed |
| `python -m build` | ✅ wheel + sdist |
| `bash -n scripts/*.sh` | ✅ all three clean |
| `pisight --mode mock --smoke-seconds 5` | ✅ exit 0, 151 frames |
| `pisight screenshots --output artifacts/screenshots` | ✅ exit 0, four 320x240 PNGs |

**Coverage: 89%** (2787 statements). Critical modules: store 100%, sanitize 100%, logging 100%,
devices screen 100%, models 99%, alerts screen 99%, navigation 99%, parsers 94%, coordinator 95%,
mock 97%, backoff 96%.

⚠️ `doctor.py` sits at 59% because most of its branches read Linux-only paths (`/dev/dri`,
`/sys/class/net`, `grp`) that cannot execute on the Windows development host. Those branches run
for the first time during hardware validation. No test was weakened or excluded to reach any of
these figures.

**Required test topics — all present:** configuration precedence · missing env token · realistic
namespaced fixtures · absent/unknown fields · malformed JSON · HTTP 401/403 · connection timeout ·
reconnect and backoff · snapshot staleness · bounded queues · deterministic mock · device
classification · MAC masking · SSID sanitization and ellipsis · severity mapping · channel
normalization · UI navigation · touch debounce · layout bounds · empty-state rendering · all four
screens at exactly 320x240 · clean shutdown.

✅ No brittle full-image golden tests (decision D19).

---

## 12. CI and repository

| Requirement | Status |
|---|---|
| Runs on PRs and pushes to main | ✅ |
| Includes Python 3.11 | ✅ Matrix 3.11/3.12/3.13/3.14; 3.11 required, 3.14 advisory |
| Caches dependencies | ✅ `actions/setup-python` with `cache: pip` |
| Format, lint, mypy, tests | ✅ |
| Builds the package | ✅ Plus an install-and-run check of the built wheel |
| Headless UI smoke test | ✅ |
| Screenshots as artifacts | ✅ Plus a job step asserting each is exactly 320x240 |
| No credentials or hardware needed | ✅ |
| Least-privilege permissions | ✅ `permissions: contents: read` |
| Repository structure | ✅ Matches the specified layout |
| Documentation complete | ✅ 9 documents |

---

## 13. Final self-review

| Check | Result |
|---|---|
| Repository searched for TODO/FIXME/placeholders | ✅ None in code. `test_no_todo_or_placeholder_markers_remain` enforces it |
| Searched for secrets | ✅ Only clearly-labelled synthetic test values |
| Searched for unsafe shell invocation | ✅ None; only docstrings disclaiming it and the tests forbidding it |
| All MVP-impacting findings resolved | ✅ See below |
| All quality gates re-run after fixes | ✅ |
| All four screenshots inspected | ✅ Visually reviewed; three defects found and fixed |
| No content clips or overflows 320x240 | ✅ Verified visually and by `test_a_screen_never_draws_outside_the_content_band` |
| All Kismet operations read-only | ✅ Three dedicated tests |
| Hardware claims marked NOT YET VERIFIED | ✅ README banner, HARDWARE_VALIDATION.md, RPI5_DEPLOYMENT.md |
| Install and rollback instructions match the actual files | ✅ Every referenced path checked to exist; `uninstall-rpi.sh` written because ROLLBACK.md referenced it |

### Defects found during self-review and fixed

1. **Channels screen normalised each band against its own peak** — a 1-device 5 GHz channel drew a
   bar as long as a 4-device 2.4 GHz channel, reading as equally busy. Fixed with a shared peak
   across both columns. *Found by looking at a screenshot, not by a test.*
2. **Device and alert ages rendered as "199d13h"** — the mock provider's deterministic clock and
   the screenshot renderer's fixed clock were unrelated constants. Unified into
   `DETERMINISTIC_WALL_TIME`.
3. **"SNAPSHOT" label clipped to "SNAPSH…"** — the system panel sized its label column against
   `"STORAGE"`. Fixed to measure the longest label actually drawn.
4. **Overview channel bars rendered as solid blocks** — bar width was nearly the full slot. Halved.
5. **A malformed Kismet response yielded zero devices instead of an error** — an error object was
   being read as one empty record, so a failed query displayed as "no devices observed". Now
   rejected. *This was the most serious finding: a silent false negative.*
6. **`install-rpi.sh` referenced a non-existent uninstaller.** Written.
7. **Two self-review tests were naive substring matches** that flagged PiSight's own disclaiming
   prose. Rewritten as AST-based checks.
8. **A tautological assertion** (`assert x is False or True`) in a mock test. Replaced with real
   timing assertions.

---

## 14. Outstanding

| Item | Status |
|---|---|
| Hardware validation, sections 1–8 | ⏳ Blocked — no Pi, display, touch panel or adapter available in this environment |
| Live Kismet field-name confirmation for channel/datasource records | ⏳ Mitigated by tolerant parsing and configurable endpoints; confirm in hardware section 5 |
| Real performance figures (FPS, CPU, memory, thermals) | ⏳ All targets in section 7 of the checklist are estimates |
| Touch debounce value tuned to a real XPT2046 panel | ⏳ 220 ms chosen by reasoning |
| 9px type legibility at arm's length on a physical ILI9341 | ⏳ Verified at 3x on a desktop monitor, which is a different claim |

---

## Verdict

**The non-hardware MVP is complete.** Every requirement that can be verified without a Raspberry
Pi has been implemented and verified. All nine local quality gates pass. The four screens are
finished, navigable and render at exactly 320x240. The passive-only boundary is enforced by tests
and, in deployment, by the kernel.

**The MVP is not hardware-validated, and this repository does not claim otherwise.**
`docs/HARDWARE_VALIDATION.md` is the checklist that closes the remaining gap.
