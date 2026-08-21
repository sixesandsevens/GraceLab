#!/usr/bin/env bash
# GraceLab — enable the admin override (Patch C maintenance mode)
# Called by gracelab_client.py when entering maintenance mode.
#
# Creates /run/gracelab-admin-override, a root-owned sentinel (NOT inside
# the shared /run/gracelab/ IPC directory, which guestlab can also write to)
# so a guest can never fake or clear it. Read back with a plain, unprivileged
# stat() — only creating/removing it requires root, which is why this runs
# via the sudoers entry below instead of being written directly by the
# gracelab-owned Python client.
#
# Also clears any stale local "return to GraceLab" request flag from a
# previous maintenance cycle so a new maintenance session always starts
# clean — see request_maintenance_exit.sh.
#
# Exit 0  = success
# Exit != 0 = failure (client logs it; maintenance still proceeds without
#             the override, degraded but not unsafe — the session-admission
#             lock does not depend on this file)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

OVERRIDE_FILE="/run/gracelab-admin-override"
EXIT_REQUEST_FILE="/run/gracelab-maintenance-exit-requested"

gl_log INFO "enable_admin_override: hook fired"

touch "$OVERRIDE_FILE"
chmod 644 "$OVERRIDE_FILE"
rm -f "$EXIT_REQUEST_FILE"

gl_log INFO "enable_admin_override: done"
exit 0
