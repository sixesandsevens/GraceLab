#!/usr/bin/env bash
# GraceLab window watchdog.
# Runs in the background alongside the GraceLab client.
# Closes any window that is not the GraceLab client window.
#
# Requires: xdotool, wmctrl
#   sudo apt-get install -y xdotool wmctrl
#
# Usage: start from run-client.sh or a systemd user service.
# The watchdog exits cleanly when the GRACELAB_WATCHDOG_STOP file is created.

set -uo pipefail

STOP_FLAG="/tmp/gracelab-watchdog-stop"
LOG="/var/log/gracelab/watchdog.log"
POLL_INTERVAL=2   # seconds between checks

# Patch C maintenance mode: while this file exists and is fresh, an admin is
# deliberately using the display, and the watchdog must not fight them by
# closing whatever window they have focused. Same freshness window as
# gracelab_client.py's own admin-override check — see
# enable_admin_override.sh / disable_admin_override.sh for how it's managed.
ADMIN_OVERRIDE="/run/gracelab-admin-override"
ADMIN_OVERRIDE_MAX_AGE=14400  # 4 hours

log() {
    local msg
    msg="$(date -Is) [watchdog] $*"
    echo "$msg"
    echo "$msg" >> "$LOG" 2>/dev/null || true
}

override_active() {
    local mtime now
    [[ -f "$ADMIN_OVERRIDE" ]] || return 1
    mtime=$(stat -c %Y "$ADMIN_OVERRIDE" 2>/dev/null) || return 1
    now=$(date +%s)
    (( now - mtime < ADMIN_OVERRIDE_MAX_AGE ))
}

# Ensure required tools are present
for tool in xdotool wmctrl; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        log "ERROR: $tool not found. Install with: sudo apt-get install -y xdotool wmctrl"
        exit 1
    fi
done

rm -f "$STOP_FLAG"
log "Watchdog started (PID $$). Polling every ${POLL_INTERVAL}s."

while true; do
    [[ -f "$STOP_FLAG" ]] && { log "Stop flag found, exiting."; exit 0; }

    if override_active; then
        sleep "$POLL_INTERVAL"
        continue
    fi

    # Get the active (focused) window title
    ACTIVE_ID=$(xdotool getactivewindow 2>/dev/null || true)
    if [[ -z "$ACTIVE_ID" ]]; then
        # No window has focus at all — this happens when xfdesktop is dead
        # (intentionally, see lockdown-operator.sh) and something like
        # Ctrl+Alt+D's show_desktop_key reveals the bare root window. There
        # is nothing to close here, but GraceLab needs to be reclaimed or
        # the operator is stuck staring at black with no way back except
        # pressing the same key combo again (confirmed support complaint,
        # June 2026). Refocus GraceLab directly.
        GRACELAB_ID=$(xdotool search --name "^GraceLab$" 2>/dev/null | head -1 || true)
        if [[ -n "$GRACELAB_ID" ]]; then
            log "No focused window (bare desktop revealed) — refocusing GraceLab."
            xdotool windowfocus "$GRACELAB_ID" 2>/dev/null || true
            xdotool windowraise "$GRACELAB_ID" 2>/dev/null || true
        fi
        sleep "$POLL_INTERVAL"
        continue
    fi

    ACTIVE_NAME=$(xdotool getwindowname "$ACTIVE_ID" 2>/dev/null || true)

    # GraceLab window is titled "GraceLab"
    if [[ "$ACTIVE_NAME" == "GraceLab" ]]; then
        sleep "$POLL_INTERVAL"
        continue
    fi

    # Something else has focus — check if it's a real visible window
    # (skip desktop, panels, and root window)
    WM_CLASS=$(xdotool getwindowclassname "$ACTIVE_ID" 2>/dev/null || true)
    case "$WM_CLASS" in
        # Allow known harmless system UI — adjust as needed
        "cinnamon"|"nemo-desktop"|"plank"|"trayer"|"stalonetray"|"xfce4-panel")
            sleep "$POLL_INTERVAL"
            continue
            ;;
    esac

    log "Intruding window detected: id=${ACTIVE_ID} name='${ACTIVE_NAME}' class='${WM_CLASS}' — closing."

    # Try a polite close first, then force-kill if it persists
    wmctrl -ic "$ACTIVE_ID" 2>/dev/null || true
    sleep 0.5

    # If it's still there, kill it harder
    STILL_THERE=$(xdotool getwindowname "$ACTIVE_ID" 2>/dev/null || true)
    if [[ -n "$STILL_THERE" ]]; then
        log "Window still present after polite close, force-killing: ${ACTIVE_ID}"
        xdotool windowkill "$ACTIVE_ID" 2>/dev/null || true
    fi

    # Refocus GraceLab
    GRACELAB_ID=$(xdotool search --name "^GraceLab$" 2>/dev/null | head -1 || true)
    if [[ -n "$GRACELAB_ID" ]]; then
        xdotool windowfocus "$GRACELAB_ID" 2>/dev/null || true
        xdotool windowraise "$GRACELAB_ID" 2>/dev/null || true
    fi

    sleep "$POLL_INTERVAL"
done
