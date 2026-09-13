#!/usr/bin/env bash
#
# PiSight installer for Raspberry Pi OS (64-bit).
#
# What this script does:
#   - creates an unprivileged 'pisight' system user
#   - installs PiSight into /opt/pisight with its own virtualenv
#   - creates /etc/pisight and installs config + environment templates
#   - installs and (optionally) enables the systemd unit
#
# What this script deliberately does NOT do:
#   - touch /boot/firmware/config.txt or any display overlay. Your display already works;
#     boot configuration is out of scope and changing it risks an unbootable Pi.
#   - install third-party or out-of-tree kernel drivers
#   - configure monitor mode, or change any wireless interface in any way
#   - install or reconfigure Kismet
#   - invent, generate or guess a Kismet API token
#   - delete any data, ever
#
# Run it from a clone of the repository:
#
#     sudo bash scripts/install-rpi.sh --dry-run   # show every action, change nothing
#     sudo bash scripts/install-rpi.sh
#
# The script is idempotent: running it again upgrades the code in place and leaves your
# existing configuration and token untouched.

set -euo pipefail

# --- Settings -------------------------------------------------------------------------

readonly INSTALL_DIR="/opt/pisight"
readonly VENV_DIR="${INSTALL_DIR}/venv"
readonly CONFIG_DIR="/etc/pisight"
readonly CONFIG_FILE="${CONFIG_DIR}/config.toml"
readonly ENV_FILE="${CONFIG_DIR}/pisight.env"
readonly SERVICE_NAME="pisight.service"
readonly SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
readonly SERVICE_USER="pisight"
readonly SERVICE_GROUP="pisight"

# Explicit paths: PATH may differ under sudo, and a surprise binary here would be bad.
readonly PYTHON_BIN="/usr/bin/python3"
readonly SYSTEMCTL_BIN="/usr/bin/systemctl"
readonly INSTALL_BIN="/usr/bin/install"

DRY_RUN=0
ENABLE_SERVICE=0
SKIP_SERVICE=0

readonly REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- Output helpers -------------------------------------------------------------------

info()  { printf '\033[0;36m[ info ]\033[0m %s\n' "$*"; }
ok()    { printf '\033[0;32m[  ok  ]\033[0m %s\n' "$*"; }
warn()  { printf '\033[0;33m[ warn ]\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[0;31m[ fail ]\033[0m %s\n' "$*" >&2; exit 1; }

# Run a command, or describe it under --dry-run. Every mutating action goes through this,
# which is what makes --dry-run a complete and trustworthy preview.
run() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        printf '\033[0;35m[ plan ]\033[0m %s\n' "$*"
    else
        "$@"
    fi
}

usage() {
    cat <<'USAGE'
Usage: sudo bash scripts/install-rpi.sh [OPTIONS]

Options:
  --dry-run         Print every action without changing anything. Run this first.
  --enable          Enable the service to start at boot (default: install but do not enable)
  --skip-service    Install the application only; do not touch systemd
  -h, --help        Show this help

The installer never writes a Kismet API token. After installing, edit
/etc/pisight/pisight.env and paste a token with the 'readonly' role.
USAGE
}

# --- Argument parsing -----------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)      DRY_RUN=1 ;;
        --enable)       ENABLE_SERVICE=1 ;;
        --skip-service) SKIP_SERVICE=1 ;;
        -h|--help)      usage; exit 0 ;;
        *)              usage; fail "unknown option: $1" ;;
    esac
    shift
done

# --- Preflight ------------------------------------------------------------------------

preflight() {
    info "Checking prerequisites"

    if [[ "${DRY_RUN}" -eq 0 && "${EUID}" -ne 0 ]]; then
        fail "this script must run as root (use: sudo bash scripts/install-rpi.sh)"
    fi

    [[ -x "${PYTHON_BIN}" ]] || fail "${PYTHON_BIN} not found; install python3 first"

    local version
    version="$("${PYTHON_BIN}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    info "Found Python ${version} at ${PYTHON_BIN}"
    "${PYTHON_BIN}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
        || fail "PiSight requires Python 3.11 or newer; found ${version}"

    "${PYTHON_BIN}" -c 'import venv' 2>/dev/null \
        || fail "the python3-venv package is missing; install it with: apt install python3-venv"

    [[ -f "${REPO_ROOT}/pyproject.toml" ]] \
        || fail "run this script from inside a PiSight clone (pyproject.toml not found)"

    local arch
    arch="$(uname -m)"
    if [[ "${arch}" != "aarch64" ]]; then
        warn "architecture is ${arch}, not aarch64; PiSight targets 64-bit Raspberry Pi OS"
    fi

    # Kismet is not required to install, only to show live data.
    if [[ -x /usr/bin/kismet ]]; then
        ok "Kismet is installed"
    else
        warn "Kismet was not found at /usr/bin/kismet."
        warn "PiSight will install fine, but live mode needs a running Kismet."
        warn "Install it from the official repository: https://www.kismetwireless.net/packages/"
    fi

    ok "Prerequisites satisfied"
}

# --- Steps ----------------------------------------------------------------------------

create_user() {
    info "Ensuring the unprivileged '${SERVICE_USER}' service account exists"

    if getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
        ok "User '${SERVICE_USER}' already exists; leaving it alone"
    else
        info "Creating system user '${SERVICE_USER}' with no login shell and no home directory"
        run /usr/sbin/useradd --system --no-create-home --shell /usr/sbin/nologin \
            "${SERVICE_USER}"
        ok "Created '${SERVICE_USER}'"
    fi

    # video/input/render are needed to open the display and read the touchscreen.
    # No other group is added, and the account never gets sudo.
    local group
    for group in video input render; do
        if ! getent group "${group}" >/dev/null 2>&1; then
            warn "Group '${group}' does not exist on this system; skipping"
            continue
        fi
        if id -nG "${SERVICE_USER}" 2>/dev/null | tr ' ' '\n' | grep -qx "${group}"; then
            ok "'${SERVICE_USER}' is already in '${group}'"
        else
            info "Adding '${SERVICE_USER}' to '${group}' (needed for display/touch access)"
            run /usr/sbin/usermod -aG "${group}" "${SERVICE_USER}"
        fi
    done
}

install_application() {
    info "Installing the application into ${INSTALL_DIR}"

    run "${INSTALL_BIN}" -d -o root -g root -m 0755 "${INSTALL_DIR}"

    if [[ -d "${VENV_DIR}" ]]; then
        ok "Virtualenv already present at ${VENV_DIR}; reusing it"
    else
        info "Creating a virtualenv at ${VENV_DIR}"
        run "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    fi

    info "Upgrading packaging tools inside the virtualenv"
    run "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

    info "Installing PiSight and its two runtime dependencies (pygame-ce, httpx)"
    run "${VENV_DIR}/bin/python" -m pip install "${REPO_ROOT}"

    ok "Application installed at ${INSTALL_DIR}"
}

install_configuration() {
    info "Setting up ${CONFIG_DIR}"

    run "${INSTALL_BIN}" -d -o root -g root -m 0755 "${CONFIG_DIR}"

    # Existing configuration is never overwritten: an upgrade must not discard local edits.
    if [[ -f "${CONFIG_FILE}" ]]; then
        ok "${CONFIG_FILE} already exists; leaving your settings untouched"
        info "The current template is at ${REPO_ROOT}/config/pisight.example.toml if you"
        info "want to compare it against your file."
    else
        info "Installing the default configuration to ${CONFIG_FILE} (world-readable, no secrets)"
        run "${INSTALL_BIN}" -o root -g root -m 0644 \
            "${REPO_ROOT}/config/pisight.example.toml" "${CONFIG_FILE}"
    fi

    if [[ -f "${ENV_FILE}" ]]; then
        ok "${ENV_FILE} already exists; your API token is left untouched"
    else
        info "Installing the environment template to ${ENV_FILE} with mode 0600 (root only)"
        run "${INSTALL_BIN}" -o root -g root -m 0600 \
            "${REPO_ROOT}/config/pisight.env.example" "${ENV_FILE}"
        warn "${ENV_FILE} contains a PLACEHOLDER token, not a working one."
        warn "PiSight cannot and will not generate a Kismet token for you."
        warn "Create one yourself with the 'readonly' role and paste it in; see"
        warn "docs/RPI5_DEPLOYMENT.md for step-by-step instructions."
    fi
}

install_service() {
    if [[ "${SKIP_SERVICE}" -eq 1 ]]; then
        info "Skipping systemd setup (--skip-service)"
        return
    fi

    info "Installing the systemd unit to ${SERVICE_FILE}"
    run "${INSTALL_BIN}" -o root -g root -m 0644 \
        "${REPO_ROOT}/systemd/${SERVICE_NAME}" "${SERVICE_FILE}"

    info "Reloading the systemd daemon"
    run "${SYSTEMCTL_BIN}" daemon-reload

    if [[ "${ENABLE_SERVICE}" -eq 1 ]]; then
        info "Enabling ${SERVICE_NAME} to start at boot"
        run "${SYSTEMCTL_BIN}" enable "${SERVICE_NAME}"
        warn "The service is enabled but NOT started."
        warn "Add your API token first, then run: sudo systemctl start ${SERVICE_NAME}"
    else
        ok "Unit installed but not enabled. Enable it when you are ready:"
        ok "    sudo systemctl enable --now ${SERVICE_NAME}"
    fi
}

summary() {
    echo
    ok "Installation complete."
    echo
    cat <<SUMMARY
What was installed
  Application     ${INSTALL_DIR}  (virtualenv at ${VENV_DIR})
  Configuration   ${CONFIG_FILE}  (0644, contains no secrets)
  Environment     ${ENV_FILE}  (0600, root-only, holds the API token)
  Service unit    ${SERVICE_FILE}
  Service account ${SERVICE_USER}  (no shell, no home, no sudo)

What was NOT touched
  /boot/firmware/config.txt and all display overlays
  Kismet's configuration and any wireless interface
  Any existing capture data

Next steps
  1. Create a read-only Kismet API token:
       - open http://127.0.0.1:2501 on the Pi and log in as the Kismet admin
       - menu -> API Tokens -> create one named 'pisight' with role 'readonly'
  2. Paste it into the environment file:
       sudo nano ${ENV_FILE}
  3. Check the environment before starting anything:
       sudo -u ${SERVICE_USER} ${VENV_DIR}/bin/pisight doctor
  4. Try it by hand first:
       sudo -u ${SERVICE_USER} ${VENV_DIR}/bin/pisight --mode mock --smoke-seconds 5
  5. Start the service:
       sudo systemctl enable --now ${SERVICE_NAME}
       journalctl -u ${SERVICE_NAME} -f

  If the display stays blank, read the two deployment profiles in
  docs/RPI5_DEPLOYMENT.md; you probably need to set SDL_VIDEODRIVER.

  To undo everything: bash scripts/uninstall-rpi.sh, or follow docs/ROLLBACK.md.
SUMMARY
}

# --- Main -----------------------------------------------------------------------------

main() {
    echo "PiSight installer"
    echo "Repository: ${REPO_ROOT}"
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        warn "DRY RUN: every action below is printed and nothing is changed."
    fi
    echo

    preflight
    create_user
    install_application
    install_configuration
    install_service

    if [[ "${DRY_RUN}" -eq 1 ]]; then
        echo
        warn "DRY RUN complete. Nothing was changed."
        warn "Re-run without --dry-run to apply."
    else
        summary
    fi
}

main "$@"
