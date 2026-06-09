#!/usr/bin/env bash
# GraceLab client installer / full-machine provisioner
#
# Usage:
#   sudo ./tools/install-client.sh \
#     --server-url http://192.168.1.246:5000 \
#     --hostname   gracelab-01 \
#     --token      PASTE_TOKEN_HERE
#
# What this script does:
#   - Creates gracelab and guestlab system users, adds both to nopasswdlogin
#   - Installs client files to /opt/gracelab-client/releases/<version>
#   - Creates /opt/gracelab-client/current symlink
#   - Writes /etc/gracelab/client_config.ini
#   - Installs template-home from repo
#   - Installs sudoers rules for lifecycle scripts
#   - Installs desktop autostart entry for gracelab user
#   - Writes gracelab XFCE kiosk lockdown settings (no panel, shortcuts disabled)
#   - Configures LightDM autologin for gracelab
#   - Hides guestlab from LightDM greeter via AccountsService
#   - Disables Ctrl+Alt+Backspace (DontZap)

set -euo pipefail

CLIENT_VERSION="0.2.0"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CLIENT_DIR="${SCRIPT_DIR}/../client"
REPO_TEMPLATE_DIR="${REPO_CLIENT_DIR}/template-home"

INSTALL_BASE="/opt/gracelab-client"
RELEASE_DIR="${INSTALL_BASE}/releases/${CLIENT_VERSION}"
CURRENT_LINK="${INSTALL_BASE}/current"
CONFIG_DIR="/etc/gracelab"
CONFIG_FILE="${CONFIG_DIR}/client_config.ini"
LOG_DIR="/var/log/gracelab"
TEMPLATE_HOME="${INSTALL_BASE}/template-home"
SUDOERS_FILE="/etc/sudoers.d/gracelab-client"

GRACELAB_USER="gracelab"
GUEST_USER="guestlab"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

info()  { printf '\e[32m[INFO]\e[0m  %s\n' "$*"; }
warn()  { printf '\e[33m[WARN]\e[0m  %s\n' "$*"; }
die()   { printf '\e[31m[ERR]\e[0m   %s\n' "$*" >&2; exit 1; }
step()  { printf '\n\e[1m>>> %s\e[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

SERVER_URL=""
HOSTNAME_ARG=""
TOKEN_ARG=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-url) SERVER_URL="$2"; shift 2 ;;
        --hostname)   HOSTNAME_ARG="$2"; shift 2 ;;
        --token)      TOKEN_ARG="$2"; shift 2 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

[[ -n "$SERVER_URL"   ]] || die "Missing --server-url"
[[ -n "$HOSTNAME_ARG" ]] || die "Missing --hostname"
[[ -n "$TOKEN_ARG"    ]] || die "Missing --token"

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

step "Pre-flight"

[[ "$(id -u)" -eq 0 ]] || die "This script must be run as root (sudo)."

command -v python3 >/dev/null 2>&1 || die "python3 is required but not installed."

if ! python3 -c "import tkinter" 2>/dev/null; then
    info "Installing python3-tk..."
    apt-get install -y python3-tk >/dev/null
fi

if ! command -v rsync >/dev/null 2>&1; then
    info "Installing rsync..."
    apt-get install -y rsync >/dev/null
fi

[[ -f "${REPO_CLIENT_DIR}/gracelab_client.py" ]] || \
    die "Client source not found at ${REPO_CLIENT_DIR}. Run from the GraceLab repo."

[[ -d "${REPO_TEMPLATE_DIR}" ]] || \
    die "template-home not found at ${REPO_TEMPLATE_DIR}. Run from the GraceLab repo."

info "Pre-flight OK."

# ---------------------------------------------------------------------------
# Install required system packages
# ---------------------------------------------------------------------------

step "Installing required packages"

apt-get update -qq
apt-get install -y \
    python3-tk python3-gi \
    aisleriot gnome-mahjongg gnome-mines quadrapassel gnome-sudoku \
    libreoffice-writer libreoffice-calc libreoffice-draw libreoffice-impress \
    drawing gnome-calculator gnome-calendar xfce4-dict xed \
    system-config-printer celluloid rhythmbox \
    onboard magnus orca xfce4-settings \
    zenity \
    2>/dev/null || warn "Some packages may have failed to install — check apt output above."

info "Packages installed."

# ---------------------------------------------------------------------------
# Create users
# ---------------------------------------------------------------------------

step "Creating system users"

if ! id "$GRACELAB_USER" &>/dev/null; then
    useradd \
        --create-home \
        --shell /bin/bash \
        --comment "GraceLab kiosk operator" \
        "$GRACELAB_USER"
    passwd -l "$GRACELAB_USER" >/dev/null 2>&1 || true
    info "Created user: ${GRACELAB_USER} (account locked)"
else
    info "User ${GRACELAB_USER} already exists — skipping."
fi

if ! id "$GUEST_USER" &>/dev/null; then
    useradd \
        --create-home \
        --shell /bin/bash \
        --comment "GraceLab guest session user" \
        "$GUEST_USER"
    passwd -l "$GUEST_USER" >/dev/null 2>&1 || true
    info "Created user: ${GUEST_USER} (account locked)"
else
    info "User ${GUEST_USER} already exists — skipping."
fi

# ---------------------------------------------------------------------------
# nopasswdlogin group
# ---------------------------------------------------------------------------

step "Configuring nopasswdlogin group"

if ! getent group nopasswdlogin >/dev/null 2>&1; then
    groupadd nopasswdlogin
    info "Created group: nopasswdlogin"
fi
usermod -aG nopasswdlogin "${GRACELAB_USER}"
usermod -aG nopasswdlogin "${GUEST_USER}"
info "Added ${GRACELAB_USER} and ${GUEST_USER} to nopasswdlogin."

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

step "Creating directory layout"

mkdir -p "${INSTALL_BASE}/downloads"
mkdir -p "${RELEASE_DIR}/scripts"
mkdir -p "${RELEASE_DIR}/updater"
mkdir -p "$CONFIG_DIR"
mkdir -p "$LOG_DIR"

# Root owns the entire installation tree so gracelab cannot modify scripts
# that are executed as root via sudoers (privilege escalation prevention).
# Only the downloads scratch dir needs to be gracelab-writable.
chown -R root:root "$INSTALL_BASE"
chown "${GRACELAB_USER}:${GRACELAB_USER}" "${INSTALL_BASE}/downloads"
chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "$LOG_DIR"

info "Directories OK."

# ---------------------------------------------------------------------------
# Install client files
# ---------------------------------------------------------------------------

step "Installing client files → ${RELEASE_DIR}"

find "${REPO_CLIENT_DIR}" -maxdepth 1 -name "*.py" -exec cp {} "${RELEASE_DIR}/" \;
cp "${REPO_CLIENT_DIR}/client_config.ini.example" "${RELEASE_DIR}/"
rsync -a "${REPO_CLIENT_DIR}/scripts/" "${RELEASE_DIR}/scripts/"
chmod +x "${RELEASE_DIR}/scripts/"*.sh

info "Client files installed."

# ---------------------------------------------------------------------------
# Create/update current symlink
# ---------------------------------------------------------------------------

step "Updating current symlink"

if [[ -L "$CURRENT_LINK" ]]; then
    OLD_TARGET="$(readlink "$CURRENT_LINK")"
    info "Previous current → ${OLD_TARGET}"
fi

ln -sfn "$RELEASE_DIR" "$CURRENT_LINK"
info "current → ${RELEASE_DIR}"

# ---------------------------------------------------------------------------
# Install system-wide desktop files for panel launchers
# ---------------------------------------------------------------------------

step "Installing system desktop files"

cat > "/usr/share/applications/gracelab-apps.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Apps
Comment=GraceLab app launcher
Exec=bash -c 'python3 ${CURRENT_LINK}/gracelab_apps.py 2>/tmp/gracelab-apps-err.log'
Icon=applications-other
Terminal=false
EOF

cat > "/usr/share/applications/gracelab-endsession.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=End Session
Comment=End your GraceLab session
Exec=bash -c 'zenity --question --title="End Session" --text="End your session and return to the main screen?\n\nUnsaved files will be deleted." --ok-label="End Session" --cancel-label="Stay" --icon-name=system-log-out 2>/dev/null && touch /tmp/gracelab-guest-logout'
Icon=system-log-out
Terminal=false
EOF

info "System desktop files installed → /usr/share/applications/"

# ---------------------------------------------------------------------------
# Write client config
# ---------------------------------------------------------------------------

step "Writing ${CONFIG_FILE}"

if [[ -f "$CONFIG_FILE" ]]; then
    cp "$CONFIG_FILE" "${CONFIG_FILE}.bak.$(date +%s)"
    warn "Existing config backed up."
fi

cat > "$CONFIG_FILE" <<EOF
[server]
url = ${SERVER_URL}

[station]
hostname = ${HOSTNAME_ARG}
token = ${TOKEN_ARG}

[session]
warning_minutes = 5
heartbeat_interval_seconds = 30
sync_interval_seconds = 12

[paths]
start_script = sudo ${CURRENT_LINK}/scripts/start_guest_session.sh
end_script   = sudo ${CURRENT_LINK}/scripts/end_guest_session.sh
reset_script = sudo ${CURRENT_LINK}/scripts/reset_guest_home.sh

[ui]
fullscreen = true
organization_name = Grace Marketplace

[updates]
enabled = true
channel = stable
check_interval_seconds = 300
install_policy = idle_only
EOF

chmod 640 "$CONFIG_FILE"
chown "root:${GRACELAB_USER}" "$CONFIG_FILE"
info "Config written."

# ---------------------------------------------------------------------------
# Install template-home from repo
# ---------------------------------------------------------------------------

step "Installing guest template-home"

mkdir -p "${TEMPLATE_HOME}"
rsync -a --delete "${REPO_TEMPLATE_DIR}/" "${TEMPLATE_HOME}/"
chown -R root:root "${TEMPLATE_HOME}"
info "template-home installed from repo → ${TEMPLATE_HOME}"

# ---------------------------------------------------------------------------
# Install sudoers
# ---------------------------------------------------------------------------

step "Installing sudoers rules → ${SUDOERS_FILE}"

cat > "$SUDOERS_FILE" <<EOF
# GraceLab client lifecycle script permissions
# Generated by install-client.sh on $(date -I)

Defaults!${CURRENT_LINK}/scripts/start_guest_session.sh !requiretty
Defaults!${CURRENT_LINK}/scripts/end_guest_session.sh   !requiretty
Defaults!${CURRENT_LINK}/scripts/reset_guest_home.sh    !requiretty

${GRACELAB_USER} ALL=(root) NOPASSWD: ${CURRENT_LINK}/scripts/start_guest_session.sh
${GRACELAB_USER} ALL=(root) NOPASSWD: ${CURRENT_LINK}/scripts/end_guest_session.sh
${GRACELAB_USER} ALL=(root) NOPASSWD: ${CURRENT_LINK}/scripts/reset_guest_home.sh
EOF

chmod 440 "$SUDOERS_FILE"

if ! visudo -c -f "$SUDOERS_FILE" >/dev/null 2>&1; then
    rm -f "$SUDOERS_FILE"
    die "sudoers syntax check failed — file removed. Check manually."
fi

info "Sudoers installed and validated."

# ---------------------------------------------------------------------------
# Desktop autostart for gracelab
# ---------------------------------------------------------------------------

step "Installing desktop autostart for ${GRACELAB_USER}"

AUTOSTART_DIR="/home/${GRACELAB_USER}/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

cat > "${AUTOSTART_DIR}/gracelab-client.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=GraceLab Client
Comment=GraceLab kiosk session manager
Exec=python3 ${CURRENT_LINK}/gracelab_client.py
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
Hidden=false
NoDisplay=false
EOF

chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "/home/${GRACELAB_USER}/.config"
info "Autostart entry written."

# ---------------------------------------------------------------------------
# gracelab XFCE kiosk lockdown
# ---------------------------------------------------------------------------

step "Writing gracelab XFCE kiosk settings"

GRACELAB_XFCONF_DIR="/home/${GRACELAB_USER}/.config/xfce4/xfconf/xfce-perchannel-xml"
mkdir -p "${GRACELAB_XFCONF_DIR}"

cat > "${GRACELAB_XFCONF_DIR}/xfce4-keyboard-shortcuts.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-keyboard-shortcuts" version="1.0">
  <property name="commands" type="empty">
    <property name="default" type="empty">
      <property name="&lt;Primary&gt;&lt;Alt&gt;t" type="string" value="/bin/true"/>
      <property name="&lt;Primary&gt;&lt;Shift&gt;Escape" type="string" value="/bin/true"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Delete" type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;l" type="string" value="/bin/true"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;l" type="string" value="/bin/true"/>
      <property name="XF86ScreenSaver" type="string" value="/bin/true"/>
    </property>
  </property>
  <property name="xfwm4" type="empty">
    <property name="default" type="empty">
      <property name="&lt;Control&gt;&lt;Alt&gt;Left" type="string" value=""/>
      <property name="&lt;Control&gt;&lt;Alt&gt;Right" type="string" value=""/>
      <property name="&lt;Control&gt;&lt;Alt&gt;Up" type="string" value=""/>
      <property name="&lt;Control&gt;&lt;Alt&gt;Down" type="string" value=""/>
      <property name="&lt;Control&gt;F1" type="string" value=""/>
      <property name="&lt;Control&gt;F2" type="string" value=""/>
      <property name="&lt;Control&gt;F3" type="string" value=""/>
      <property name="&lt;Control&gt;F4" type="string" value=""/>
      <property name="&lt;Control&gt;F5" type="string" value=""/>
      <property name="&lt;Control&gt;F6" type="string" value=""/>
      <property name="&lt;Control&gt;F7" type="string" value=""/>
      <property name="&lt;Control&gt;F8" type="string" value=""/>
      <property name="&lt;Control&gt;F9" type="string" value=""/>
      <property name="&lt;Control&gt;F10" type="string" value=""/>
      <property name="&lt;Control&gt;F11" type="string" value=""/>
      <property name="&lt;Control&gt;F12" type="string" value=""/>
      <property name="&lt;Shift&gt;&lt;Alt&gt;Page_Down" type="string" value=""/>
      <property name="&lt;Shift&gt;&lt;Alt&gt;Page_Up" type="string" value=""/>
    </property>
  </property>
</channel>
XMLEOF

cat > "${GRACELAB_XFCONF_DIR}/xfce4-desktop.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-desktop" version="1.0">
  <property name="desktop-menu" type="empty">
    <property name="show" type="bool" value="false"/>
  </property>
  <property name="windowlist-menu" type="empty">
    <property name="show" type="bool" value="false"/>
  </property>
</channel>
XMLEOF

cat > "${GRACELAB_XFCONF_DIR}/xfce4-panel.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-panel" version="1.0">
  <property name="configver" type="int" value="2"/>
  <property name="panels" type="array">
  </property>
</channel>
XMLEOF

chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "/home/${GRACELAB_USER}/.config"
info "gracelab XFCE kiosk settings written."

# ---------------------------------------------------------------------------
# LightDM autologin
# ---------------------------------------------------------------------------

step "Configuring LightDM autologin"

LIGHTDM_CONF_DIR="/etc/lightdm/lightdm.conf.d"
mkdir -p "${LIGHTDM_CONF_DIR}"

cat > "${LIGHTDM_CONF_DIR}/90-gracelab.conf" <<EOF
[Seat:*]
autologin-guest=false
autologin-user=${GRACELAB_USER}
autologin-user-timeout=0
EOF

info "LightDM autologin configured → ${LIGHTDM_CONF_DIR}/90-gracelab.conf"

# ---------------------------------------------------------------------------
# AccountsService (greeter visibility)
# ---------------------------------------------------------------------------

step "Configuring AccountsService"

ACCOUNTS_DIR="/var/lib/AccountsService/users"
mkdir -p "${ACCOUNTS_DIR}"

cat > "${ACCOUNTS_DIR}/${GRACELAB_USER}" <<EOF
[User]
SystemAccount=false
EOF

cat > "${ACCOUNTS_DIR}/${GUEST_USER}" <<EOF
[User]
SystemAccount=true
EOF

info "AccountsService configured (${GRACELAB_USER} visible, ${GUEST_USER} hidden from greeter)."

# ---------------------------------------------------------------------------
# Disable Ctrl+Alt+Backspace (DontZap)
# ---------------------------------------------------------------------------

step "Disabling Ctrl+Alt+Backspace (DontZap)"

XORG_CONF_DIR="/etc/X11/xorg.conf.d"
mkdir -p "${XORG_CONF_DIR}"

cat > "${XORG_CONF_DIR}/10-serverflags.conf" <<'EOF'
Section "ServerFlags"
    Option "DontZap" "true"
EndSection
EOF

info "DontZap configured → ${XORG_CONF_DIR}/10-serverflags.conf"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '\n'
printf '═%.0s' {1..60}
printf '\n'
printf ' GraceLab client v%s installed successfully\n' "$CLIENT_VERSION"
printf '═%.0s' {1..60}
printf '\n\n'

printf 'Config:        %s\n' "$CONFIG_FILE"
printf 'Client:        %s\n' "$CURRENT_LINK"
printf 'Logs:          %s\n' "$LOG_DIR"
printf 'Template home: %s\n' "$TEMPLATE_HOME"
printf '\n'
printf 'Next steps:\n'
printf '  1. Reboot — LightDM will autologin as "%s" and GraceLab should appear fullscreen.\n' "$GRACELAB_USER"
printf '  2. On first boot the station will show OFFLINE until the server is reachable.\n'
printf '  3. Verify DontZap took effect (Ctrl+Alt+Backspace should do nothing after reboot).\n'
printf '\n'
