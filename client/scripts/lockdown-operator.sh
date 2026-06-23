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
    -p /general/SaveOnExit -n -t bool -s false 2>/dev/null || true
xfconf-query -c xfce4-session \
    -p /general/AutoSave  -n -t bool -s false 2>/dev/null || true
# Clear any session cache written before this setting took effect.
rm -rf "${HOME}/.cache/sessions" 2>/dev/null || true

# ── Desktop menus ──────────────────────────────────────────────────────────
for prop in /desktop-menu/show /windowlist-menu/show; do
    xfconf-query -c xfce4-desktop -p "$prop" -n -t bool -s false 2>/dev/null || true
    xfconf-query -c xfce4-desktop -p "$prop"    -t bool -s false 2>/dev/null || true
done

# ── Keyboard shortcuts ─────────────────────────────────────────────────────
# Neutralise everything that can launch a shell, open a menu, or reach the
# desktop while GraceLab's fullscreen window is on top.
_noop() {
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/commands/custom/${1}" -n -t string -s '/bin/true' 2>/dev/null || true
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
_blank_wm() {
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/xfwm4/custom/${1}" -n -t string -s '' 2>/dev/null || true
    xfconf-query -c xfce4-keyboard-shortcuts \
        -p "/xfwm4/default/${1}" -n -t string -s '' 2>/dev/null || true
}

_blank_wm "<Primary><Alt>d"
_blank_wm "<Primary><Alt>Escape"
_blank_wm "<Primary><Alt>Tab"
_blank_wm "<Primary><Alt>n"
_blank_wm "<Primary><Alt>s"
_blank_wm "<Primary><Alt>Insert"
_blank_wm "<Alt>F11"
_blank_wm "<Alt>space"

# ── Panel ──────────────────────────────────────────────────────────────────
# Use pkill only — xfce4-panel --quit goes via D-Bus and pops an error dialog
# when the panel isn't running, which is exactly the state we want.
pkill -f xfce-superkey 2>/dev/null || true
pkill -x xfce4-panel   2>/dev/null || true

# ── Watchdog loop ──────────────────────────────────────────────────────────
# Re-kill any panel or menu helper that respawns (e.g. XFCE session manager
# restarting crashed components).
while true; do
    sleep 3
    pkill -x xfce4-panel   2>/dev/null || true
    pkill -f xfce-superkey 2>/dev/null || true
done
