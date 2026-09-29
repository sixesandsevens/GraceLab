#!/usr/bin/env bash
# Deny NetworkManager changes to the kiosk accounts (gracelab, guestlab).
#
# NetworkManager's default polkit policy lets any active local session
# disconnect, switch networks, toggle Wi-Fi and edit saved connections, so a
# guest could knock a station offline from the tray — and a disconnected
# station can't be fixed from the dashboard. Every other account (including
# the local administrator) keeps the distribution defaults.
#
# Called as root by tools/install-client.sh and updater/do-install.sh;
# idempotent. NOT in sudoers — gracelab must never be able to run this.
#
# Both polkit rule formats are written because stations may be on either:
#   - polkit 0.105 (Mint 21.x):  .pkla files under /etc/polkit-1/localauthority
#   - polkit 121+  (Mint 22.x):  JavaScript rules under /etc/polkit-1/rules.d
# Each version ignores the other's format, so writing both is harmless.
# polkitd watches both directories and picks up changes without a restart.

set -euo pipefail

PKLA_DIR="/etc/polkit-1/localauthority/90-mandatory.d"
PKLA_FILE="${PKLA_DIR}/90-gracelab-network.pkla"
RULES_DIR="/etc/polkit-1/rules.d"
# Low number: JS rules run in lexical order and the first result wins, so
# this must come before any distro rule that grants NM access.
RULES_FILE="${RULES_DIR}/10-gracelab-network.rules"

if [[ $EUID -ne 0 ]]; then
    echo "install-network-lockdown.sh must run as root" >&2
    exit 1
fi

mkdir -p "$PKLA_DIR" "$RULES_DIR"

cat > "$PKLA_FILE" <<'EOF'
# GraceLab: kiosk accounts may not change networking.
# Managed by install-network-lockdown.sh — rewritten on every client update.
[GraceLab deny NetworkManager for kiosk accounts]
Identity=unix-user:gracelab;unix-user:guestlab
Action=org.freedesktop.NetworkManager.*
ResultAny=no
ResultInactive=no
ResultActive=no
EOF
chmod 644 "$PKLA_FILE"

cat > "$RULES_FILE" <<'EOF'
// GraceLab: kiosk accounts may not change networking.
// Managed by install-network-lockdown.sh — rewritten on every client update.
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") === 0 &&
        (subject.user === "gracelab" || subject.user === "guestlab")) {
        return polkit.Result.NO;
    }
});
EOF
chmod 644 "$RULES_FILE"

echo "NetworkManager lockdown installed → ${PKLA_FILE}, ${RULES_FILE}"
