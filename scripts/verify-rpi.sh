#!/usr/bin/env bash
#
# PiSight post-installation verification.
#
# Read-only. This script inspects; it changes nothing, starts nothing and stops nothing.
# Run it after install-rpi.sh, and again whenever something looks wrong.
#
#     bash scripts/verify-rpi.sh
#
# Exit status: 0 if every required check passed, 1 otherwise. Warnings do not fail.

set -uo pipefail
# Note: -e is deliberately NOT set. Checks are expected to fail individually; the script
# must report every result rather than stopping at the first problem.

readonly INSTALL_DIR="/opt/pisight"
readonly VENV_DIR="${INSTALL_DIR}/venv"
readonly PISIGHT_BIN="${VENV_DIR}/bin/pisight"
readonly CONFIG_DIR="/etc/pisight"
readonly CONFIG_FILE="${CONFIG_DIR}/config.toml"
readonly ENV_FILE="${CONFIG_DIR}/pisight.env"
readonly SERVICE_NAME="pisight.service"
readonly SERVICE_USER="pisight"

PASS=0
WARN=0
FAIL=0

pass() { printf '\033[0;32m[ ok ]\033[0m %s\n' "$*"; PASS=$((PASS + 1)); }
warn() { printf '\033[0;33m[warn]\033[0m %s\n' "$*"; WARN=$((WARN + 1)); }
fail() { printf '\033[0;31m[FAIL]\033[0m %s\n' "$*"; FAIL=$((FAIL + 1)); }
note() { printf '       %s\n' "$*"; }
head_() { printf '\n\033[1m-- %s %s\033[0m\n' "$1" "$(printf '%.0s-' $(seq 1 $((60 - ${#1}))))"; }

# --- Installation ---------------------------------------------------------------------

head_ "installation"

if [[ -d "${INSTALL_DIR}" ]]; then
    pass "${INSTALL_DIR} exists"
else
    fail "${INSTALL_DIR} is missing; run scripts/install-rpi.sh"
fi

if [[ -x "${PISIGHT_BIN}" ]]; then
    pass "pisight executable found at ${PISIGHT_BIN}"
    version="$("${PISIGHT_BIN}" --version 2>&1 || echo 'failed to run')"
    note "version: ${version}"
else
    fail "${PISIGHT_BIN} is missing or not executable"
fi

if [[ -x "${VENV_DIR}/bin/python" ]]; then
    py_version="$("${VENV_DIR}/bin/python" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>&1)"
    pass "virtualenv Python ${py_version}"
else
    fail "no Python interpreter in ${VENV_DIR}"
fi

for module in pygame httpx; do
    if "${VENV_DIR}/bin/python" -c "import ${module}" 2>/dev/null; then
        pass "dependency '${module}' imports cleanly"
    else
        fail "dependency '${module}' cannot be imported"
    fi
done

# --- Configuration --------------------------------------------------------------------

head_ "configuration"

if [[ -f "${CONFIG_FILE}" ]]; then
    pass "${CONFIG_FILE} exists"
else
    warn "${CONFIG_FILE} is missing; PiSight will fall back to packaged defaults"
fi

if [[ -f "${ENV_FILE}" ]]; then
    pass "${ENV_FILE} exists"

    perms="$(stat -c '%a' "${ENV_FILE}" 2>/dev/null || echo '???')"
    if [[ "${perms}" == "600" ]]; then
        pass "${ENV_FILE} permissions are 0600 (root only)"
    else
        fail "${ENV_FILE} permissions are ${perms}, expected 600"
        note "fix with: sudo chmod 600 ${ENV_FILE}"
    fi

    owner="$(stat -c '%U' "${ENV_FILE}" 2>/dev/null || echo '?')"
    if [[ "${owner}" == "root" ]]; then
        pass "${ENV_FILE} is owned by root"
    else
        fail "${ENV_FILE} is owned by '${owner}', expected root"
    fi

    # Check only whether a placeholder is still in place. The token value is never printed.
    if [[ -r "${ENV_FILE}" ]]; then
        if grep -q 'replace_with_your_readonly_token' "${ENV_FILE}" 2>/dev/null; then
            fail "the API token is still the placeholder; live mode will not authenticate"
            note "edit ${ENV_FILE} and paste a token with the 'readonly' role"
        elif grep -q '^PISIGHT_KISMET_API_TOKEN=.\+' "${ENV_FILE}" 2>/dev/null; then
            pass "an API token is set (value not shown)"
        else
            warn "PISIGHT_KISMET_API_TOKEN does not appear to be set"
        fi
    else
        warn "cannot read ${ENV_FILE} as this user; re-run with sudo to check the token"
    fi
else
    fail "${ENV_FILE} is missing"
fi

# The world-readable config must never contain a secret.
if [[ -r "${CONFIG_FILE}" ]] && grep -qiE '^[[:space:]]*(api_?token|api_?key|password)' "${CONFIG_FILE}"; then
    fail "${CONFIG_FILE} appears to contain a credential; it is world-readable"
    note "move the token to ${ENV_FILE} and remove it from the config file"
else
    pass "no credential found in the world-readable config file"
fi

# --- Service account ------------------------------------------------------------------

head_ "service account"

if getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
    pass "service account '${SERVICE_USER}' exists"

    shell="$(getent passwd "${SERVICE_USER}" | cut -d: -f7)"
    if [[ "${shell}" == */nologin || "${shell}" == */false ]]; then
        pass "'${SERVICE_USER}' has no login shell (${shell})"
    else
        warn "'${SERVICE_USER}' has a login shell: ${shell}"
    fi

    groups="$(id -nG "${SERVICE_USER}" 2>/dev/null || echo '')"
    note "groups: ${groups}"
    for group in video input; do
        if echo "${groups}" | tr ' ' '\n' | grep -qx "${group}"; then
            pass "'${SERVICE_USER}' is in the '${group}' group"
        else
            warn "'${SERVICE_USER}' is not in '${group}'; needed for the DRM/KMS profile"
        fi
    done

    if echo "${groups}" | tr ' ' '\n' | grep -qxE 'sudo|root|adm'; then
        fail "'${SERVICE_USER}' has administrative group membership; it should not"
    else
        pass "'${SERVICE_USER}' has no administrative privileges"
    fi
else
    fail "service account '${SERVICE_USER}' does not exist"
fi

# --- systemd --------------------------------------------------------------------------

head_ "systemd"

if [[ -f "/etc/systemd/system/${SERVICE_NAME}" ]]; then
    pass "unit file installed"

    if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        pass "${SERVICE_NAME} is enabled at boot"
    else
        warn "${SERVICE_NAME} is not enabled (enable with: sudo systemctl enable ${SERVICE_NAME})"
    fi

    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        pass "${SERVICE_NAME} is running"
    else
        warn "${SERVICE_NAME} is not running"
        note "start with: sudo systemctl start ${SERVICE_NAME}"
        note "inspect with: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
    fi

    # The unit must not run as root.
    if grep -qE '^User=root' "/etc/systemd/system/${SERVICE_NAME}"; then
        fail "the unit runs PiSight as root; it must not"
    else
        pass "the unit does not run PiSight as root"
    fi
else
    warn "unit file not installed"
fi

# --- Kismet ---------------------------------------------------------------------------

head_ "kismet"

if systemctl is-active --quiet kismet.service 2>/dev/null; then
    pass "kismet.service is running"
else
    warn "kismet.service is not running; PiSight will show OFFLINE in live mode"
fi

if command -v curl >/dev/null 2>&1; then
    # An unauthenticated probe of the timestamp endpoint. No token is sent or printed.
    if curl -fsS --max-time 4 http://127.0.0.1:2501/system/timestamp.json >/dev/null 2>&1; then
        pass "the Kismet REST API answers on http://127.0.0.1:2501"
    else
        warn "no response from http://127.0.0.1:2501 (Kismet down, or it requires auth here)"
    fi
else
    warn "curl is not installed; skipping the Kismet reachability probe"
fi

# --- Display --------------------------------------------------------------------------

head_ "display"

if [[ -d /dev/dri ]]; then
    pass "/dev/dri exists: $(ls /dev/dri 2>/dev/null | tr '\n' ' ')"
else
    warn "/dev/dri is absent; the DRM/KMS deployment profile will not work"
fi

fb_found=0
for fb in /dev/fb0 /dev/fb1; do
    [[ -e "${fb}" ]] && { pass "framebuffer device ${fb} present"; fb_found=1; }
done
[[ "${fb_found}" -eq 0 ]] && warn "no framebuffer device found"

event_count="$(find /dev/input -maxdepth 1 -name 'event*' 2>/dev/null | wc -l)"
if [[ "${event_count}" -gt 0 ]]; then
    pass "${event_count} input event device(s) present (touch input)"
else
    warn "no /dev/input/event* devices; the touchscreen will not work"
fi

# --- Headless self-test ----------------------------------------------------------------

head_ "headless self-test"

if [[ -x "${PISIGHT_BIN}" ]]; then
    if SDL_VIDEODRIVER=dummy "${PISIGHT_BIN}" --mode mock --smoke-seconds 3 >/dev/null 2>&1; then
        pass "mock-mode smoke test rendered and exited cleanly"
    else
        fail "the mock-mode smoke test failed"
        note "see the error with:"
        note "  SDL_VIDEODRIVER=dummy ${PISIGHT_BIN} --mode mock --smoke-seconds 3"
    fi
else
    fail "cannot run the smoke test: ${PISIGHT_BIN} is missing"
fi

# --- Summary ---------------------------------------------------------------------------

head_ "summary"
printf 'passed: %d   warnings: %d   failures: %d\n\n' "${PASS}" "${WARN}" "${FAIL}"

if [[ "${FAIL}" -gt 0 ]]; then
    printf '\033[0;31mVerification failed.\033[0m Resolve the items marked [FAIL] above.\n'
    printf 'For a deeper look at the environment, run:\n'
    printf '    %s doctor\n' "${PISIGHT_BIN}"
    exit 1
fi

printf '\033[0;32mVerification passed.\033[0m\n'
if [[ "${WARN}" -gt 0 ]]; then
    printf 'Review the %d warning(s) above; some are expected before first start.\n' "${WARN}"
fi
printf '\nRemember: display output on real hardware is NOT verified by this script.\n'
printf 'Work through docs/HARDWARE_VALIDATION.md at the Pi to confirm it.\n'
exit 0
