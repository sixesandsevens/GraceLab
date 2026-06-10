#!/usr/bin/env bash
# GraceLab — start guest session hook
# Called after the server confirms a session has started.
#
# Exit 0  = success (session proceeds)
# Exit != 0 = failure (client marks station needs_attention and ends session)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

gl_log INFO "start_guest_session: hook fired"

# Unlock the guest account so it can be used.
passwd -u "$GUEST_USER" 2>/dev/null || true

# Clear any stale lock/logout files from a previous session.
gl_clear_guest_logout_flags
rm -f /tmp/.gracelab-guest-* 2>/dev/null || true

# --------------------------------------------------------------------------
# Optional: launch a guest desktop session here.
# Uncomment and adapt ONE of the following approaches:
#
# A) Switch to guest user X session (if running a full desktop):
#    su - "$GUEST_USER" -c "env DISPLAY=:0 startxfce4 &" || true
#
# B) Open a browser as guest user (single-app kiosk):
#    su - "$GUEST_USER" -c "env DISPLAY=:0 firefox --kiosk about:blank &" || true
#
# C) Use systemd to start a guest session service:
#    systemctl start gracelab-guest-session.service
# --------------------------------------------------------------------------

gl_log INFO "start_guest_session: done"
exit 0
