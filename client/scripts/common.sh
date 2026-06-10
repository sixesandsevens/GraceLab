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
