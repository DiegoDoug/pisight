# Raspberry Pi 5 deployment

> **NOT YET VERIFIED ON HARDWARE.** These instructions were written and their scripts
> syntax-checked on a desktop. No step below has been executed on a real Raspberry Pi 5, ILI9341
> display, XPT2046 touch panel or PAU0B adapter. Treat this as a careful plan, not a tested
> runbook, and work through [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) as you go.

## Target

| | |
|---|---|
| Board | Raspberry Pi 5, ARM64 |
| OS | Raspberry Pi OS 64-bit (Bookworm or newer) |
| Python | 3.11+ (Bookworm ships 3.11) |
| Display | 320x240 landscape SPI TFT, ILI9341 |
| Touch | XPT2046 resistive |
| Adapter | Panda Wireless PAU0B AC600 |
| Storage | NVMe via a PCIe-to-M.2 HAT |
| Capture | Kismet (installed and configured separately) |

## Before you start

**Your display must already be working.** PiSight's installer deliberately does **not** touch
`/boot/firmware/config.txt` or any display overlay. Getting an ILI9341 panel working involves
device-tree overlays specific to your exact wiring, and an installer that edits boot configuration
can leave a Pi that will not boot. That is firmly out of scope for this MVP.

Confirm the display works before installing PiSight. If it does not, fix that first with your
panel vendor's instructions.

**Kismet must be installed separately.** Use the official repository at
<https://www.kismetwireless.net/packages/>. PiSight does not install, configure or modify Kismet.

---

## Choose your deployment profile

Raspberry Pi display stacks vary considerably. Two profiles are supported. Pick the one that
matches your setup — this choice determines one setting, `SDL_VIDEODRIVER`.

### Profile A — Direct DRM/KMS console (no desktop)

**Use when:** the Pi boots to a console with no desktop environment, and PiSight is the only thing
on the screen. This is the recommended setup for a dedicated field instrument: lower memory, lower
CPU, faster boot, nothing else to crash.

**Requires:** `/dev/dri` present, and the `pisight` user in the `video` and `render` groups (the
installer handles the groups).

**Setting**, in `/etc/pisight/pisight.env`:

```bash
SDL_VIDEODRIVER=kmsdrm
```

**Caveats.** Not every SPI panel presents a DRM device. If your ILI9341 is driven by `fbtft` it may
expose only `/dev/fb0` and no `/dev/dri` node. In that case try `SDL_VIDEODRIVER=fbcon` — and note
that SDL2's fbcon support is limited and may not work at all with your panel. Check what you have:

```bash
ls -l /dev/dri/ 2>/dev/null || echo "no DRM devices"
ls -l /dev/fb* 2>/dev/null || echo "no framebuffer devices"
```

### Profile B — Existing graphical desktop session

**Use when:** the Pi already boots to the Raspberry Pi OS desktop and you want PiSight inside it.
Easier to get working and easier to debug, at the cost of the desktop's memory and CPU.

**Setting:** usually none. SDL autodetects X11 or Wayland. If it does not:

```bash
SDL_VIDEODRIVER=x11        # or: wayland
```

Under a desktop session, running PiSight from systemd is awkward — the unit needs `DISPLAY` and
access to the session's authority file. Consider a desktop autostart entry instead:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/pisight.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=PiSight
Exec=/opt/pisight/venv/bin/pisight --mode live --fullscreen
X-GNOME-Autostart-enabled=true
EOF
```

You will need the token in that session's environment; the simplest approach is a small wrapper
script that sources `/etc/pisight/pisight.env` — but note that makes the token readable by your
desktop user, which weakens the protection described in
[THREAT_MODEL.md](THREAT_MODEL.md) (T1).

> **Neither profile has been verified on hardware during development.** Both are reasoned from the
> documented behaviour of SDL2 and Raspberry Pi OS.

---

## Install

### 1. Prepare the Pi

```bash
ssh pi@raspberrypi.local

sudo apt update
sudo apt install -y git python3-venv python3-pip fonts-dejavu-core
```

`fonts-dejavu-core` is optional — PiSight falls back to a built-in font — but DejaVu Sans is the
face the interface was designed against.

### 2. Clone

```bash
cd ~
git clone https://github.com/DiegoDoug/pisight.git
cd pisight
```

### 3. Preview the installation

Always do this first. It prints every action and changes nothing.

```bash
sudo bash scripts/install-rpi.sh --dry-run
```

Read the output. Every mutating action appears as a `[ plan ]` line.

### 4. Install

```bash
sudo bash scripts/install-rpi.sh
```

This creates:

| Path | Owner | Mode | Contents |
|---|---|---|---|
| `/opt/pisight` | root:root | 0755 | Application and its virtualenv |
| `/etc/pisight/config.toml` | root:root | 0644 | Settings. **No secrets.** |
| `/etc/pisight/pisight.env` | root:root | **0600** | The API token |
| `/etc/systemd/system/pisight.service` | root:root | 0644 | Service unit |
| the `pisight` account | — | — | No shell, no home, no sudo |

It is idempotent: re-running upgrades the code and leaves your configuration and token untouched.

**What it does not do:** touch boot configuration or display overlays, install kernel drivers,
configure monitor mode, alter any wireless interface, modify Kismet, invent a token, or delete
anything.

### 5. Create a read-only Kismet API token

PiSight cannot generate this for you.

1. On the Pi, open `http://127.0.0.1:2501` and log in as your Kismet admin user.
2. Menu (top right) → **API Tokens**.
3. Create a token:
   - **Name:** `pisight`
   - **Role:** `readonly`
   - **User:** your Kismet user
4. Copy the token.

**Use `readonly`, not `admin`.** The `readonly` role permits only endpoints that do not modify
devices, state or configuration — exactly what PiSight needs and nothing more.

### 6. Install the token

```bash
sudo nano /etc/pisight/pisight.env
```

```bash
PISIGHT_KISMET_API_TOKEN=paste_your_readonly_token_here
PISIGHT_MODE=live
# Profile A only:
#SDL_VIDEODRIVER=kmsdrm
```

Confirm the permissions:

```bash
sudo ls -l /etc/pisight/pisight.env
# -rw------- 1 root root ... /etc/pisight/pisight.env
```

If it is not `0600 root root`, fix it: `sudo chmod 600 /etc/pisight/pisight.env`.

### 7. Verify

```bash
bash scripts/verify-rpi.sh
```

Read-only. It checks the installation, configuration permissions, the service account, systemd,
Kismet reachability, display and input devices, and runs a headless smoke test.

Then the deeper diagnostic:

```bash
sudo -u pisight /opt/pisight/venv/bin/pisight doctor
```

This reports OS and architecture, Python version, display environment, `/dev/dri` and framebuffer
nodes, SDL availability, input devices, USB inventory, wireless interfaces and their drivers,
whether monitor mode appears supported, Kismet reachability, whether the token is accepted, the
storage path and free space, and group membership. **It changes nothing** — it never enables
monitor mode, alters an interface, installs a driver or writes to disk.

### 8. Test by hand before enabling the service

Mock mode first, which needs no Kismet and no token:

```bash
sudo -u pisight SDL_VIDEODRIVER=kmsdrm /opt/pisight/venv/bin/pisight \
  --mode mock --fullscreen --smoke-seconds 15
```

You should see the dashboard for 15 seconds. This is the moment to sort out the display driver.

Then live mode:

```bash
sudo -u pisight \
  PISIGHT_KISMET_API_TOKEN="$(sudo grep -oP '(?<=^PISIGHT_KISMET_API_TOKEN=).*' /etc/pisight/pisight.env)" \
  SDL_VIDEODRIVER=kmsdrm \
  /opt/pisight/venv/bin/pisight --mode live --fullscreen --smoke-seconds 15
```

### 9. Enable the service

```bash
sudo systemctl enable --now pisight.service
systemctl status pisight.service
journalctl -u pisight -f
```

---

## The systemd unit

Notable choices, and why:

- **`Wants=kismet.service`, not `Requires=`.** PiSight is useful when Kismet is down — it says so
  on screen. A hard dependency would stop PiSight exactly when you most want to see that Kismet
  died.
- **`User=pisight`** with `video`, `input` and `render` supplementary groups. Never root.
- **`CapabilityBoundingSet=` and `AmbientCapabilities=` are empty.** No Linux capability at all,
  in particular not `NET_RAW` or `NET_ADMIN`.
- **`RestrictAddressFamilies` omits `AF_PACKET`.** The process cannot open a raw socket, so it
  cannot capture or inject packets even if its code were changed to try.
- **`ProtectSystem=strict` with no `ReadWritePaths=`.** PiSight needs no writable path anywhere.
- **`PrivateDevices=no`** — it must stay `no`, because the display and touch device nodes are the
  entire point. Access is narrowed with explicit `DeviceAllow=` lines instead.
- **`MemoryDenyWriteExecute=no`** — some Mesa/SDL drivers JIT shaders; tightening this breaks KMS
  output.
- **`Restart=on-failure` with `StartLimitBurst=5`.** A crash loop on a headless Pi is better
  surfaced by a stopped unit than hidden by an endless restart.
- **`MemoryMax=256M`, `TasksMax=32`.** PiSight's memory is bounded by design; this turns a
  regression in that property into a restart rather than an OOM that takes Kismet with it.
- **Logs to journald only.** PiSight writes no log files, so it cannot fill the capture volume.

### Overriding without editing the unit

```bash
sudo systemctl edit pisight.service
```

```ini
[Service]
Environment=SDL_VIDEODRIVER=x11
```

Your override survives a reinstall; editing the unit file directly does not.

---

## Troubleshooting

### Blank screen

By far the most likely problem, and almost always the video driver.

```bash
journalctl -u pisight -n 50 --no-pager
sudo -u pisight /opt/pisight/venv/bin/pisight doctor | sed -n '/display/,/input/p'
```

Then work through:

1. Does SDL see a driver at all?
   ```bash
   sudo -u pisight SDL_VIDEODRIVER=kmsdrm /opt/pisight/venv/bin/pisight --mode mock --smoke-seconds 3
   ```
   If that fails, try `fbcon`, then `x11`.
2. Is the user in the right groups? `id pisight` should list `video` and `input`.
3. Does the device node exist? `ls -l /dev/dri/ /dev/fb*`
4. Does anything else own the display? On Profile A, make sure no desktop session is running.

### `Failed to start` immediately

```bash
systemctl status pisight.service
journalctl -u pisight -n 100 --no-pager
```

Common causes: a missing or placeholder token (live mode refuses to start without one — by
design), a `config.toml` typo (PiSight rejects unknown keys rather than ignoring them), or
`/opt/pisight/venv/bin/pisight` missing because the install failed partway.

### Dashboard shows OFFLINE

Kismet is unreachable.

```bash
systemctl status kismet
curl -s http://127.0.0.1:2501/system/timestamp.json
```

PiSight keeps running and keeps showing its last data. That is intended.

### Dashboard shows STALE

Snapshots are arriving slowly or have stopped. Check load — Kismet under heavy traffic can be slow
to answer. Consider raising `polling.interval_seconds`.

### 401/403 from Kismet

The token is wrong, expired, or was deleted. Generate a new `readonly` token and update
`/etc/pisight/pisight.env`, then `sudo systemctl restart pisight`.

### Touch does nothing

```bash
ls -l /dev/input/event*
id pisight    # must include 'input'
```

PiSight debounces touches over 220 ms. One press changing one screen is correct behaviour.

### High CPU

Lower `display.fps` to 15 and raise `polling.interval_seconds` to 5. On a Pi 5 neither should be
necessary, but this has not been measured on hardware.

---

## Updating

```bash
cd ~/pisight
git pull
sudo bash scripts/install-rpi.sh          # idempotent; keeps your config and token
sudo systemctl restart pisight.service
bash scripts/verify-rpi.sh
```

## Uninstalling

See [ROLLBACK.md](ROLLBACK.md).

## Next

Work through [HARDWARE_VALIDATION.md](HARDWARE_VALIDATION.md) and record what you find. Until that
is done, every hardware-dependent claim in this repository is unverified.
