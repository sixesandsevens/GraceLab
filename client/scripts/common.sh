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
    local attempt result
    if ! id "$GUEST_USER" &>/dev/null; then
        gl_log ERROR "guest account ${GUEST_USER} is missing"
        return 1
    fi
    pkill -KILL -u "$GUEST_USER" 2>/dev/null || true
    loginctl terminate-user "$GUEST_USER" 2>/dev/null || true
    # Never wipe a home or report a successful end while guest processes
    # remain. Allow the display manager a bounded interval to reap them;
    # 5s proved too short in the field (gracelab-06/-02, 2026-10-05).
    for attempt in $(seq 1 15); do
        sleep 1
        if pgrep -u "$GUEST_USER" >/dev/null 2>&1; then
            pkill -KILL -u "$GUEST_USER" 2>/dev/null || true
        else
            result=$?
            if [[ "$result" -eq 1 ]]; then
                return 0
            fi
            gl_log ERROR "could not verify guest process termination"
            return 1
        fi
    done
    gl_log ERROR "guest processes remain after termination; cleanup blocked"
    gl_log_guest_survivors
    return 1
}


# Record which guest processes outlived the kill, in the lifecycle log and on
# stderr — the client forwards hook stderr to the dashboard's failure event.
gl_log_guest_survivors() {
    local survivors
    survivors="$(ps -o pid=,ppid=,stat=,etimes=,wchan:20=,args= -u "$GUEST_USER" 2>&1 || true)"
    gl_log ERROR "surviving guest processes (pid ppid stat age wchan cmd):"$'\n'"${survivors}"
    printf 'guest processes remain after termination:\n%s\n' "$survivors" >&2
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
