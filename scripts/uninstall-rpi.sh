#!/usr/bin/env bash
#
# PiSight uninstaller.
#
#     sudo bash scripts/uninstall-rpi.sh --dry-run
#     sudo bash scripts/uninstall-rpi.sh
#     sudo bash scripts/uninstall-rpi.sh --purge     # also remove /etc/pisight
#
# This removes only what install-rpi.sh created:
#   /opt/pisight, /etc/systemd/system/pisight.service, and the 'pisight' service account.
#
# It never touches Kismet, Kismet's configuration, Kismet's logs, any capture data, any
# wireless interface, or /boot/firmware/config.txt.
#
# /etc/pisight is KEPT by default because it holds your API token and local settings.
# Pass --purge to remove it; you will be asked to confirm.

set -euo pipefail

readonly INSTALL_DIR="/opt/pisight"
readonly CONFIG_DIR="/etc/pisight"
readonly SERVICE_NAME="pisight.service"
readonly SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}"
readonly SERVICE_USER="pisight"
readonly SYSTEMCTL_BIN="/usr/bin/systemctl"

DRY_RUN=0
PURGE=0
KEEP_USER=0

info() { printf '\033[0;36m[ info ]\033[0m %s\n' "$*"; }
ok()   { printf '\033[0;32m[  ok  ]\033[0m %s\n' "$*"; }
warn() { printf '\033[0;33m[ warn ]\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[0;31m[ fail ]\033[0m %s\n' "$*" >&2; exit 1; }

run() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        printf '\033[0;35m[ plan ]\033[0m %s\n' "$*"
    else
        "$@"
    fi
}

usage() {
    cat <<'USAGE'
Usage: sudo bash scripts/uninstall-rpi.sh [OPTIONS]

Options:
  --dry-run     Print every action without changing anything
  --purge       Also remove /etc/pisight (configuration and API token)
  --keep-user   Leave the 'pisight' service account in place
  -h, --help    Show this help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)    DRY_RUN=1 ;;
        --purge)      PURGE=1 ;;
        --keep-user)  KEEP_USER=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            usage; fail "unknown option: $1" ;;
    esac
    shift
done

if [[ "${DRY_RUN}" -eq 0 && "${EUID}" -ne 0 ]]; then
    fail "this script must run as root (use: sudo bash scripts/uninstall-rpi.sh)"
fi

echo "PiSight uninstaller"
[[ "${DRY_RUN}" -eq 1 ]] && warn "DRY RUN: nothing will be changed."
echo

# --- Stop and disable the service ------------------------------------------------------

if [[ -f "${SERVICE_FILE}" ]]; then
    if "${SYSTEMCTL_BIN}" is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        info "Stopping ${SERVICE_NAME}"
        run "${SYSTEMCTL_BIN}" stop "${SERVICE_NAME}"
    fi
    if "${SYSTEMCTL_BIN}" is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        info "Disabling ${SERVICE_NAME}"
        run "${SYSTEMCTL_BIN}" disable "${SERVICE_NAME}"
    fi
    info "Removing ${SERVICE_FILE}"
    run rm -f "${SERVICE_FILE}"
    run "${SYSTEMCTL_BIN}" daemon-reload
    run "${SYSTEMCTL_BIN}" reset-failed
    ok "Service removed"
else
    info "No unit file at ${SERVICE_FILE}; nothing to stop"
fi

# --- Remove the application ------------------------------------------------------------

if [[ -d "${INSTALL_DIR}" ]]; then
    # Guard against a mistyped constant turning this into a catastrophic delete.
    case "${INSTALL_DIR}" in
        /opt/pisight) ;;
        *) fail "refusing to delete an unexpected path: ${INSTALL_DIR}" ;;
    esac
    info "Removing ${INSTALL_DIR} (application code and virtualenv only)"
    run rm -rf "${INSTALL_DIR}"
    ok "Application removed"
else
    info "${INSTALL_DIR} does not exist"
fi

# --- Configuration ---------------------------------------------------------------------

if [[ -d "${CONFIG_DIR}" ]]; then
    if [[ "${PURGE}" -eq 1 ]]; then
        warn "About to delete ${CONFIG_DIR}, including your Kismet API token."
        if [[ "${DRY_RUN}" -eq 0 ]]; then
            read -r -p "Type 'yes' to confirm: " reply
            if [[ "${reply}" != "yes" ]]; then
                info "Keeping ${CONFIG_DIR}"
            else
                run rm -rf "${CONFIG_DIR}"
                ok "Configuration removed"
            fi
        else
            run rm -rf "${CONFIG_DIR}"
        fi
    else
        ok "Keeping ${CONFIG_DIR} (your token and settings). Use --purge to remove it."
    fi
fi

# --- Service account ---------------------------------------------------------------------

if [[ "${KEEP_USER}" -eq 0 ]] && getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
    info "Removing the '${SERVICE_USER}' service account"
    run /usr/sbin/userdel "${SERVICE_USER}" || warn "could not remove '${SERVICE_USER}'"
    ok "Service account removed"
fi

echo
if [[ "${DRY_RUN}" -eq 1 ]]; then
    warn "DRY RUN complete. Nothing was changed."
else
    ok "PiSight has been removed."
    echo
    echo "Untouched, as promised:"
    echo "  Kismet, its configuration and its service"
    echo "  All capture data and logs under /var/log/kismet"
    echo "  /boot/firmware/config.txt and every display overlay"
    [[ "${PURGE}" -eq 0 && -d "${CONFIG_DIR}" ]] && echo "  ${CONFIG_DIR} (use --purge to remove)"
fi
