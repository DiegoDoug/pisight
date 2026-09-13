# Threat model

This document states what PiSight protects, from whom, and how. It is written for someone
deciding whether to deploy it, and for anyone changing the code who needs to know which
properties are load-bearing.

PiSight v0.1.0 (MVP).

## What PiSight is, in security terms

A read-only display client for a local Kismet instance. It has **no** capture capability, **no**
transmit capability, and **no** outbound network destination. It holds one credential (a Kismet
`readonly` API token) and renders data that originates over the air from parties it does not
control.

## Trust boundaries

```
   UNTRUSTED                 SEMI-TRUSTED              TRUSTED
   ─────────                 ────────────              ───────
   The air                   Kismet's REST API         PiSight's own config
   (device names,       ───▶ (well-formed-ish,    ───▶ (operator-authored,
    SSIDs, alert text)        may fail or lie)          root-owned)
```

The critical insight: **data crossing from Kismet into PiSight is untrusted.** Kismet itself is
trusted software, but the *content* it relays — SSIDs, device names, alert text — was written by
whoever was transmitting nearby. An attacker controls those strings completely.

## Assets

| # | Asset | Why it matters |
|---|---|---|
| A1 | The Kismet API token | Grants read access to everything Kismet has observed |
| A2 | Kismet's capture data on disk | May contain sensitive observations about a real location |
| A3 | The observations displayed on screen | MAC addresses, SSIDs, presence — personal data in many jurisdictions |
| A4 | The Pi's availability | A frozen or crashed dashboard is a failed instrument |
| A5 | Kismet's continued operation | PiSight must never disturb the capture engine |
| A6 | The operator's legal position | Using this where observation is not authorized is the top real-world risk |

## Threats and mitigations

### T1 — API token exposure

**Threat.** The token leaks through a log file, a crash report, a screenshot, a config file in a
support bundle, a request URL, or Git history. Anyone holding it can read everything Kismet has
seen.

**Mitigations.**

- The token is read from `PISIGHT_KISMET_API_TOKEN` at point of use and is **never a field on
  `AppConfig`**. It therefore cannot appear in a config dump, a `repr`, an exception, or the output
  of `pisight config`. `test_token_is_not_part_of_the_config_object` asserts this.
- It is transmitted only in the `KISMET` cookie, never in a URL or query string, so it cannot end
  up in an access log or a `Referer`. `test_token_is_sent_as_a_cookie_and_never_in_the_url` asserts
  this against every request the provider makes.
- `RedactingFilter` scrubs the live token value from every log record as a backstop against a
  future refactor or a library exception repr.
- `httpx` and `httpcore` loggers are held at WARNING; httpx logs full request URLs at INFO.
- Auth-failure messages name the *environment variable*, never the value.
- On the Pi the token lives in `/etc/pisight/pisight.env`, `root:root` mode `0600`. systemd reads
  it as root and injects it into the unprivileged process, so the `pisight` account cannot read it
  from disk. `verify-rpi.sh` checks the mode and ownership and fails if they are wrong.
- `.gitignore` excludes `pisight.env` and `*.env`. `test_no_plausible_credential_is_committed`
  scans the repository for credential-shaped assignments.

**Residual risk.** The token is in the process environment, readable via `/proc/<pid>/environ` by
root. Anyone who is already root on the Pi has larger capabilities than reading this token.

### T2 — Overly broad Kismet credentials

**Threat.** Using an `admin` token means a compromise of PiSight (or of the file holding its token)
hands the attacker control of the capture engine: reconfigure sources, change channels, stop
capture, alter alert definitions.

**Mitigations.**

- Documentation specifies the `readonly` role everywhere a token is mentioned, and explains why.
- PiSight never requires or stores the Kismet administrator password.
- Nothing in the code needs more than `readonly`. `test_no_kismet_write_or_control_endpoint_is_referenced`
  fails the build if a control path appears; `test_default_endpoints_are_all_read_paths` asserts
  every configured endpoint is a read.
- Only `GET` and `POST` are used, and `POST` only for Kismet's documented *query* endpoints, which
  take a JSON body solely to bound and simplify the response. `test_only_read_methods_are_used`
  enforces this.

**Residual risk.** An operator can still paste an `admin` token in. PiSight cannot detect the role
of a token it was given. The documentation is the control.

### T3 — Remote Kismet exposure

**Threat.** Pointing PiSight at a Kismet across a network sends the API token, and all observation
data, over that network. Plain HTTP means anyone on the path can read both.

**Mitigations.**

- The default is `http://127.0.0.1:2501`, loopback.
- Config validation rejects a `base_url` with embedded credentials.
- `test_no_outbound_upload_destination_is_configured` asserts every URL literal in the source is
  loopback.
- The deployment documentation states the loopback expectation explicitly.

**Residual risk.** An operator can point `base_url` anywhere. If you must, use HTTPS and treat the
token as exposed to that network. PiSight does not currently pin or verify a custom CA.

### T4 — Capture-data sensitivity

**Threat.** Kismet's logs record who was present, when, and where. That is sensitive personal data
in many jurisdictions and a surveillance record in all of them.

**Mitigations.**

- PiSight **never reads Kismet's log files.** It calls `statvfs` on the configured path purely to
  display free space, and never opens, moves, rotates or deletes anything there.
- PiSight has no export, upload or sync capability of any kind.
- `allow_uploads` and `enable_gps` are validated as `false`; PiSight refuses to start if either is
  set true. They exist to make the guarantee explicit and to fail loudly if a future change relaxes
  it.

### T5 — Physical device loss

**Threat.** A small Pi with a screen is easy to lose or steal. It carries the API token and,
through Kismet, the capture history.

**Mitigations (partial).**

- MAC addresses are masked to their last four hex characters by default, so a photograph of the
  screen — or a screenshot committed to a repository — is not identifying.
- The token is `0600` root-only, so casual access to the SD card as a normal user does not yield it.
- PiSight stores nothing itself. Its own loss discloses nothing beyond the token.

**Residual risk — significant and unmitigated by PiSight.** Physical possession of the storage
media yields the token and all of Kismet's capture data. **If the observations matter, use full-disk
encryption.** PiSight cannot help here and does not pretend to.

### T6 — Malicious or malformed SSIDs and device names

**Threat.** An attacker chooses their SSID. They can make it 300 characters, fill it with ANSI
escape sequences, embed newlines to forge log entries, or use right-to-left overrides to make text
render deceptively.

**Mitigations.**

- Every string from Kismet passes through `sanitize_text()` before rendering or logging: C0/C1
  control characters and bidi overrides become `.`, newlines and tabs collapse to spaces, runs of
  whitespace collapse, and length is bounded.
- Rendering is additionally width-bounded: every text call passes a `max_width` and ellipsizes
  against the actual font metrics. A long SSID cannot overflow the 320-pixel canvas.
- `RedactingFilter` flattens every log record to one line, so a crafted name cannot forge a journal
  entry.
- `test_screens_render_hostile_content` renders 60 devices with 300-character bidi-override names,
  40 alerts with 500-character bodies and billion-count channels, and asserts the canvas is still
  exactly 320x240.

### T7 — Untrusted Kismet response data

**Threat.** A compromised, buggy, or version-mismatched Kismet returns structurally unexpected
JSON. Naive parsing crashes the dashboard, or worse, renders wrong data as if it were right.

**Mitigations.**

- All parsing is tolerant by construction: absent optional fields, unknown device types,
  out-of-range severities and mixed numeric encodings all normalize rather than raise.
- Structurally invalid responses raise `MalformedResponse`, and the coordinator retains the
  previous snapshot rather than displaying nothing.
- **An object where a list was expected is rejected, not read as one empty record.** Rendering a
  Kismet error body as "no devices observed" would tell the operator the air is quiet when in fact
  the query failed — a silent false negative is the worst outcome for a reconnaissance instrument.
- Any unexpected exception from a provider is caught by the coordinator, logged, and treated as a
  failed poll.

### T8 — Resource exhaustion

**Threat.** A very busy RF environment (a conference, a dense apartment block, or a deliberate
flood of thousands of fake beacons) makes Kismet report far more devices, channels and alerts than
the Pi can hold.

**Mitigations.** Bounds at four levels — query window, field simplification, parse-time caps, and
a single-slot store. `NewDeviceTracker` evicts oldest-first past 4096 keys. The systemd unit sets
`MemoryMax=256M` and `TasksMax=32`, so a regression in any of those becomes a restart rather than
an OOM that takes Kismet down with it.

**Residual risk.** Under a beacon flood the Devices screen shows a sample, not the whole picture.
That is the intended trade-off — see Known Limitations in the README.

### T9 — Log injection

**Threat.** Device names reach the journal. Newlines let an attacker forge entries that a human
reading `journalctl`, or a downstream log parser, takes at face value.

**Mitigations.** `RedactingFilter` sanitizes and flattens **every** record to a single line, not
just the ones PiSight formats deliberately. Tested by
`test_log_records_are_flattened_to_one_line`.

### T10 — Accidental upload or commit of observations

**Threat.** A developer commits a real capture, a screenshot containing real SSIDs, or a fixture
built from a real environment. Once pushed, it is public and effectively permanent.

**Mitigations.**

- `.gitignore` excludes `*.kismet`, `*.pcap`, `*.pcapng`, `*.env` and `artifacts/`.
- **Every fixture is synthetic.** All MACs use the locally-administered `02:` prefix, which is
  never vendor-assigned and cannot collide with real hardware. All SSIDs are invented.
  `test_fixture_macs_are_all_locally_administered` and `test_mock_never_emits_a_routable_vendor_mac`
  enforce this.
- Committed screenshots are rendered from the deterministic mock provider, so they contain no real
  observation.
- `test_no_capture_data_is_committed` fails the build if a capture file appears anywhere.

### T11 — Privilege escalation through PiSight

**Threat.** A vulnerability in PiSight or a dependency gives an attacker code execution as the
PiSight process.

**Mitigations.**

- Runs as an unprivileged `pisight` account with no shell, no home directory and no sudo.
  `verify-rpi.sh` fails if that account has administrative group membership.
- `CapabilityBoundingSet=` and `AmbientCapabilities=` are **empty**: the process holds no Linux
  capability at all, in particular not `NET_RAW` or `NET_ADMIN`, which putting an interface into
  monitor mode would require.
- `RestrictAddressFamilies` omits `AF_PACKET`. **The process cannot open a raw socket even if its
  code were changed to try.** This is the strongest single control in the design: the passive-only
  promise is enforced by the kernel, not by our good intentions.
- `ProtectSystem=strict` with no `ReadWritePaths=`: the entire filesystem is read-only to PiSight.
- `NoNewPrivileges`, `ProtectKernelModules`, `ProtectKernelTunables`, `RestrictNamespaces`,
  `RestrictSUIDSGID`, `LockPersonality`, and a `@system-service` syscall filter.
- Two runtime dependencies only (`pygame-ce`, `httpx`). A small dependency tree is a small supply
  chain.
- `doctor` is the only component that runs external commands. It uses argument lists — never
  `shell=True` — with short timeouts, and an allowlist test restricts it to `lsusb` and `iw list`.
  `test_subprocess_is_only_imported_by_the_doctor` prevents that spreading.

### T12 — Unauthorized observation (the real-world risk)

**Threat.** PiSight makes passive Wi-Fi observation easy and portable. Used where observation is
not authorized, it exposes the operator to serious legal consequences, regardless of how carefully
the software is written.

**Mitigations.** Documentation only, and documentation is a weak control. The authorization notice
appears in the README, in `SECURITY.md`, in `PRODUCT_SCOPE.md`, and in the CLI's own `--help`.

**This is the residual risk we can do least about and that matters most. Get written permission
before you deploy this, and know the law where you are.**

## Out of scope

- Attacks against Kismet itself (a separate project with its own model).
- Attacks against the Raspberry Pi OS, its kernel, or its firmware.
- Physical tampering with the Pi or the SD card.
- RF-layer attacks against the observing adapter.
- Anything arising from modifying PiSight to add offensive capability. The guards in
  `tests/test_safety.py` prevent accidental introduction, not a determined fork.

## Reporting

See [SECURITY.md](../SECURITY.md).
