#!/usr/bin/env bash
# GraceLab — reboot this station
# Called by gracelab_client.py in response to an admin-issued "reboot"
# station command (see stations.py's reboot_station route).
#
# Narrowly scoped: performs exactly one action and nothing else. Does not
# accept arguments, so the sudoers grant for this script cannot be abused to
# run anything but a plain reboot.
#
# The client reports the command complete BEFORE invoking this script (see
# gracelab_client.py's _handle_reboot_command) — once systemctl reboot runs
# there is no reliable way to report anything further.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${SCRIPT_DIR}/common.sh"

gl_log INFO "reboot_station: reboot invoked"
systemctl reboot
