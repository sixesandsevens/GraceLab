#!/usr/bin/env bash
# Suppress Linux Mint / Ubuntu update notifications for the kiosk user.
# Run once during station setup (as the kiosk user, not root).
#
# What this does:
#   1. Kills any already-running update-notifier or mintupdate process
#   2. Disables their XDG autostart entries for this user
#   3. Disables apt periodic update checks that trigger the notifier
#
# Updates should be handled by staff/admin via: sudo apt-get update && sudo apt-get upgrade

set -euo pipefail

log() { echo "$(date -Is) [disable-updater] $*"; }

# ── 1. Kill any running instances ────────────────────────────────────────────
for proc in update-notifier mintupdate mintupdate-launcher; do
    if pgrep -x "$proc" >/dev/null 2>&1; then
        log "Killing $proc"
        pkill -x "$proc" || true
    fi
done

# ── 2. Disable XDG autostart entries for this user ───────────────────────────
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
mkdir -p "$AUTOSTART_DIR"

for entry in \
    update-notifier.desktop \
    mintupdate.desktop \
    mintupdate-launcher.desktop \
    com.linuxmint.updates.desktop \
    org.gnome.Software.desktop \
    update-manager.desktop
do
    SYSTEM_FILE="/etc/xdg/autostart/${entry}"
    USER_FILE="${AUTOSTART_DIR}/${entry}"

    if [[ -f "$SYSTEM_FILE" ]] || [[ -f "$USER_FILE" ]]; then
        log "Disabling autostart: $entry"
        # Override with a user-level entry that sets Hidden=true
        cat > "$USER_FILE" <<EOF
[Desktop Entry]
Hidden=true
EOF
    fi
done

# ── 3. Suppress apt periodic checks that wake the notifier ───────────────────
APT_PERIODIC="$HOME/.config/gracelab/apt-periodic-override"
SYSTEM_APT_PERIODIC="/etc/apt/apt.conf.d/10periodic"

if [[ -f "$SYSTEM_APT_PERIODIC" ]]; then
    # We can't write /etc/apt as a normal user, but we can check if it's
    # already set to 0. Print a reminder if not.
    if grep -q 'Update-Package-Lists "0"' "$SYSTEM_APT_PERIODIC" 2>/dev/null; then
        log "apt periodic checks already disabled system-wide."
    else
        log "NOTE: To fully suppress apt checks system-wide, run as root:"
        log "  sudo tee /etc/apt/apt.conf.d/99gracelab-no-updates >/dev/null <<'EOF'"
        log '  APT::Periodic::Update-Package-Lists "0";'
        log '  APT::Periodic::Download-Upgradeable-Packages "0";'
        log '  APT::Periodic::AutocleanInterval "0";'
        log '  APT::Periodic::Unattended-Upgrade "0";'
        log '  EOF'
    fi
fi

log "Done. Update notifier suppressed for user: $USER"
