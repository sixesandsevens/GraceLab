#!/usr/bin/env bash
# GraceLab shortcut audit — dumps every xfce4-keyboard-shortcuts binding for
# the gracelab account so you can eyeball what XFCE/Mint ships by default and
# confirm nothing dangerous (terminal, file manager, desktop, menu) is live.
#
# Run as root on the station (it needs to reach the gracelab session bus):
#   sudo ./tools/audit-shortcuts.sh
#
# Output is grouped into:
#   1. All bindings, raw
#   2. Anything still pointing at a real command (not blank, not /bin/true)
#   3. A simple risk flag for common escape-prone keywords

set -euo pipefail

GRACELAB_USER="gracelab"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This script must be run as root (sudo)." >&2
    exit 1
fi

if ! id "$GRACELAB_USER" &>/dev/null; then
    echo "User ${GRACELAB_USER} does not exist on this machine." >&2
    exit 1
fi

GRACE_UID="$(id -u "$GRACELAB_USER")"
GRACE_BUS="unix:path=/run/user/${GRACE_UID}/bus"

if [[ ! -S "/run/user/${GRACE_UID}/bus" ]]; then
    echo "No active D-Bus session for ${GRACELAB_USER} (is it logged in on :0?)." >&2
    exit 1
fi

_run() {
    sudo -u "$GRACELAB_USER" env DISPLAY=:0 DBUS_SESSION_BUS_ADDRESS="$GRACE_BUS" "$@"
}

echo "════════════════════════════════════════════════════════════════"
echo " GraceLab keyboard shortcut audit — $(date -Is)"
echo " Account: ${GRACELAB_USER} (uid ${GRACE_UID})"
echo "════════════════════════════════════════════════════════════════"
echo

echo "── All bindings (raw) ──────────────────────────────────────────"
_run xfconf-query -c xfce4-keyboard-shortcuts -lv | sort
echo

echo "── Bindings still pointing at a real command ──────────────────"
echo "  (blank values and /bin/true are already neutralised)"
_run xfconf-query -c xfce4-keyboard-shortcuts -lv \
    | awk '{ $1=""; print }' \
    | sort -u \
    | grep -vE '^\s*$' \
    | grep -vE '/bin/true' \
    || true
echo

echo "── Risk flags (keyword match against command/binding names) ──"
_run xfconf-query -c xfce4-keyboard-shortcuts -lv \
    | grep -iE 'term|thunar|nautilus|files|shell|bash|xfdesktop|menu|whisker|appfinder|run-dialog|desktop|fullscreen|screensaver|lock' \
    || echo "  (none matched — review the raw list above manually too)"
echo

echo "── Currently running processes that could provide an escape ───"
pgrep -a -u "$GRACELAB_USER" -f 'xfce4-panel|xfce-superkey|xfce4-appfinder|thunar|xterm|gnome-terminal|xfce4-terminal' \
    || echo "  (none found — good)"
echo

echo "════════════════════════════════════════════════════════════════"
echo " Review complete. Any line under 'real command' or 'risk flags'"
echo " that isn't already covered by lockdown-operator.sh needs a fix."
echo "════════════════════════════════════════════════════════════════"
