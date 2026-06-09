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

_log() {
    local msg
    msg="$(date -Is) [run-client] $*"
    echo "$msg"
    echo "$msg" >> "$LOG" 2>/dev/null || true
}

_log "Wrapper started (PID $$)."

while true; do
    rm -f "$UPDATE_FLAG"

    _log "Launching gracelab_client.py from ${CURRENT}"
    python3 "${CURRENT}/gracelab_client.py"
    EXIT=$?

    if [[ -f "$UPDATE_FLAG" ]]; then
        NEW_VER="$(cat "$UPDATE_FLAG" 2>/dev/null || echo '?')"
        _log "Update flag found (v${NEW_VER}). Relaunching against new version."
        sleep 1
    else
        _log "Client exited (code ${EXIT}). Restarting in 5s."
        sleep 5
    fi
done
