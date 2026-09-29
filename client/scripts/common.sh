#!/usr/bin/env bash
# Shared constants and helpers sourced by GraceLab lifecycle scripts.

GUEST_USER="guestlab"
GUEST_HOME="/home/${GUEST_USER}"
TEMPLATE_HOME="/opt/gracelab-client/template-home"
LOG_DIR="/var/log/gracelab"
LOG="${LOG_DIR}/lifecycle.log"
IPC_DIR="/run/gracelab"
GUEST_LOGOUT_FLAG="${IPC_DIR}/guest-logout"
LEGACY_GUEST_LOGOUT_FLAG="/tmp/gracelab-guest-logout"

gl_log() {
    local level="$1"; shift
    mkdir -p "$LOG_DIR"
    printf '%s [%s] %s\n' "$(date --iso-8601=seconds)" "$level" "$*" >> "$LOG"
}

gl_kill_guest() {
    if id "$GUEST_USER" &>/dev/null; then
        pkill -KILL -u "$GUEST_USER" 2>/dev/null || true
        sleep 1
        loginctl terminate-user "$GUEST_USER" 2>/dev/null || true
    fi
}


gl_clear_guest_logout_flags() {
    rm -f "$GUEST_LOGOUT_FLAG" "$LEGACY_GUEST_LOGOUT_FLAG" 2>/dev/null || true
}


# Re-assert the NetworkManager polkit lockdown for the kiosk accounts. Called
# from the root lifecycle hooks because they run from the *current* release —
# stations provisioned before 0.4.6 have a do-install.sh that predates the
# lockdown — so every station picks it up on its next session.
# Non-fatal: a polkit hiccup must not block a guest session or reset.
gl_ensure_network_lockdown() {
    local helper="${SCRIPT_DIR}/install-network-lockdown.sh"
    if [ -x "$helper" ] && "$helper" >/dev/null 2>&1; then
        return 0
    fi
    gl_log WARN "network lockdown: ${helper} failed or missing"
    return 0
}

# Bring the root-owned updater helper up to the current release's version.
# do-install.sh does this itself from 0.4.6 on; calling it from the hooks too
# is what upgrades stations whose helper predates that. Non-fatal.
gl_ensure_updater_helper() {
    local helper="${SCRIPT_DIR}/sync-updater-helper.sh" out
    if [ -x "$helper" ] && out="$("$helper" 2>&1)"; then
        [ -n "$out" ] && gl_log INFO "updater helper: ${out}"
        return 0
    fi
    gl_log WARN "updater helper: ${helper} failed or missing: ${out:-}"
    return 0
}
