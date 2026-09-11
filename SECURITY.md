# Security policy

## Authorized use only

PiSight is for wireless observation that the owner of the network and location has **authorized**.
Passive Wi-Fi observation is regulated differently in different jurisdictions, and "the signal
reached my antenna" is not the same as "I was permitted to record it". Obtain written permission
before deploying, and understand the rules that apply where you are.

This is the most significant risk associated with PiSight, and it is one the software cannot
mitigate.

## The passive-only boundary

PiSight observes. It never transmits. It does not implement, invoke, or provide a path to:

- packet injection
- deauthentication or disassociation
- active probe transmission
- authentication attempts or network association
- password or handshake cracking
- credential collection
- exploitation
- spoofing of any kind
- arbitrary shell commands from the UI
- Kismet configuration changes
- automatic upload of observations
- shutting down the host or deleting capture data

**Pull requests adding any of these will be declined.** This is not negotiable; it is what the
project is.

The boundary is enforced in three ways, in increasing order of strength:

1. **Documentation** — this file and the README.
2. **Executable tests** — `tests/test_safety.py` parses the source tree (it parses rather than
   greps, so a docstring *disclaiming* a capability is not mistaken for the capability) and fails
   the build on a shell invocation, an offensive tool reference, a raw-socket import, a Kismet
   write endpoint, a non-loopback destination, or a committed credential.
3. **The kernel** — the systemd unit drops every Linux capability and omits `AF_PACKET` from
   `RestrictAddressFamilies`. The deployed process **cannot open a raw socket**, so it is
   incapable of capturing or injecting packets even if its code were changed to try.

## Kismet credentials

Use a token with Kismet's **`readonly`** role.

- `readonly` grants access only to endpoints that do not modify devices, state or configuration.
  PiSight needs nothing more.
- **Never use an `admin` token.** A compromise of PiSight, or of the file holding its token, would
  then hand an attacker control of your capture engine.
- PiSight never requires, requests or stores the Kismet administrator password.

### Where the token lives

Only in the `PISIGHT_KISMET_API_TOKEN` environment variable.

- **Never** put it in `config.toml`. That file is world-readable and ends up in backups and support
  bundles.
- On a Pi, put it in `/etc/pisight/pisight.env` with `root:root` ownership and mode `0600`.
  systemd reads the file as root and injects the value into the unprivileged process, so the
  `pisight` account cannot read it from disk.
- `scripts/verify-rpi.sh` checks those permissions and fails if they are wrong.

The token is deliberately not a field on PiSight's configuration object, so it cannot appear in a
config dump, a `repr`, an exception, or the output of `pisight config`. It travels only in the
`KISMET` cookie, never in a URL. A redacting log filter scrubs it from every log record as a
backstop.

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Use GitHub's private reporting: go to the repository's **Security** tab and choose
**Report a vulnerability**. If that is unavailable, open an issue titled "Security contact
request" containing no detail, and a maintainer will arrange a private channel.

Please include:

- what the issue is and where in the code
- how to reproduce it
- what an attacker gains
- the version or commit
- any suggested fix

**Please do not include real capture data, real SSIDs, real MAC addresses or real coordinates in a
report.** Synthesise an equivalent using locally-administered MACs (the `02:` prefix), as every
fixture in this repository does.

### What to expect

This is a small project maintained on a best-effort basis. Expect an acknowledgement within about
a week. Fixes for confirmed issues are prioritised by severity, with token exposure, remote code
execution, and anything that would let PiSight transmit treated as the highest severity.

You will be credited unless you prefer otherwise.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | Yes |
| < 0.1 | No |

PiSight is pre-1.0 MVP software. **It has not been validated on real Raspberry Pi hardware** — see
[docs/HARDWARE_VALIDATION.md](docs/HARDWARE_VALIDATION.md). Treat it accordingly.

## Deployment hardening

Beyond the defaults the installer sets:

1. **Keep Kismet on loopback.** The default `base_url` is `http://127.0.0.1:2501`. Exposing Kismet
   to a network exposes both the token and every observation to that network.
2. **Use full-disk encryption if the observations matter.** Physical possession of the SD card or
   NVMe yields the token and all of Kismet's capture data. PiSight cannot mitigate this.
3. **Leave `show_full_mac = false`.** The default masks MAC addresses to their last four hex
   characters, so a photograph of the screen is not identifying.
4. **Keep dependencies current.** Only `pygame-ce` and `httpx` are required at runtime.
5. **Review `journalctl -u pisight` after changes.** PiSight writes no log files of its own, so the
   journal is the complete record.
6. **Do not run PiSight as root.** The unit will not, and `verify-rpi.sh` fails if it is changed to.

## Known security limitations

These are accepted, documented trade-offs, not oversights:

- **Physical loss is not mitigated.** See T5 in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
- **PiSight cannot verify the role of the token it is given.** An operator can supply an `admin`
  token; documentation is the only control.
- **A custom CA is not pinned or verified** for a non-loopback HTTPS Kismet.
- **The token is visible in `/proc/<pid>/environ` to root.** Anyone already root has larger
  capabilities than reading it.

Full analysis: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).
