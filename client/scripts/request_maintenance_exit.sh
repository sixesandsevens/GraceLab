#!/usr/bin/env bash
# GraceLab — locally request exiting maintenance mode
# A convenience for the administrator working the console during
# maintenance mode: run this (e.g. via a desktop launcher or menu entry in
# the admin session) instead of using the web dashboard's Return to
# GraceLab button.
#
# Narrowly scoped: creates exactly one root-owned flag file. It does NOT
# itself clear server-side maintenance state, disable the admin override, or
# switch the display — the already-running, already-authenticated
# gracelab_client.py process does all of that (see
# _check_local_maintenance_exit_request), because it already holds the
# station's API credentials and this script deliberately does not.
#
# Grantable via sudo to the gracelab-admin group rather than a specific
# username, since the actual local administrator account is a deployment
# choice (see [maintenance] admin_user in client_config.ini) — add whichever
# account(s) should have this convenience to that group with:
#   usermod -aG gracelab-admin <username>

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

touch /run/gracelab-maintenance-exit-requested
gl_log INFO "request_maintenance_exit: local exit request flagged"
