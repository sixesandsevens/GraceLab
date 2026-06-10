#!/usr/bin/env bash
# GraceLab client wrapper.
# Keeps gracelab_client.py running and relaunches it after updates.
#
# The updater writes /tmp/gracelab-update-ready when a new version is
# installed. The client exits cleanly when it sees that flag, and this
# wrapper relaunches against the updated current/ symlink.

CURRENT="/opt/gracelab-client/current"
LOG="/var/log/gracelab/run-client.log"
UPDATE_FLAG="/tmp/gracelab-update-ready"
WATCHDOG="${CURRENT}/scripts/window-watchdog.sh"
WATCHDOG_STOP="/tmp/gracelab-watchdog-stop"
WATCHDOG_PID=""

_log() {
    local msg
    msg="$(date -Is) [run-client] $*"
    echo "$msg"
    echo "$msg" >> "$LOG" 2>/dev/null || true
}

_start_watchdog() {
    if [[ -x "$WATCHDOG" ]]; then
        rm -f "$WATCHDOG_STOP"
        bash "$WATCHDOG" &
        WATCHDOG_PID=$!
        _log "Watchdog started (PID ${WATCHDOG_PID})."
    else
        _log "Watchdog script not found or not executable: ${WATCHDOG}"
    fi
}

_stop_watchdog() {
    touch "$WATCHDOG_STOP"
    if [[ -n "$WATCHDOG_PID" ]]; then
        kill "$WATCHDOG_PID" 2>/dev/null || true
        WATCHDOG_PID=""
    fi
}

# Suppress update notifier on first run
DISABLE_UPDATER="${CURRENT}/scripts/disable-updater.sh"
if [[ -x "$DISABLE_UPDATER" ]]; then
    bash "$DISABLE_UPDATER" >> "$LOG" 2>&1 || true
fi

_log "Wrapper started (PID $$)."
_start_watchdog

while true; do
    rm -f "$UPDATE_FLAG"

    _log "Launching gracelab_client.py from ${CURRENT}"
    python3 "${CURRENT}/gracelab_client.py"
    EXIT=$?

    if [[ -f "$UPDATE_FLAG" ]]; then
        NEW_VER="$(cat "$UPDATE_FLAG" 2>/dev/null || echo '?')"
        _log "Update flag found (v${NEW_VER}). Relaunching against new version."
        _stop_watchdog
        sleep 1
        _start_watchdog
    else
        _log "Client exited (code ${EXIT}). Restarting in 5s."
        sleep 5
    fi
done
