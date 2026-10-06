#!/usr/bin/env bash
# GraceLab client installation helper — executed as root via sudoers.
# Called by updater.py after a verified download.
#
# Usage: do-install.sh <version> <tarball_path>
#
# Security:
#   - version must match semver pattern
#   - tarball must be inside /opt/gracelab-client/downloads/
#   - path traversal prevented by realpath check

set -euo pipefail

INSTALL_BASE="/opt/gracelab-client"
DOWNLOADS_DIR="${INSTALL_BASE}/downloads"
STATE_DIR="/var/lib/gracelab-client"

VERSION="${1:-}"
TARBALL="${2:-}"

# ---------------------------------------------------------------------------
# Validate arguments
# ---------------------------------------------------------------------------

if [[ -z "$VERSION" || -z "$TARBALL" ]]; then
    echo "Usage: do-install.sh <version> <tarball_path>" >&2
    exit 1
fi

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+[-a-z0-9.]*$ ]]; then
    echo "Invalid version format: $VERSION" >&2
    exit 1
fi

REAL_TARBALL="$(realpath "$TARBALL" 2>/dev/null || true)"
REAL_DOWNLOADS="$(realpath "$DOWNLOADS_DIR")"

if [[ -z "$REAL_TARBALL" || "$REAL_TARBALL" != "${REAL_DOWNLOADS}/"* ]]; then
    echo "Tarball must be inside ${DOWNLOADS_DIR}." >&2
    exit 1
fi

if [[ ! -f "$REAL_TARBALL" ]]; then
    echo "Tarball not found: $REAL_TARBALL" >&2
    exit 1
fi

# Filename must match the version we were given (prevents mismatched installs)
EXPECTED_BASENAME="gracelab-client-${VERSION}.tar.gz"
if [[ "$(basename "$REAL_TARBALL")" != "$EXPECTED_BASENAME" ]]; then
    echo "Tarball filename does not match version: expected ${EXPECTED_BASENAME}" >&2
    exit 1
fi

# Inspect tarball for path traversal or absolute paths before extracting
UNSAFE="$(tar -tzf "$REAL_TARBALL" 2>/dev/null | grep -E '(^/|(^|/)\.\.(/|$))' || true)"
if [[ -n "$UNSAFE" ]]; then
    echo "Tarball contains unsafe paths — aborting:" >&2
    echo "$UNSAFE" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Stage the release, then switch to it
#
# Power-loss safety: `current` must always point at a complete, on-disk
# release. Guests do power stations off mid-update (gracelab-03, 2026-10-06:
# a reinstall deleted and re-extracted the live release dir, the power cut
# left ~30 zero-byte files, and the station could not start the kiosk or the
# updater again). So the release is extracted into a staging dir that nothing
# points at, flushed to disk, and only then renamed into place and swapped
# in. The release `current` points at is never modified: reinstalling the
# running version goes to a fresh "<version>.reinstall-<time>" dir instead.
# ---------------------------------------------------------------------------

RELEASES_DIR="${INSTALL_BASE}/releases"
CURRENT_LINK="${INSTALL_BASE}/current"
TMP_LINK="${CURRENT_LINK}.new"
STAGING_DIR="${RELEASES_DIR}/.staging-${VERSION}"
LIVE_DIR="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"

RELEASE_DIR="${RELEASES_DIR}/${VERSION}"
if [[ "$LIVE_DIR" == "$RELEASE_DIR" ]]; then
    RELEASE_DIR="${RELEASES_DIR}/${VERSION}.reinstall-$(date +%Y%m%d%H%M%S)"
fi

mkdir -p "$RELEASES_DIR"
# Leftovers from an interrupted install were never pointed at; drop them.
rm -rf "${RELEASES_DIR}"/.staging-*
mkdir -p "$STAGING_DIR"

tar -xzf "$REAL_TARBALL" -C "$STAGING_DIR"

# Root owns everything; everything is readable by the kiosk user (0.4.9
# shipped 0600 files) and scripts are executable.
chown -R root:root "$STAGING_DIR"
chmod -R u+rwX,go+rX,go-w "$STAGING_DIR"
find "${STAGING_DIR}/scripts" -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true
find "${STAGING_DIR}/updater" -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true

[[ -s "${STAGING_DIR}/gracelab_client.py" ]] \
    || { echo "Extracted release has no gracelab_client.py — aborting." >&2; exit 1; }

sync

# RELEASE_DIR is not live here (see above), so replacing it is safe.
rm -rf "$RELEASE_DIR"
mv -T "$STAGING_DIR" "$RELEASE_DIR"
sync

# Atomic symlink swap
ln -sfn "$RELEASE_DIR" "$TMP_LINK"
mv -Tf "$TMP_LINK" "$CURRENT_LINK"
sync

# Drop other copies of this version (e.g. the release we just reinstalled
# over), now that nothing points at them. Other versions are kept for rollback.
for old in "${RELEASES_DIR}/${VERSION}" "${RELEASES_DIR}/${VERSION}".reinstall-*; do
    if [[ -e "$old" && "$old" != "$RELEASE_DIR" ]]; then
        rm -rf "$old"
    fi
done

# ---------------------------------------------------------------------------
# Sync shared data from this release
# (assets and template-home live outside the versioned release dir so they
#  are reachable at a stable path regardless of which release is current)
# ---------------------------------------------------------------------------

# Assets: wallpaper, icons, etc.
ASSETS_SRC="${RELEASE_DIR}/assets"
ASSETS_DST="${INSTALL_BASE}/assets"
if [[ -d "$ASSETS_SRC" ]]; then
    mkdir -p "$ASSETS_DST"
    rsync -a --delete "$ASSETS_SRC/" "$ASSETS_DST/"
    chown -R root:root "$ASSETS_DST"
    echo "Assets updated → ${ASSETS_DST}"
fi

# Guest desktop template: only update the source here.
# reset_guest_home.sh applies it to /home/guestlab at the next session reset
# so we never touch a live guest session.
TEMPLATE_SRC="${RELEASE_DIR}/template-home"
TEMPLATE_DST="${INSTALL_BASE}/template-home"
if [[ -d "$TEMPLATE_SRC" ]]; then
    mkdir -p "$TEMPLATE_DST"
    rsync -a --delete "$TEMPLATE_SRC/" "$TEMPLATE_DST/"
    chown -R root:root "$TEMPLATE_DST"
    echo "template-home updated → ${TEMPLATE_DST}"
fi

# ---------------------------------------------------------------------------
# System config that requires root (idempotent, safe to rewrite on every update)
# ---------------------------------------------------------------------------

# slick-greeter background — keep in sync with the desktop wallpaper
SLICK_CONF="/etc/lightdm/slick-greeter.conf"
GREETER_BG="${INSTALL_BASE}/assets/GraceDesktopBackground.png"
if [[ -f "$GREETER_BG" ]]; then
    cat > "${SLICK_CONF}" <<EOF
[Greeter]
background=${GREETER_BG}
draw-user-backgrounds=false
EOF
    echo "slick-greeter background updated → ${SLICK_CONF}"
fi

# NetworkManager polkit lockdown for gracelab/guestlab (non-fatal: the
# lifecycle hooks re-assert it at every session start/reset anyway)
"${RELEASE_DIR}/scripts/install-network-lockdown.sh" \
    || echo "WARN: network lockdown failed" >&2

# Ensure runtime state directory exists and is gracelab-writable
GRACELAB_USER="gracelab"
if id "$GRACELAB_USER" &>/dev/null; then
    mkdir -p "$STATE_DIR"
    chown "${GRACELAB_USER}:${GRACELAB_USER}" "$STATE_DIR"
fi

# ---------------------------------------------------------------------------
# Re-apply gracelab operator lockdown on every update
# (keyboard shortcuts, session-save disable, autostart watchdog)
# ---------------------------------------------------------------------------

GRACELAB_HOME="/home/${GRACELAB_USER}"
GRACELAB_XFCONF="${GRACELAB_HOME}/.config/xfce4/xfconf/xfce-perchannel-xml"

if id "$GRACELAB_USER" &>/dev/null && [[ -d "$GRACELAB_HOME" ]]; then
    mkdir -p "$GRACELAB_XFCONF"

    cat > "${GRACELAB_XFCONF}/xfce4-keyboard-shortcuts.xml" <<'XMLEOF'
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
      <!-- xfconf stores the Ctrl modifier as <Primary>, not <Control>. -->
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
      <!-- Confirmed live binding: /xfwm4/default/<Primary><Alt>d = show_desktop_key.
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

    cat > "${GRACELAB_XFCONF}/xfce4-session.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfce4-session" version="1.0">
  <property name="general" type="empty">
    <property name="SaveOnExit" type="bool" value="false"/>
    <property name="AutoSave"   type="bool" value="false"/>
  </property>
</channel>
XMLEOF

    # Collapse to a single workspace — see lockdown-operator.sh comment for
    # why empty/blanked workspace-switch shortcuts aren't sufficient on their
    # own (confirmed on station, June 2026).
    cat > "${GRACELAB_XFCONF}/xfwm4.xml" <<'XMLEOF'
<?xml version="1.0" encoding="UTF-8"?>
<channel name="xfwm4" version="1.0">
  <property name="general" type="empty">
    <property name="workspace_count" type="int" value="1"/>
  </property>
</channel>
XMLEOF

    # Clear any previously-saved XFCE session
    rm -rf "${GRACELAB_HOME}/.cache/sessions"
    mkdir -p "${GRACELAB_HOME}/.cache"

    # Update the lockdown-operator autostart to point to the new current link
    GRACELAB_AUTOSTART="${GRACELAB_HOME}/.config/autostart"
    mkdir -p "$GRACELAB_AUTOSTART"
    cat > "${GRACELAB_AUTOSTART}/gracelab-lockdown-operator.desktop" <<EOF
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

    chown -R "${GRACELAB_USER}:${GRACELAB_USER}" \
        "${GRACELAB_HOME}/.config" \
        "${GRACELAB_HOME}/.cache"

    echo "gracelab operator lockdown updated."
fi

# ---------------------------------------------------------------------------
# Self-update: replace this helper with the release's copy (atomic rename, so
# it's safe while this very script is still running). Last on purpose — the
# new helper only takes effect from the next update.
# ---------------------------------------------------------------------------

"${RELEASE_DIR}/scripts/sync-updater-helper.sh" \
    || echo "WARN: updater helper self-update failed" >&2

echo "Installed gracelab-client ${VERSION} → ${RELEASE_DIR}"
