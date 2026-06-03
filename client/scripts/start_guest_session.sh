#!/usr/bin/env bash
# GraceLab — start guest session hook
# Called after the server confirms a session has started.
#
# Phase 4A: placeholder that proves the hook fires and logs correctly.
# Phase 4B: will launch the guestlab desktop session or switch display.
#
# Exit 0  = success (session proceeds normally)
# Exit != 0 = failure (client marks station needs_attention, ends session)

set -euo pipefail

LOG="/var/log/gracelab-client.log"
TIMESTAMP=$(date --iso-8601=seconds)

echo "${TIMESTAMP} [start_guest_session] Hook fired." >> "$LOG"

# --------------------------------------------------------------------------
# Phase 4B: replace the block below with real guest session launch.
# Examples:
#   su - guestlab -c "startxfce4 &"
#   loginctl enable-linger guestlab
#   systemctl start gracelab-guest-session.service
# --------------------------------------------------------------------------

echo "${TIMESTAMP} [start_guest_session] Done (placeholder)." >> "$LOG"
exit 0
