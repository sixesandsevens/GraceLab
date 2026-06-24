#!/usr/bin/env bash
# GraceLab operator lockdown — runs as the gracelab user at every XFCE login.
#
# Disables the XFCE panel, neutralises dangerous keyboard shortcuts, disables
# session saving, and then loops as a watchdog to re-enforce all of the above
# in case XFCE regenerates the panel or restores a saved session.

set -euo pipefail

# Wait for xfconfd to be ready (it may not exist yet at login time).
for i in $(seq 1 20); do
    pgrep -x xfconfd >/dev/null 2>&1 && break
    sleep 0.5
done

# ── Session saving ─────────────────────────────────────────────────────────
# Prevent XFCE from ever saving a session that could restore the panel.
xfconf-query -c xfce4-session \
    -p /general/SaveOnExit -t bool -s false 2>/dev/null || \
xfconf-query -c xfce4-session \
    -p /general/SaveOnExit -n -t bool -s false 2>/dev/null || true
xfconf-query -c xfce4-session \
    -p /general/AutoSave -t bool -s false 2>/dev/null || \
xfconf-query -c xfce4-session \
    -p /general/AutoSave -n -t bool -s false 2>/dev/null || true
# Clear any session cache written before this setting took effect.
rm -rf "${HOME}/.cache/sessions" 2>/dev/null || true

# ── Desktop menus ──────────────────────────────────────────────────────────
# desktop-menu/show=false alone does not reliably stop the right-click menu —
# xfdesktop appears to cache this at its own startup the same way xfwm4 cached
# keybindings (confirmed on station, June 2026: Ctrl+Alt+D reaches a desktop
# where right-click still opens a menu with terminal access, even with this
# property set to false). Killing xfdesktop outright is the only fix that
# actually removes the right-click target — with no process drawing the
# desktop, there is nothing to right-click on, regardless of which key
# combination got you there or what xfconf says.
for prop in /desktop-menu/show /windowlist-menu/show; do
    xfconf-query -c xfce4-desktop -p "$prop" -n -t bool -s false 2>/dev/null || true
    xfconf-query -c xfce4-desktop -p "$prop"    -t bool -s false 2>/dev/null || true
done
pkill -x xfdesktop 2>/dev/null || true

# ── Workspaces ─────────────────────────────────────────────────────────────
# Mint ships 4 workspaces by default. Ctrl+F2/F3/F4 and other workspace-
# switch keys jump to an empty workspace, which looks just like "the
# desktop" and is just as much an escape as show_desktop_key — confirmed on
# station (June 2026). Collapsing to a single workspace makes every
# workspace-switch shortcut a permanent no-op regardless of which exact key
# combination Mint or XFCE binds it to, without having to enumerate them all.
xfconf-query -c xfwm4 -p /general/workspace_count -t int -s 1 2>/dev/null || \
xfconf-query -c xfwm4 -p /general/workspace_count -n -t int -s 1 2>/dev/null || true

# ── Keyboard shortcuts ─────────────────────────────────────────────────────
# Neutralise everything that can launch a shell, open a menu, or reach the
# desktop while GraceLab's fullscreen window is on top.
#
# IMPORTANT: -n only creates a NEW property. If the property already exists
# (the common case — Mint ships these with real bindings out of the box),
# -n fails immediately and falls through to `|| true`, silently doing
# nothing. This previously let <Alt>F3 (xfce4-appfinder) and <Primary><Alt>f
# (thunar) survive every lockdown pass even though they were "handled."
# Always try the plain overwrite first; only use -n as a fallback for
# properties that genuinely don't exist yet.
_noop() {
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/commands/custom/${1}" -t string -s '/bin/true' 2>/dev/null || \
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/commands/custom/${1}" -n -t string -s '/bin/true' 2>/dev/null || true
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/commands/default/${1}" -t string -s '/bin/true' 2>/dev/null || \
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/commands/default/${1}" -n -t string -s '/bin/true' 2>/dev/null || true
}

# Bare Windows key — the bypass found in June 2026
_noop "Super_L"
_noop "Super_R"

# Whisker menu / application finders
_noop "<Alt>F1"
_noop "<Alt>F2"
_noop "<Primary>Escape"
_noop "<Super>r"
_noop "<Super>s"

# File manager / app shortcuts
_noop "<Super>e"
_noop "<Super>p"
_noop "<Super>t"
_noop "<Super>d"
_noop "<Super>f"
_noop "<Primary><Alt>f"
_noop "<Alt>F3"

# Terminal shortcuts
_noop "<Primary><Alt>t"
_noop "<Primary><Shift>Escape"
_noop "<Primary><Alt>Delete"

# Lock shortcuts (just redirect them to /bin/true rather than a lock screen)
_noop "<Super>l"
_noop "<Primary><Alt>l"
_noop "XF86ScreenSaver"

# ── xfwm4 window-manager shortcuts ──────────────────────────────────────────
# Show-desktop / fullscreen toggles found exposing right-click desktop and
# Thunar access through Ctrl+Alt+D / Ctrl+Alt+F (June 2026).
# NOTE: xfconf stores the Ctrl modifier as "<Primary>", not "<Control>" — an
# earlier fix used the wrong modifier name and silently created unused dead
# properties instead of overriding the live ones. Verified against the actual
# `xfconf-query -lv` dump from a station: show_desktop_key is bound under
# /xfwm4/.../<Primary><Alt>d.
#
# Setting these to an empty string did NOT disable show_desktop_key in
# practice (confirmed on station after 0.3.18) — xfwm4 keeps acting on the
# binding even with an empty value. Resetting (-r) falls back to xfwm4's
# compiled-in default, which re-enables the same action. The only reliable
# fix is to rebind the key combo to "cancel_key" — xfwm4's escape-current-
# operation action. It is a real, valid xfwm4 action so xfconf accepts it,
# and it is a no-op whenever there is no drag/resize/move in progress (i.e.
# always, on the kiosk screen).
_neutralize_wm() {
    # Try to set as if the property already exists first (the common case —
    # Mint ships these with real bindings out of the box). Fall back to -n
    # (create new) only if that fails, e.g. on a station where the binding
    # was never present at all.
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/xfwm4/custom/${1}" -t string -s 'cancel_key' 2>/dev/null || \
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/xfwm4/custom/${1}" -n -t string -s 'cancel_key' 2>/dev/null || true
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/xfwm4/default/${1}" -t string -s 'cancel_key' 2>/dev/null || \
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/xfwm4/default/${1}" -n -t string -s 'cancel_key' 2>/dev/null || true
}

_neutralize_wm "<Primary><Alt>d"
_neutralize_wm "<Primary><Alt>Escape"
_neutralize_wm "<Primary><Alt>Tab"
_neutralize_wm "<Primary><Alt>n"
_neutralize_wm "<Primary><Alt>s"
_neutralize_wm "<Primary><Alt>Insert"
_neutralize_wm "<Alt>F11"
_neutralize_wm "<Alt>space"

# xfwm4 caches keybindings at its own startup and does not pick up xfconf
# changes made after the fact — confirmed on station: the xfconf value was
# correctly set to "cancel_key" but Ctrl+Alt+D kept showing the desktop until
# xfwm4 was restarted. Since this script runs after xfwm4 has already
# started (it's an autostart entry, same as xfwm4 itself), we must force a
# reload every time so our overrides actually take effect.
xfwm4 --replace >/dev/null 2>&1 &
disown

# ── Panel ──────────────────────────────────────────────────────────────────
# Use pkill only — xfce4-panel --quit goes via D-Bus and pops an error dialog
# when the panel isn't running, which is exactly the state we want.
pkill -f xfce-superkey 2>/dev/null || true
pkill -x xfce4-panel   2>/dev/null || true

# ── Watchdog loop ──────────────────────────────────────────────────────────
# Re-kill any panel, desktop, or menu helper that respawns (e.g. XFCE session
# manager restarting crashed components).
while true; do
    sleep 3
    pkill -x xfce4-panel   2>/dev/null || true
    pkill -x xfdesktop      2>/dev/null || true
    pkill -f xfce-superkey 2>/dev/null || true
done
