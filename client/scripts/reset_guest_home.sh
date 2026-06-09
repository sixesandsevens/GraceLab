#!/usr/bin/env bash
# GraceLab — reset guest home directory
# Called after end_guest_session.sh succeeds.
# Wipes /home/guestlab and restores it from the clean template.
#
# Exit 0  = success (client returns to idle)
# Exit != 0 = failure (client marks station needs_attention)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

gl_log INFO "reset_guest_home: hook fired"

if [ ! -d "$TEMPLATE_HOME" ]; then
    gl_log ERROR "reset_guest_home: template not found at ${TEMPLATE_HOME}"
    exit 1
fi

# Belt-and-suspenders kill before wiping.
gl_kill_guest

# Wipe and restore from template.
rm -rf "$GUEST_HOME"
mkdir -p "$GUEST_HOME"
rsync -a "${TEMPLATE_HOME}/" "${GUEST_HOME}/"
chown -R "${GUEST_USER}:${GUEST_USER}" "$GUEST_HOME"
chmod 700 "$GUEST_HOME"

gl_log INFO "reset_guest_home: reset complete"
exit 0
