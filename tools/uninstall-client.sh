#!/usr/bin/env bash
# GraceLab client uninstaller
#
# Removes everything install-client.sh put on this machine.
# Safe to run before a clean reinstall.
#
# Usage:
#   sudo ./tools/uninstall-client.sh

set -euo pipefail

info()  { printf '\e[32m[INFO]\e[0m  %s\n' "$*"; }
step()  { printf '\n\e[1m>>> %s\e[0m\n' "$*"; }

[[ "$(id -u)" -eq 0 ]] || { echo "Must be run as root (sudo)."; exit 1; }

# ---------------------------------------------------------------------------
# Stop running processes
# ---------------------------------------------------------------------------

step "Stopping GraceLab processes"

pkill -u gracelab -f gracelab_client.py 2>/dev/null && info "Killed gracelab_client.py" || true
pkill -u guestlab 2>/dev/null            && info "Killed guestlab processes"    || true
loginctl terminate-user gracelab 2>/dev/null || true
loginctl terminate-user guestlab 2>/dev/null || true
sleep 1

# ---------------------------------------------------------------------------
# Sudoers
# ---------------------------------------------------------------------------

step "Removing sudoers rules"
rm -f /etc/sudoers.d/gracelab-client
info "Removed /etc/sudoers.d/gracelab-client"

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

step "Removing users"

if id gracelab &>/dev/null; then
    userdel -r gracelab 2>/dev/null || userdel gracelab 2>/dev/null || true
    rm -rf /home/gracelab
    info "Removed user: gracelab"
fi

if id guestlab &>/dev/null; then
    userdel -r guestlab 2>/dev/null || userdel guestlab 2>/dev/null || true
    rm -rf /home/guestlab
    info "Removed user: guestlab"
fi

# ---------------------------------------------------------------------------
# Installation directories
# ---------------------------------------------------------------------------

step "Removing installation directories"

rm -rf /opt/gracelab-client
info "Removed /opt/gracelab-client"

rm -rf /etc/gracelab
info "Removed /etc/gracelab"

rm -rf /var/log/gracelab
info "Removed /var/log/gracelab"

# ---------------------------------------------------------------------------
# System desktop files
# ---------------------------------------------------------------------------

step "Removing system desktop files"

rm -f /usr/share/applications/gracelab-apps.desktop
rm -f /usr/share/applications/gracelab-endsession.desktop
info "Removed gracelab desktop files"

# ---------------------------------------------------------------------------
# LightDM autologin
# ---------------------------------------------------------------------------

step "Removing LightDM autologin"

rm -f /etc/lightdm/lightdm.conf.d/90-gracelab.conf
info "Removed /etc/lightdm/lightdm.conf.d/90-gracelab.conf"

# ---------------------------------------------------------------------------
# AccountsService
# ---------------------------------------------------------------------------

step "Removing AccountsService entries"

rm -f /var/lib/AccountsService/users/gracelab
rm -f /var/lib/AccountsService/users/guestlab
info "Removed AccountsService entries"

# ---------------------------------------------------------------------------
# Xorg DontZap
# ---------------------------------------------------------------------------

step "Removing DontZap xorg config"

rm -f /etc/X11/xorg.conf.d/10-serverflags.conf
info "Removed /etc/X11/xorg.conf.d/10-serverflags.conf"

# ---------------------------------------------------------------------------
# Firefox policies
# ---------------------------------------------------------------------------

step "Removing Firefox policies"

rm -f /etc/firefox/policies/policies.json
rmdir /etc/firefox/policies 2>/dev/null || true
rmdir /etc/firefox 2>/dev/null || true
info "Removed Firefox policies"

# ---------------------------------------------------------------------------
# Temp files
# ---------------------------------------------------------------------------

rm -f /tmp/gracelab-session.json /tmp/gracelab-guest-logout
info "Cleared temp files"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

printf '\n'
printf '═%.0s' {1..60}
printf '\n'
printf ' GraceLab uninstalled. Ready for a clean reinstall.\n'
printf '═%.0s' {1..60}
printf '\n\n'
printf 'Reboot before reinstalling to ensure LightDM picks up the changes.\n\n'
