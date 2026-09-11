# Rollback and uninstall

How to stop, downgrade or completely remove PiSight. Every instruction here matches an actual file
in this repository — the paths below are the same ones `scripts/install-rpi.sh` creates.

**Nothing in this document touches Kismet, Kismet's configuration, Kismet's capture data, any
wireless interface, or `/boot/firmware/config.txt`.**

---

## Level 1 — Stop PiSight now

The fastest way to make it stop, reversible in seconds.

```bash
sudo systemctl stop pisight.service
```

Restart it with:

```bash
sudo systemctl start pisight.service
```

## Level 2 — Stop it starting at boot

Leaves everything installed; PiSight simply will not come back after a reboot.

```bash
sudo systemctl disable --now pisight.service
```

Undo:

```bash
sudo systemctl enable --now pisight.service
```

## Level 3 — Fall back to mock mode

Useful when you suspect PiSight's Kismet integration rather than PiSight itself. Mock mode needs
no token and touches no radio, so if the display works in mock mode the problem is the Kismet link
or the token, not the renderer.

```bash
sudo systemctl stop pisight.service
sudo -u pisight SDL_VIDEODRIVER=kmsdrm /opt/pisight/venv/bin/pisight \
  --mode mock --fullscreen --smoke-seconds 30
```

To make mock mode persistent, set `PISIGHT_MODE=mock` in `/etc/pisight/pisight.env` and restart.
Note the unit's `ExecStart` passes `--mode live`, which **overrides** the environment variable, so
for a persistent change use a drop-in:

```bash
sudo systemctl edit pisight.service
```

```ini
[Service]
ExecStart=
ExecStart=/opt/pisight/venv/bin/pisight --mode mock --fullscreen
```

(The empty `ExecStart=` is required to clear the original before setting a new one.)

```bash
sudo systemctl restart pisight.service
```

Undo by deleting the drop-in:

```bash
sudo rm /etc/systemd/system/pisight.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart pisight.service
```

## Level 4 — Downgrade to an earlier version

The installer is idempotent, so reinstalling from an older commit is a downgrade. Your
configuration and token are preserved.

```bash
cd ~/pisight
git log --oneline -20            # find the commit you want
git checkout <commit-or-tag>
sudo bash scripts/install-rpi.sh --dry-run
sudo bash scripts/install-rpi.sh
sudo systemctl restart pisight.service
bash scripts/verify-rpi.sh
```

Return to the latest:

```bash
cd ~/pisight
git checkout main && git pull
sudo bash scripts/install-rpi.sh
sudo systemctl restart pisight.service
```

## Level 5 — Uninstall, keeping your configuration

Removes the application, the service and the service account. Keeps `/etc/pisight`, so your API
token and settings survive for a future reinstall.

```bash
cd ~/pisight
sudo bash scripts/uninstall-rpi.sh --dry-run    # preview; changes nothing
sudo bash scripts/uninstall-rpi.sh
```

This removes:

- `/opt/pisight` (application code and virtualenv)
- `/etc/systemd/system/pisight.service`
- the `pisight` system account

Reinstall later with `sudo bash scripts/install-rpi.sh`; your existing `/etc/pisight` is detected
and left untouched.

## Level 6 — Complete removal, including the token

```bash
sudo bash scripts/uninstall-rpi.sh --purge
```

You will be asked to type `yes` before `/etc/pisight` is deleted, because that directory holds
your API token.

**Afterwards, revoke the token in Kismet** (Kismet UI → API Tokens → delete the `pisight` token).
Deleting the file on the Pi does not invalidate the token; only Kismet can do that.

---

## Manual removal

If the uninstall script is unavailable — for example you deleted the clone before uninstalling —
these are exactly the steps it performs:

```bash
# 1. Stop and disable
sudo systemctl stop pisight.service
sudo systemctl disable pisight.service

# 2. Remove the unit
sudo rm -f /etc/systemd/system/pisight.service
sudo rm -rf /etc/systemd/system/pisight.service.d
sudo systemctl daemon-reload
sudo systemctl reset-failed

# 3. Remove the application
sudo rm -rf /opt/pisight

# 4. Remove the configuration (contains your API token)
sudo rm -rf /etc/pisight

# 5. Remove the service account
sudo userdel pisight
```

Nothing else was ever created, so nothing else needs removing.

---

## Verifying the removal

```bash
systemctl status pisight.service        # Unit pisight.service could not be found
ls /opt/pisight                         # No such file or directory
ls /etc/pisight                         # No such file or directory (only after --purge)
id pisight                              # no such user
```

And confirm what should still be there:

```bash
systemctl status kismet                 # still active (running)
ls -l /var/log/kismet                   # capture data intact
```

---

## What is never removed

The uninstaller will not touch any of this, whatever flags you pass:

| Item | Why |
|---|---|
| Kismet, its config and its service | PiSight did not install it and has no business removing it |
| Everything under `/var/log/kismet` | Your capture data. PiSight never had write access to it in the first place |
| `/boot/firmware/config.txt` and display overlays | PiSight never edited them; removing an overlay could leave you with no display |
| Wireless interface configuration | PiSight never changed it |
| `python3`, `python3-venv`, `git`, `fonts-dejavu-core` | Installed by you with apt; other things may depend on them |

To remove those apt packages, do it yourself with `sudo apt remove`, after checking what else
depends on them.

---

## If rollback itself fails

**The service will not stop.**

```bash
sudo systemctl kill -s SIGKILL pisight.service
sudo systemctl reset-failed pisight.service
```

**The display is left in a bad state after stopping.** PiSight restores the console on clean exit;
after a `SIGKILL` it may not. Reset the console:

```bash
sudo chvt 1 && sudo chvt 7     # or simply: sudo reboot
```

**`userdel` reports the user is in use.** A process is still running as `pisight`:

```bash
sudo pkill -u pisight
sudo userdel pisight
```

**You are locked out because the display is unusable.** SSH in from another machine and run Level 2
(`sudo systemctl disable --now pisight.service`), then reboot. PiSight never modifies boot
configuration, so a Pi that booted before installing PiSight will boot after removing it.
