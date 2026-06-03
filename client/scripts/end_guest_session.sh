#!/usr/bin/env bash
# GraceLab — end guest session hook
# Called when a session expires, is ended by staff, or fails.
# Runs BEFORE reset_guest_home.sh.
#
# Phase 4A: placeholder.
# Phase 4B: will kill the guestlab session and all its processes.
#
# Exit 0  = success (reset script will run next)
# Exit != 0 = failure (client marks station needs_attention, skips reset)

set -euo pipefail

GUEST_USER="guestlab"
LOG="${GRACELAB_LOG:-/tmp/gracelab-client.log}"
TIMESTAMP=$(date --iso-8601=seconds)

echo "${TIMESTAMP} [end_guest_session] Hook fired." >> "$LOG"

# --------------------------------------------------------------------------
# Phase 4B: uncomment and adapt for real guest session teardown.
# --------------------------------------------------------------------------
#
# Kill all guestlab processes
# pkill -KILL -u "$GUEST_USER" || true
# sleep 1
#
# Terminate the login session (works with logind/systemd)
# loginctl terminate-user "$GUEST_USER" || true
# sleep 1
#
# Verify no guestlab processes remain
# if pgrep -u "$GUEST_USER" > /dev/null 2>&1; then
#     echo "${TIMESTAMP} [end_guest_session] WARNING: guestlab processes still running." >> "$LOG"
# fi

echo "${TIMESTAMP} [end_guest_session] Done (placeholder)." >> "$LOG"
exit 0
