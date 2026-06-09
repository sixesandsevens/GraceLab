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

# Kill all guestlab processes and terminate the login session.
gl_kill_guest

# Verify no guestlab processes remain (warn only — don't fail).
if pgrep -u "$GUEST_USER" > /dev/null 2>&1; then
    gl_log WARN "end_guest_session: some guestlab processes are still running after kill"
fi

# Lock the account so it cannot be logged into between sessions.
passwd -l "$GUEST_USER" 2>/dev/null || true

gl_log INFO "end_guest_session: done"
exit 0
