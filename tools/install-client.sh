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
#   - Suppresses Linux Mint welcome screen for gracelab and guestlab
#   - Sets desktop background via set-wallpaper.sh autostart (detects monitor name at login)
#   - Writes Firefox enterprise policies: homepage locked to guestdesk.info, extensions blocked

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CLIENT_DIR="${SCRIPT_DIR}/../client"

CLIENT_VERSION="$(python3 -c "
import re, sys
m = re.search(r\"CLIENT_VERSION\s*=\s*['\\\"](.+?)['\\\"]\", open(sys.argv[1]).read())
print(m.group(1) if m else '')
" "${REPO_CLIENT_DIR}/gracelab_client.py" 2>/dev/null || true)"
[[ -n "$CLIENT_VERSION" ]] || { echo "ERROR: could not read CLIENT_VERSION from gracelab_client.py" >&2; exit 1; }
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
IPC_GROUP="gracelab-ipc"
IPC_DIR="/run/gracelab"

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

step "Configuring login and IPC groups"

if ! getent group nopasswdlogin >/dev/null 2>&1; then
    groupadd nopasswdlogin
    info "Created group: nopasswdlogin"
fi
if ! getent group "${IPC_GROUP}" >/dev/null 2>&1; then
    groupadd "${IPC_GROUP}"
    info "Created group: ${IPC_GROUP}"
fi
usermod -aG nopasswdlogin,"${IPC_GROUP}" "${GRACELAB_USER}"
usermod -aG nopasswdlogin,"${IPC_GROUP}" "${GUEST_USER}"
info "Added ${GRACELAB_USER} and ${GUEST_USER} to nopasswdlogin and ${IPC_GROUP}."

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

step "Creating directory layout"

mkdir -p "${INSTALL_BASE}/downloads"
mkdir -p "${IPC_DIR}"
mkdir -p "${RELEASE_DIR}/scripts"
mkdir -p "${RELEASE_DIR}/updater"
mkdir -p "$CONFIG_DIR"
mkdir -p "$LOG_DIR"
mkdir -p "/var/lib/gracelab-client"

# Root owns the entire installation tree so gracelab cannot modify scripts
# that are executed as root via sudoers (privilege escalation prevention).
# Only the downloads scratch dir and runtime state dir need to be gracelab-writable.
chown -R root:root "$INSTALL_BASE"
chown "${GRACELAB_USER}:${GRACELAB_USER}" "${INSTALL_BASE}/downloads"
chown "${GRACELAB_USER}:${GRACELAB_USER}" "/var/lib/gracelab-client"
chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "$LOG_DIR"
chown root:"${IPC_GROUP}" "${IPC_DIR}"
chmod 2775 "${IPC_DIR}"

info "Directories OK."

cat > /etc/tmpfiles.d/gracelab.conf <<EOF
d ${IPC_DIR} 2775 root ${IPC_GROUP} - -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/gracelab.conf 2>/dev/null || true
info "IPC directory configured → ${IPC_DIR}"

# ---------------------------------------------------------------------------
# Install client files
# ---------------------------------------------------------------------------

step "Installing client files → ${RELEASE_DIR}"

find "${REPO_CLIENT_DIR}" -maxdepth 1 -name "*.py" -exec cp {} "${RELEASE_DIR}/" \;
cp "${REPO_CLIENT_DIR}/client_config.ini.example" "${RELEASE_DIR}/"
rsync -a "${REPO_CLIENT_DIR}/scripts/" "${RELEASE_DIR}/scripts/"
rsync -a "${REPO_CLIENT_DIR}/updater/" "${RELEASE_DIR}/updater/"
chmod +x "${RELEASE_DIR}/scripts/"*.sh
chmod +x "${RELEASE_DIR}/updater/"*.sh 2>/dev/null || true

info "Client files installed."

# ---------------------------------------------------------------------------
# Install assets (background image, etc.)
# ---------------------------------------------------------------------------

step "Installing assets"

ASSETS_SRC="${REPO_CLIENT_DIR}/assets"
ASSETS_DST="${INSTALL_BASE}/assets"
mkdir -p "${ASSETS_DST}"

if [[ -d "$ASSETS_SRC" ]]; then
    rsync -a --exclude='.gitkeep' "${ASSETS_SRC}/" "${ASSETS_DST}/"
    chown -R root:root "${ASSETS_DST}"
    info "Assets installed → ${ASSETS_DST}"
else
    warn "No assets directory found at ${ASSETS_SRC} — background image will not be available."
fi

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
Exec=bash -c 'zenity --question --title="End Session" --text="End your session and return to the main screen?\n\nUnsaved files will be deleted." --ok-label="End Session" --cancel-label="Stay" --icon-name=system-log-out 2>/dev/null && touch /run/gracelab/guest-logout'
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

# Apply template to guestlab's home immediately so autostart overrides
# (e.g. magnus, mintwelcome) are in place before the very first login.
# Subsequent session resets are handled by reset_guest_home.sh.
GUEST_HOME="/home/${GUEST_USER}"
rm -rf "${GUEST_HOME}"
mkdir -p "${GUEST_HOME}"
rsync -a "${TEMPLATE_HOME}/" "${GUEST_HOME}/"
chown -R "${GUEST_USER}:${GUEST_USER}" "${GUEST_HOME}"
chmod 700 "${GUEST_HOME}"
info "Guest home initialized from template → ${GUEST_HOME}"

# ---------------------------------------------------------------------------
# Install updater do-install helper
# ---------------------------------------------------------------------------

step "Installing updater do-install helper"

UPDATER_DIR="${INSTALL_BASE}/updater"
mkdir -p "${UPDATER_DIR}"
cp "${REPO_CLIENT_DIR}/updater/do-install.sh" "${UPDATER_DIR}/do-install.sh"
chmod 755 "${UPDATER_DIR}/do-install.sh"
chown root:root "${UPDATER_DIR}/do-install.sh"
info "do-install.sh → ${UPDATER_DIR}/do-install.sh (root-owned)"

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
Defaults!${UPDATER_DIR}/do-install.sh                   !requiretty

${GRACELAB_USER} ALL=(root) NOPASSWD: ${CURRENT_LINK}/scripts/start_guest_session.sh
${GRACELAB_USER} ALL=(root) NOPASSWD: ${CURRENT_LINK}/scripts/end_guest_session.sh
${GRACELAB_USER} ALL=(root) NOPASSWD: ${CURRENT_LINK}/scripts/reset_guest_home.sh
${GRACELAB_USER} ALL=(root) NOPASSWD: ${UPDATER_DIR}/do-install.sh
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
Exec=${CURRENT_LINK}/scripts/run-client.sh
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
Hidden=false
NoDisplay=false
EOF

cat > "${AUTOSTART_DIR}/gracelab-updater.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=GraceLab Updater
Comment=GraceLab client auto-updater
Exec=python3 ${CURRENT_LINK}/updater/updater.py
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=30
Hidden=false
NoDisplay=false
EOF

# Suppress Mint welcome screen for gracelab
cat > "${AUTOSTART_DIR}/mintwelcome.desktop" <<'EOF'
[Desktop Entry]
Hidden=true
EOF

# Suppress system-wide magnus autostart for gracelab
cat > "${AUTOSTART_DIR}/magnus-autostart.desktop" <<'EOF'
[Desktop Entry]
Hidden=true
EOF

# Kill light-locker and disable X screensaver/DPMS at every login
cat > "${AUTOSTART_DIR}/gracelab-nodpms.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=GraceLab No-DPMS
Comment=Disable screen blanking and lock for kiosk session
Exec=bash -c 'pkill -f light-locker; pkill -f xscreensaver; pkill -f xfce4-screensaver; xset s off; xset s noblank; xset -dpms'
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
Hidden=false
NoDisplay=true
EOF

# Operator lockdown: kills xfce4-panel, xfce-superkey, disables session saving,
# and runs a watchdog that re-enforces all of the above in the background.
cat > "${AUTOSTART_DIR}/gracelab-lockdown-operator.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=GraceLab Operator Lockdown
Comment=Enforce kiosk restrictions for the gracelab operator account
Exec=${CURRENT_LINK}/scripts/lockdown-operator.sh
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=2
Hidden=false
NoDisplay=true
EOF

chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "/home/${GRACELAB_USER}/.config"
info "Autostart entries written (client wrapper + updater)."

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
      <property name="&lt;Primary&gt;&lt;Alt&gt;t"       type="string" value="/bin/true"/>
      <property name="&lt;Primary&gt;&lt;Shift&gt;Escape" type="string" value="/bin/true"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Delete"   type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;l"                     type="string" value="/bin/true"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;l"        type="string" value="/bin/true"/>
      <property name="XF86ScreenSaver"                    type="string" value="/bin/true"/>
      <property name="Super_L"                            type="string" value="/bin/true"/>
      <property name="Super_R"                            type="string" value="/bin/true"/>
      <property name="&lt;Alt&gt;F1"                      type="string" value="/bin/true"/>
      <property name="&lt;Alt&gt;F2"                      type="string" value="/bin/true"/>
      <property name="&lt;Alt&gt;F3"                      type="string" value="/bin/true"/>
      <property name="&lt;Primary&gt;Escape"              type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;e"                     type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;p"                     type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;r"                     type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;s"                     type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;t"                     type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;d"                     type="string" value="/bin/true"/>
      <property name="&lt;Super&gt;f"                     type="string" value="/bin/true"/>
      <!-- xfconf stores the Ctrl modifier as <Primary>, not <Control> — an
           earlier fix used the wrong modifier name and never took effect. -->
      <property name="&lt;Primary&gt;&lt;Alt&gt;f"        type="string" value="/bin/true"/>
    </property>
  </property>
  <property name="xfwm4" type="empty">
    <property name="default" type="empty">
      <property name="&lt;Primary&gt;&lt;Alt&gt;Left"    type="string" value=""/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Right"   type="string" value=""/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Up"      type="string" value=""/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Down"    type="string" value=""/>
      <property name="&lt;Primary&gt;F1"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F2"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F3"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F4"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F5"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F6"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F7"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F8"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F9"                 type="string" value=""/>
      <property name="&lt;Primary&gt;F10"                type="string" value=""/>
      <property name="&lt;Primary&gt;F11"                type="string" value=""/>
      <property name="&lt;Primary&gt;F12"                type="string" value=""/>
      <property name="&lt;Shift&gt;&lt;Alt&gt;Page_Down" type="string" value=""/>
      <property name="&lt;Shift&gt;&lt;Alt&gt;Page_Up"   type="string" value=""/>
      <!-- Confirmed live binding via xfconf-query -lv on station:
           /xfwm4/default/<Primary><Alt>d = show_desktop_key.
           An empty value does NOT unbind an xfwm4 action key — xfwm4 keeps
           acting on it. Remapping to "cancel_key" (a no-op in this kiosk
           session) is the only value that reliably neutralises these. -->
      <property name="&lt;Primary&gt;&lt;Alt&gt;d"      type="string" value="cancel_key"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Escape" type="string" value="cancel_key"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Tab"    type="string" value="cancel_key"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;n"      type="string" value="cancel_key"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;s"      type="string" value="cancel_key"/>
      <property name="&lt;Primary&gt;&lt;Alt&gt;Insert" type="string" value="cancel_key"/>
      <property name="&lt;Alt&gt;F11"                  type="string" value="cancel_key"/>
      <property name="&lt;Alt&gt;space"                type="string" value="cancel_key"/>
    </property>
  </property>
</channel>
XMLEOF

# Disable xfce4-screensaver lock
cat > "${GRACELAB_XFCONF_DIR}/xfce4-screensaver.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-screensaver" version="1.0">
  <property name="saver" type="empty">
    <property name="enabled" type="bool" value="false"/>
    <property name="mode" type="int" value="0"/>
  </property>
  <property name="lock" type="empty">
    <property name="enabled" type="bool" value="false"/>
    <property name="snoopy" type="bool" value="false"/>
  </property>
</channel>
XMLEOF

# Disable xfce4-power-manager display sleep and lock-on-suspend
cat > "${GRACELAB_XFCONF_DIR}/xfce4-power-manager.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-power-manager" version="1.0">
  <property name="xfce4-power-manager" type="empty">
    <property name="dpms-enabled" type="bool" value="false"/>
    <property name="blank-on-ac" type="int" value="0"/>
    <property name="dpms-on-ac-sleep" type="uint" value="0"/>
    <property name="dpms-on-ac-off" type="uint" value="0"/>
    <property name="lock-screen-suspend-hibernate" type="bool" value="false"/>
    <property name="logind-handle-lid-switch" type="bool" value="false"/>
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

# Disable XFCE session saving so a crashed/regenerated panel can never be
# restored on the next login.
cat > "${GRACELAB_XFCONF_DIR}/xfce4-session.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-session" version="1.0">
  <property name="general" type="empty">
    <property name="SaveOnExit" type="bool" value="false"/>
    <property name="AutoSave"   type="bool" value="false"/>
  </property>
</channel>
XMLEOF

# Clear any previously-saved XFCE session (panel, apps, etc.)
rm -rf "/home/${GRACELAB_USER}/.cache/sessions"
mkdir -p "/home/${GRACELAB_USER}/.cache"

chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "/home/${GRACELAB_USER}/.config"
chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "/home/${GRACELAB_USER}/.cache"
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

# Configure slick-greeter background to match the desktop wallpaper
SLICK_CONF="/etc/lightdm/slick-greeter.conf"
GREETER_BG="${INSTALL_BASE}/assets/GraceDesktopBackground.png"
cat > "${SLICK_CONF}" <<EOF
[Greeter]
background=${GREETER_BG}
draw-user-backgrounds=false
EOF
info "slick-greeter background configured → ${SLICK_CONF}"

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
# Firefox policies (homepage + block extension installs)
# ---------------------------------------------------------------------------

step "Configuring Firefox policies"

# Build the policy JSON once and write it to every location Firefox checks.
# On Linux Mint, the browser reads the distribution/ directory inside its own
# installation tree first; /etc/firefox/policies is a fallback. We write both
# so the policy applies regardless of which Firefox package variant is installed.

_FIREFOX_POLICY_JSON='
{
  "policies": {
    "Homepage": {
      "URL": "https://guestdesk.info",
      "Locked": true,
      "StartPage": "homepage"
    },
    "OverrideFirstRunPage": "",
    "OverridePostUpdatePage": "",
    "DontCheckDefaultBrowser": true,
    "DisableProfileImport": true,
    "DisableFirefoxAccounts": true,
    "DisableFirefoxStudies": true,
    "DisableTelemetry": true,
    "DisablePocket": true,
    "NoDefaultBookmarks": true,
    "BlockAboutAddons": true,
    "ExtensionSettings": {
      "*": {
        "installation_mode": "blocked",
        "blocked_install_message": "Extensions cannot be installed on this device."
      }
    },
    "FirefoxHome": {
      "Search": false,
      "TopSites": false,
      "SponsoredTopSites": false,
      "Highlights": false,
      "Pocket": false,
      "SponsoredPocket": false,
      "Snippets": false,
      "Locked": true
    },
    "UserMessaging": {
      "ExtensionRecommendations": false,
      "FeatureRecommendations": false,
      "UrlbarInterventions": false,
      "SkipOnboarding": true,
      "MoreFromMozilla": false,
      "Locked": true
    },
    "Preferences": {
      "browser.startup.homepage": {
        "Value": "https://guestdesk.info",
        "Status": "locked"
      },
      "browser.aboutwelcome.enabled": {
        "Value": false,
        "Status": "locked"
      },
      "browser.startup.homepage_override.mstone": {
        "Value": "ignore",
        "Status": "locked"
      },
      "browser.shell.checkDefaultBrowser": {
        "Value": false,
        "Status": "locked"
      },
      "browser.shell.didSkipDefaultBrowserCheckOnFirstRun": {
        "Value": true,
        "Status": "locked"
      },
      "datareporting.policy.dataSubmissionPolicyBypassNotification": {
        "Value": true,
        "Status": "locked"
      },
      "browser.sessionstore.resume_from_crash": {
        "Value": false,
        "Status": "locked"
      }
    }
  }
}
'

# Validate JSON before writing anywhere
echo "$_FIREFOX_POLICY_JSON" | python3 -m json.tool >/dev/null || \
    die "Firefox policies.json failed JSON validation — check syntax."

# /etc/firefox/policies (standard Linux path)
mkdir -p /etc/firefox/policies
echo "$_FIREFOX_POLICY_JSON" > /etc/firefox/policies/policies.json
chmod 644 /etc/firefox/policies/policies.json
info "Firefox policy written → /etc/firefox/policies/policies.json"

# Firefox distribution/ directory (Mint reads this first; path varies by install)
for _FF_CANDIDATE in \
    /usr/lib/firefox \
    /usr/lib/firefox-esr \
    /usr/lib/x86_64-linux-gnu/firefox \
    /opt/firefox \
    "$(dirname "$(readlink -f "$(command -v firefox 2>/dev/null || true)")" 2>/dev/null || true)"
do
    [[ -d "$_FF_CANDIDATE" ]] || continue
    mkdir -p "${_FF_CANDIDATE}/distribution"
    echo "$_FIREFOX_POLICY_JSON" > "${_FF_CANDIDATE}/distribution/policies.json"
    chmod 644 "${_FF_CANDIDATE}/distribution/policies.json"
    info "Firefox policy written → ${_FF_CANDIDATE}/distribution/policies.json"
done

unset _FIREFOX_POLICY_JSON _FF_CANDIDATE

# ---------------------------------------------------------------------------
# Guest session hardening
# ---------------------------------------------------------------------------

step "Hardening guest session"

# ── Thunar: disable built-in terminal and clear custom actions ────────────
GUEST_THUNAR_DIR="/home/${GUEST_USER}/.config/Thunar"
mkdir -p "$GUEST_THUNAR_DIR"

# thunarrc: MiscExecShell= (empty) removes "Open Terminal Here" from context menu
cat > "${GUEST_THUNAR_DIR}/thunarrc" <<'EOF'
[Configuration]
LastShowHidden=FALSE
MiscExecShell=
MiscOpenNewWindowAsRoot=FALSE
MiscShowDeleteAction=FALSE
MiscSingleClick=FALSE
EOF

# uca.xml: empty custom actions (belt-and-suspenders)
cat > "${GUEST_THUNAR_DIR}/uca.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<actions>
</actions>
EOF

chown -R "${GUEST_USER}:${GUEST_USER}" "$GUEST_THUNAR_DIR"
info "Thunar terminal access disabled (thunarrc + uca.xml)."

# ── Block terminal emulators for guestlab via wrapper scripts ─────────────
# Each wrapper prints a message and exits so the binary is effectively
# inaccessible even if the .desktop file or keyboard shortcut fires.
BLOCK_DIR="/usr/local/lib/gracelab/block-terminals"
mkdir -p "$BLOCK_DIR"

cat > "${BLOCK_DIR}/blocked-terminal" <<'EOF'
#!/usr/bin/env bash
# Terminal access is disabled on this computer.
exit 1
EOF
chmod 755 "${BLOCK_DIR}/blocked-terminal"

# Shadow each known terminal emulator for guestlab only via ~/.local/bin
GUEST_LOCAL_BIN="/home/${GUEST_USER}/.local/bin"
mkdir -p "$GUEST_LOCAL_BIN"

for term in \
    xterm xfce4-terminal xfce4-console gnome-terminal \
    konsole lxterminal rxvt urxvt kitty alacritty tilix \
    xdg-terminal sensible-terminal
do
    ln -sf "${BLOCK_DIR}/blocked-terminal" "${GUEST_LOCAL_BIN}/${term}"
done

# Ensure ~/.local/bin is first in PATH for guestlab
GUEST_BASHRC="/home/${GUEST_USER}/.bashrc"
if ! grep -q "gracelab-terminal-block" "$GUEST_BASHRC" 2>/dev/null; then
    cat >> "$GUEST_BASHRC" <<'EOF'

# GraceLab: terminal access disabled
export PATH="$HOME/.local/bin:$PATH"
EOF
fi

chown -R "${GUEST_USER}:${GUEST_USER}" "$GUEST_LOCAL_BIN" "$GUEST_BASHRC" 2>/dev/null || true
info "Terminal emulators blocked for ${GUEST_USER}."

info "Guest session hardening complete."

# ---------------------------------------------------------------------------
# Suppress Linux Mint Update Manager system-wide
# ---------------------------------------------------------------------------

step "Suppressing Mint Update Manager"

# Disable apt periodic background checks — these are what wake mintupdate.
cat > /etc/apt/apt.conf.d/99gracelab-no-updates <<'EOF'
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
APT::Periodic::Unattended-Upgrade "0";
EOF
info "apt periodic checks disabled → /etc/apt/apt.conf.d/99gracelab-no-updates"

# Mask the mintupdate systemd user service so it never auto-starts.
# (mintupdate ships a user-level .service on newer Mint releases)
systemctl --global disable mintupdate.service 2>/dev/null || true
systemctl --global mask   mintupdate.service 2>/dev/null || true

# Disable the system-wide XDG autostart entries for all Mint update notifiers.
# We do this at the system level (/etc/xdg/autostart) so it applies to both
# gracelab and guestlab without needing per-user overrides.
for entry in \
    mintupdate.desktop \
    mintupdate-launcher.desktop \
    com.linuxmint.updates.desktop \
    update-notifier.desktop \
    update-manager.desktop \
    org.gnome.Software.desktop
do
    SYSTEM_FILE="/etc/xdg/autostart/${entry}"
    if [[ -f "$SYSTEM_FILE" ]]; then
        # Append X-GNOME-Autostart-enabled=false rather than deleting the file
        # so package upgrades don't silently recreate it unchecked.
        if ! grep -q "X-GNOME-Autostart-enabled=false" "$SYSTEM_FILE"; then
            echo "X-GNOME-Autostart-enabled=false" >> "$SYSTEM_FILE"
            info "Disabled system autostart: ${entry}"
        else
            info "Already disabled: ${entry}"
        fi
    fi
done

# Also place user-level Hidden=true overrides for gracelab so the above can't
# be undone by a package reinstall restoring the system file.
for entry in \
    mintupdate.desktop \
    mintupdate-launcher.desktop \
    com.linuxmint.updates.desktop \
    update-notifier.desktop \
    update-manager.desktop \
    org.gnome.Software.desktop
do
    cat > "/home/${GRACELAB_USER}/.config/autostart/${entry}" <<'DEOF'
[Desktop Entry]
Hidden=true
DEOF
done
chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "/home/${GRACELAB_USER}/.config/autostart"
info "User-level autostart overrides written for ${GRACELAB_USER}."

info "Mint Update Manager suppressed."

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
