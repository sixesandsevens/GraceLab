#!/usr/bin/env bash
# GraceLab — reset guest home directory
# Called after end_guest_session.sh succeeds.
# Wipes /home/guestlab and restores it from the template.
#
# Phase 4A: placeholder — skips actual wipe to avoid destroying dev machines.
# Phase 4B: uncomment the real implementation below.
#
# Exit 0  = success (client returns to idle)
# Exit != 0 = failure (client marks station needs_attention)

set -euo pipefail

GUEST_USER="guestlab"
GUEST_HOME="/home/${GUEST_USER}"
TEMPLATE_HOME="/opt/gracelab-client/template-home"
LOG="${GRACELAB_LOG:-/tmp/gracelab-client.log}"
TIMESTAMP=$(date --iso-8601=seconds)

echo "${TIMESTAMP} [reset_guest_home] Hook fired." >> "$LOG"

# --------------------------------------------------------------------------
# Phase 4B: uncomment the block below when running on a real lab workstation.
# Make sure end_guest_session.sh has already killed guestlab processes.
# --------------------------------------------------------------------------
#
# if [ ! -d "$TEMPLATE_HOME" ]; then
#     echo "${TIMESTAMP} [reset_guest_home] ERROR: template-home not found at $TEMPLATE_HOME" >> "$LOG"
#     exit 1
# fi
#
# # Belt-and-suspenders: kill any remaining guestlab processes before wiping
# pkill -KILL -u "$GUEST_USER" 2>/dev/null || true
# sleep 1
#
# rm -rf "$GUEST_HOME"
# mkdir -p "$GUEST_HOME"
# rsync -a "${TEMPLATE_HOME}/" "${GUEST_HOME}/"
# chown -R "${GUEST_USER}:${GUEST_USER}" "$GUEST_HOME"
# chmod 700 "$GUEST_HOME"
#
# echo "${TIMESTAMP} [reset_guest_home] Reset complete." >> "$LOG"

echo "${TIMESTAMP} [reset_guest_home] Done (placeholder — real wipe disabled)." >> "$LOG"
exit 0
