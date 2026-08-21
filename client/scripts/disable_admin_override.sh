#!/usr/bin/env bash
# GraceLab — disable the admin override (Patch C maintenance mode)
# Called by gracelab_client.py when exiting maintenance mode.
#
# Removes /run/gracelab-admin-override so window-watchdog.sh and
# lockdown-operator.sh resume enforcing kiosk state, and clears any stale
# local "return to GraceLab" request flag — see enable_admin_override.sh and
# request_maintenance_exit.sh for the full lifecycle these two files share.
#
# Exit 0  = success
# Exit != 0 = failure (client marks the station needs_attention — an
#             override that fails to clear could leave watchdog/lockdown
#             enforcement suppressed indefinitely, so this one IS treated
#             as fatal, unlike enable_admin_override.sh's failure)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

OVERRIDE_FILE="/run/gracelab-admin-override"
EXIT_REQUEST_FILE="/run/gracelab-maintenance-exit-requested"

gl_log INFO "disable_admin_override: hook fired"

rm -f "$OVERRIDE_FILE" "$EXIT_REQUEST_FILE"

gl_log INFO "disable_admin_override: done"
exit 0
