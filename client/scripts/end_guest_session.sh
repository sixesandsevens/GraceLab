#!/usr/bin/env bash
# GraceLab — end guest session hook
# Called when a session expires, is ended by staff, or fails.
# Runs BEFORE reset_guest_home.sh.
#
# Exit 0  = success (reset script will run next)
# Exit != 0 = failure (client marks station needs_attention, skips reset)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

gl_log INFO "end_guest_session: hook fired"

# Clear stale guest-requested logout flags before and after killing guestlab.
gl_clear_guest_logout_flags

# Kill all guestlab processes and terminate the login session.
gl_kill_guest

# Lock the account so it cannot be logged into between sessions.
passwd -l "$GUEST_USER" 2>/dev/null || true

gl_log INFO "end_guest_session: done"
exit 0
