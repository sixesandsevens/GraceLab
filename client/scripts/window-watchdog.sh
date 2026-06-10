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

log() {
    local msg
    msg="$(date -Is) [watchdog] $*"
    echo "$msg"
    echo "$msg" >> "$LOG" 2>/dev/null || true
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

    # Get the active (focused) window title
    ACTIVE_ID=$(xdotool getactivewindow 2>/dev/null || true)
    if [[ -z "$ACTIVE_ID" ]]; then
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
