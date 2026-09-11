# Hardware validation checklist

> ## STATUS: NOT YET VERIFIED
>
> **No part of PiSight has run on real hardware.** Everything in this repository was developed and
> tested on a desktop with the SDL dummy video driver. The full test suite passes, the four screens
> render at exactly 320x240, the Kismet parsers are exercised against synthetic fixtures, and the
> shell scripts are syntax-checked — all off-hardware.
>
> **Nothing here has touched a Raspberry Pi 5, an ILI9341 panel, an XPT2046 touch controller, or a
> Panda PAU0B adapter.**
>
> Every hardware-dependent claim in this repository is *designed intent*, not measured fact. This
> document is the checklist that converts intent into fact.

## How to use this

Work through it at the Pi in order; later sections assume earlier ones passed. Record the result
of each item. When a section is complete, update its status line here and commit the change, so
the repository's claims always match reality.

**Report format for each item:** `PASS` / `FAIL` / `PARTIAL` / `N/A`, with what you actually
observed. "Looks fine" is not a result; "all four screens render, text legible at 60 cm, no
clipping" is.

---

## Section 1 — Base system

**Status: NOT VERIFIED**

| # | Check | How | Expected |
|---|---|---|---|
| 1.1 | Board is a Pi 5 | `cat /proc/device-tree/model` | `Raspberry Pi 5 ...` |
| 1.2 | 64-bit OS | `uname -m` | `aarch64` |
| 1.3 | Python version | `python3 --version` | 3.11 or newer |
| 1.4 | NVMe present and mounted | `lsblk`, `df -h` | NVMe visible with free space |
| 1.5 | Kismet installed | `kismet --version` | A version prints |
| 1.6 | Kismet running | `systemctl status kismet` | `active (running)` |
| 1.7 | Kismet API answers | `curl -s http://127.0.0.1:2501/system/timestamp.json` | JSON with a timestamp |

---

## Section 2 — Capture adapter (PAU0B)

**Status: NOT VERIFIED**

| # | Check | How | Expected |
|---|---|---|---|
| 2.1 | Adapter enumerates on USB | `lsusb` | The PAU0B appears |
| 2.2 | Interface exists | `ip -o link` / `ls /sys/class/net` | A `wlan*` interface |
| 2.3 | Driver bound | `pisight doctor` (driver section) | A driver name, not `unknown` |
| 2.4 | Monitor mode supported | `iw list \| grep -A10 'Supported interface modes'` | `monitor` listed |
| 2.5 | Kismet has it as a source | Kismet UI → Data Sources | Source present, running |
| 2.6 | Packets are being seen | Kismet UI | Packet count climbing |
| 2.7 | USB power is adequate | Watch for resets in `dmesg -w` | No disconnect/reset storms |

> **Note on 2.4.** `pisight doctor` reports whether monitor mode *appears* supported by reading
> `iw list`. It never enables it. Putting the adapter into monitor mode is Kismet's job.

**Known limitation to confirm in practice:** one PAU0B listens on one channel at a time. Expect
channel hopping and expect gaps. Verify that the Channels screen's counts look like a *sample*
consistent with the hop pattern, not a census.

---

## Section 3 — Display (ILI9341, 320x240 landscape)

**Status: NOT VERIFIED — the highest-risk section**

Establish your deployment profile first (see [RPI5_DEPLOYMENT.md](RPI5_DEPLOYMENT.md)).

| # | Check | How | Expected |
|---|---|---|---|
| 3.1 | Panel works before PiSight | Console text or desktop visible | Display is already functional |
| 3.2 | Device nodes | `ls -l /dev/dri/ /dev/fb*` | At least one node present |
| 3.3 | Which driver works | Try `kmsdrm`, then `fbcon`, then `x11` | One of them opens a display |
| 3.4 | Mock mode renders | `sudo -u pisight SDL_VIDEODRIVER=<driver> /opt/pisight/venv/bin/pisight --mode mock --fullscreen --smoke-seconds 15` | Dashboard visible for 15 s |
| 3.5 | Orientation is landscape | Observe | 320 wide, 240 tall, not rotated |
| 3.6 | No scaling artifacts | Observe | 1:1 pixels, crisp 1px dividers |
| 3.7 | Colours are correct | Observe | Cyan/amber/red distinct; no red/blue swap |
| 3.8 | Nothing is clipped | Check all four screens | No text past any edge |
| 3.9 | Legible at arm's length | Stand ~60 cm back | All four screens readable |
| 3.10 | Full-screen leaves no cursor | Observe | No mouse pointer artifact |

> **3.7 is worth care.** A BGR/RGB mismatch in the panel's overlay configuration shows up as
> swapped red and blue. PiSight's palette makes this obvious: the record dot and critical alerts
> should be **red**, normal activity **cyan**. If those are swapped, the panel configuration is
> wrong, not PiSight.

**If the display stays blank**, this is the failure to expect. Work through the blank-screen
section of [RPI5_DEPLOYMENT.md](RPI5_DEPLOYMENT.md). Record which `SDL_VIDEODRIVER` value finally
worked — that is the single most valuable result from this whole document.

---

## Section 4 — Touch input (XPT2046)

**Status: NOT VERIFIED**

| # | Check | How | Expected |
|---|---|---|---|
| 4.1 | Event device exists | `ls -l /dev/input/event*` | At least one |
| 4.2 | Touches produce events | `sudo evtest` on the touch device | Coordinates on press |
| 4.3 | `pisight` can read it | `id pisight` | Includes `input` |
| 4.4 | Taps switch screens | Tap each of the four nav buttons | Correct screen each time |
| 4.5 | Coordinates are not inverted | Tap the leftmost button | Overview, not Alerts |
| 4.6 | Debounce works | Tap once, firmly | Advances exactly one screen |
| 4.7 | Content taps do nothing | Tap the middle of the screen | No screen change |
| 4.8 | Targets are comfortable | Tap with a fingertip, not a stylus | Reliable hits |

> **4.5.** If the leftmost tap selects the rightmost screen, the touch axes are inverted or
> swapped. That is a touch-controller calibration issue in your overlay, not a PiSight bug —
> PiSight receives whatever coordinates SDL hands it.
>
> **4.6.** The debounce window is 220 ms and is unit-tested as logic. Whether it is the right value
> for *your* panel is a hardware question. If one press still advances two screens, raise it; if
> deliberate rapid taps are being swallowed, lower it.

---

## Section 5 — Live Kismet integration

**Status: NOT VERIFIED**

| # | Check | How | Expected |
|---|---|---|---|
| 5.1 | Token accepted | `pisight doctor` (kismet section) | `token accepted (readonly role)` |
| 5.2 | Live mode starts | `pisight --mode live --smoke-seconds 15` | No auth or connection errors |
| 5.3 | Real devices appear | Devices screen | Plausible nearby devices |
| 5.4 | Device types are sane | Devices screen | APs as `AP`, clients as `STA` |
| 5.5 | Channels look right | Channels screen | Activity on real local channels |
| 5.6 | Packet rate is non-zero | Overview | A plausible rate |
| 5.7 | Datasource state correct | Alerts/System | Interface name and `HOP` |
| 5.8 | Free storage is right | Alerts/System vs `df -h` | Figures agree |
| 5.9 | CPU temperature is right | Alerts/System vs `vcgencmd measure_temp` | Figures agree |
| 5.10 | Alerts appear if any fire | Alerts/System | Severity mapping looks sensible |
| 5.11 | MACs are masked | Devices screen | Only last four hex characters |
| 5.12 | Real SSIDs render safely | Devices screen | Long names ellipsized, no overflow |

> **5.12 is the one to watch.** Real environments contain SSIDs with emoji, non-Latin scripts and
> deliberate oddities. Sanitization and ellipsis are unit-tested against synthetic hostile input,
> but a real neighbourhood is a better fuzzer than anything written here. Report anything that
> renders badly.

---

## Section 6 — Failure behaviour

**Status: NOT VERIFIED**

The most important section after the display, because it verifies that PiSight degrades instead of
dying.

| # | Check | How | Expected |
|---|---|---|---|
| 6.1 | Kismet stopped | `sudo systemctl stop kismet` | Status bar shows `OFFLINE` within ~30 s; last data stays on screen; **no crash** |
| 6.2 | Stale indicator | Watch during 6.1 | `STALE` appears before `OFFLINE` |
| 6.3 | Automatic recovery | `sudo systemctl start kismet` | Returns to live within ~30 s with no restart |
| 6.4 | Backoff is not a hammer | `journalctl -u pisight -f` during 6.1 | Retry gaps grow to ~30 s, not a tight loop |
| 6.5 | Bad token | Set a wrong token, restart | Clear auth error in the journal; **the token itself never appears** |
| 6.6 | Adapter unplugged | Physically remove the PAU0B | Datasource shows the error; PiSight keeps running |
| 6.7 | Clean shutdown | `sudo systemctl stop pisight` | Exits within a few seconds, no orphan process |
| 6.8 | Restart after crash | `sudo systemctl kill -s SIGKILL pisight` | Restarts within ~5 s |
| 6.9 | Survives a reboot | `sudo reboot` | Comes back automatically, display works |
| 6.10 | Long soak | Leave running 24 h | Still responsive; memory flat (`systemctl status pisight`) |

> **6.5 is a security check, not just a functional one.** After setting a bad token, run
> `journalctl -u pisight | grep -i <the token value>` and confirm **zero matches**.
>
> **6.10 verifies the bounded-memory design end to end.** The single-slot store and the capped
> device/alert lists are unit-tested, but a 24-hour soak in a real RF environment is the real test.
> Record the RSS at start and at 24 h.

---

## Section 7 — Performance on hardware

**Status: NOT VERIFIED — all figures below are targets, not measurements**

| # | Metric | How | Target |
|---|---|---|---|
| 7.1 | Frame rate | Count frames over a fixed smoke run | ~30 FPS |
| 7.2 | PiSight CPU | `top -p $(pgrep -f pisight)` | Under ~15% of one core |
| 7.3 | Memory (RSS) | `systemctl status pisight` | Under ~120 MB, flat over time |
| 7.4 | Effect on Kismet | Compare Kismet's packet rate with PiSight on and off | No meaningful drop |
| 7.5 | Startup time | `systemd-analyze blame \| grep pisight` | Under ~10 s to first frame |
| 7.6 | Screen switch latency | Tap and observe | Feels immediate (< ~100 ms) |
| 7.7 | Thermals | `vcgencmd measure_temp` after 1 h | No throttling |

> **7.4 is the one that matters most.** PiSight exists to serve Kismet, not to compete with it. If
> running PiSight measurably reduces Kismet's capture rate, raise `polling.interval_seconds` and
> lower `display.fps`, and record the numbers here.

---

## Section 8 — Security posture on hardware

**Status: NOT VERIFIED**

| # | Check | How | Expected |
|---|---|---|---|
| 8.1 | Not running as root | `ps -o user= -p $(pgrep -f pisight)` | `pisight` |
| 8.2 | Token file locked down | `sudo ls -l /etc/pisight/pisight.env` | `-rw------- root root` |
| 8.3 | Token not in the journal | `sudo journalctl -u pisight \| grep -c <token>` | `0` |
| 8.4 | Token not in the config | `grep -i token /etc/pisight/config.toml` | No match |
| 8.5 | No capabilities held | `grep Cap /proc/$(pgrep -f pisight)/status` | `CapEff: 0000000000000000` |
| 8.6 | Cannot open a raw socket | See below | Fails with `EAFNOSUPPORT` or `EPERM` |
| 8.7 | Filesystem is read-only | `sudo -u pisight touch /opt/pisight/x` | Permission denied |
| 8.8 | Interfaces unchanged | `ip link` before and after running PiSight | Identical |
| 8.9 | Kismet config unchanged | `md5sum` Kismet's config before and after | Identical |
| 8.10 | No capture data touched | `ls -l --time-style=full /var/log/kismet` before/after | mtimes unchanged by PiSight |

**8.6 — the decisive test.** The systemd unit omits `AF_PACKET` from `RestrictAddressFamilies`, so
the process should be *incapable* of opening a raw socket:

```bash
sudo -u pisight systemd-run --uid=pisight --property=RestrictAddressFamilies="AF_UNIX AF_INET AF_INET6" \
  --pty /opt/pisight/venv/bin/python -c \
  "import socket; socket.socket(socket.AF_PACKET, socket.SOCK_RAW)"
```

Expect an exception. **If this succeeds, the sandbox is not applying and that must be fixed before
deployment** — it is the control that makes "PiSight cannot capture packets" a kernel-enforced fact
rather than a promise.

---

## Sign-off

Do not mark PiSight hardware-validated until Sections 1–8 are complete.

```
Validated by:      ____________________
Date:              ____________________
Pi 5 revision:     ____________________
OS version:        ____________________  (cat /etc/os-release)
Kernel:            ____________________  (uname -r)
Kismet version:    ____________________
Display driver:    ____________________  (the SDL_VIDEODRIVER that worked)
Deployment profile: A / B
PiSight commit:    ____________________

Sections passed:   1 [ ]  2 [ ]  3 [ ]  4 [ ]  5 [ ]  6 [ ]  7 [ ]  8 [ ]

Open issues:
```

When a section passes, change its status line in this file from `NOT VERIFIED` to
`VERIFIED <date> on <OS version>`, update the status banner in the README, and commit. The
repository's claims should never outrun what has actually been tested.
