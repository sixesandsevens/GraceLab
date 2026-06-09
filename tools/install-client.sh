#!/usr/bin/env bash
# GraceLab client installer
#
# Usage:
#   sudo ./tools/install-client.sh \
#     --server-url http://gracelab.local \
#     --hostname   gracelab-01 \
#     --token      PASTE_TOKEN_HERE
#
# What this script does:
#   - Creates gracelab and guestlab system users
#   - Installs client files to /opt/gracelab-client/releases/<version>
#   - Creates /opt/gracelab-client/current symlink
#   - Writes /etc/gracelab/client_config.ini
#   - Creates /opt/gracelab-client/template-home from /etc/skel
#   - Creates /var/log/gracelab
#   - Installs sudoers rules for lifecycle scripts
#   - Installs desktop autostart entry for gracelab user

set -euo pipefail

CLIENT_VERSION="0.2.0"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_CLIENT_DIR="${SCRIPT_DIR}/../client"

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

# Verify tkinter is available (required by the client)
python3 -c "import tkinter" 2>/dev/null || \
    warn "python3-tk may not be installed. Run: apt-get install python3-tk"

# Install rsync if missing
if ! command -v rsync >/dev/null 2>&1; then
    info "Installing rsync..."
    apt-get install -y rsync >/dev/null
fi

info "Pre-flight OK."

# ---------------------------------------------------------------------------
# Create users
# ---------------------------------------------------------------------------

step "Creating system users"

if ! id "$GRACELAB_USER" &>/dev/null; then
    useradd \
        --system \
        --create-home \
        --shell /bin/bash \
        --comment "GraceLab kiosk operator" \
        "$GRACELAB_USER"
    info "Created user: ${GRACELAB_USER}"
else
    info "User ${GRACELAB_USER} already exists — skipping."
fi

if ! id "$GUEST_USER" &>/dev/null; then
    useradd \
        --create-home \
        --shell /bin/bash \
        --comment "GraceLab guest session user" \
        "$GUEST_USER"
    # Lock the account by default; start_guest_session.sh unlocks it per-session
    passwd -l "$GUEST_USER" >/dev/null 2>&1 || true
    info "Created user: ${GUEST_USER} (account locked)"
else
    info "User ${GUEST_USER} already exists — skipping."
fi

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------

step "Creating directory layout"

mkdir -p "${INSTALL_BASE}/downloads"
mkdir -p "${RELEASE_DIR}/scripts"
mkdir -p "${RELEASE_DIR}/updater"
mkdir -p "$CONFIG_DIR"
mkdir -p "$LOG_DIR"

chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "$INSTALL_BASE"
chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "$LOG_DIR"

info "Directories OK."

# ---------------------------------------------------------------------------
# Install client files
# ---------------------------------------------------------------------------

step "Installing client files → ${RELEASE_DIR}"

[[ -f "${REPO_CLIENT_DIR}/gracelab_client.py" ]] || \
    die "Client source not found at ${REPO_CLIENT_DIR}. Run from the GraceLab repo."

cp "${REPO_CLIENT_DIR}/gracelab_client.py" "${RELEASE_DIR}/"
cp "${REPO_CLIENT_DIR}/client_config.ini.example" "${RELEASE_DIR}/"
cp "${REPO_CLIENT_DIR}/scripts/"*.sh "${RELEASE_DIR}/scripts/"
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
# Create template-home from /etc/skel
# ---------------------------------------------------------------------------

step "Creating guest template-home"

if [[ -d "$TEMPLATE_HOME" ]]; then
    info "Template-home already exists at ${TEMPLATE_HOME} — skipping."
else
    mkdir -p "$TEMPLATE_HOME"
    rsync -a /etc/skel/ "${TEMPLATE_HOME}/"
    chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "$TEMPLATE_HOME"
    info "Template-home created from /etc/skel."
    info "Customise ${TEMPLATE_HOME} with default browser bookmarks, wallpaper, etc."
fi

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
${GRACELAB_USER} ALL=(root) NOPASSWD: /usr/bin/pkill
${GRACELAB_USER} ALL=(root) NOPASSWD: /usr/sbin/rsync
${GRACELAB_USER} ALL=(root) NOPASSWD: /usr/bin/rsync
${GRACELAB_USER} ALL=(root) NOPASSWD: /usr/sbin/passwd
${GRACELAB_USER} ALL=(root) NOPASSWD: /usr/bin/loginctl
EOF

chmod 440 "$SUDOERS_FILE"

# Validate before leaving it in place
if ! visudo -c -f "$SUDOERS_FILE" >/dev/null 2>&1; then
    rm -f "$SUDOERS_FILE"
    die "sudoers syntax check failed — file removed. Check manually."
fi

info "Sudoers installed and validated."

# ---------------------------------------------------------------------------
# Desktop autostart
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

# Optional: also install a systemd user service as an alternative
SYSTEMD_USER_DIR="/home/${GRACELAB_USER}/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"

cat > "${SYSTEMD_USER_DIR}/gracelab-client.service" <<EOF
[Unit]
Description=GraceLab Kiosk Client
After=graphical-session.target

[Service]
WorkingDirectory=${CURRENT_LINK}
ExecStart=python3 ${CURRENT_LINK}/gracelab_client.py
Restart=always
RestartSec=3
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/${GRACELAB_USER}/.Xauthority

[Install]
WantedBy=default.target
EOF

chown -R "${GRACELAB_USER}:${GRACELAB_USER}" "$SYSTEMD_USER_DIR"
info "Systemd user service written (not yet enabled — see next steps)."

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
printf '  1. Configure %s autologin for user "%s" in your display manager.\n' "$(hostname)" "$GRACELAB_USER"
printf '  2. Verify template-home contents: ls %s\n' "$TEMPLATE_HOME"
printf '  3. Reboot and confirm GraceLab appears fullscreen.\n'
printf '  4. On first boot, the station will show OFFLINE until the server is reachable.\n'
printf '\n'
printf 'To enable the systemd service instead of desktop autostart:\n'
printf '  sudo loginctl enable-linger %s\n' "$GRACELAB_USER"
printf '  sudo -u %s systemctl --user enable gracelab-client.service\n' "$GRACELAB_USER"
printf '\n'
