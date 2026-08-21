#!/usr/bin/env bash
# GraceLab — reboot this station
# Called by gracelab_client.py in response to an admin-issued "reboot"
# station command (see stations.py's reboot_station route).
#
# Narrowly scoped: performs exactly one action and nothing else. Does not
# accept arguments, so the sudoers grant for this script cannot be abused to
# run anything but a plain reboot.
#
# --no-block: queue the shutdown job over D-Bus and return immediately,
# rather than blocking until the machine actually goes down. This is what
# lets the client (see gracelab_client.py's _handle_reboot_command) get a
# real, timely exit code to report "complete" or "failed" on — reporting
# complete only AFTER this call succeeds, never before.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

gl_log INFO "reboot_station: reboot invoked"
systemctl reboot --no-block
