#!/usr/bin/env bash
# Disable all screensaver, screen lock, and DPMS for the kiosk session.
# Run on every client start so it survives updates and reboots.
#
# Covers:
#   - X11 DPMS and screen blanking (xset)
#   - light-locker (LightDM's screen locker)
#   - xfce4-screensaver
#   - xscreensaver
#   - xfce4-power-manager display sleep and lock-on-suspend (xfconf-query)

set -uo pipefail

log() { echo "$(date -Is) [disable-screensaver] $*"; }

# ── 1. X11 blanking and DPMS ─────────────────────────────────────────────────
if command -v xset >/dev/null 2>&1; then
    xset s off        # disable screensaver timer
    xset s noblank    # prevent screen from blanking
    xset -dpms        # disable DPMS (Energy Star) power-saving
    log "xset: screensaver and DPMS disabled."
else
    log "xset not found — skipping."
fi

# ── 2. Kill screen lockers ────────────────────────────────────────────────────
for proc in light-locker xfce4-screensaver xscreensaver; do
    if pgrep -x "$proc" >/dev/null 2>&1; then
        pkill -x "$proc" || true
        log "Killed $proc."
    fi
done

# ── 3. xfconf-query: disable xfce4-screensaver lock ─────────────────────────
if command -v xfconf-query >/dev/null 2>&1; then
    xfconf-query -c xfce4-screensaver -p /saver/enabled    -n -t bool -s false 2>/dev/null || \
    xfconf-query -c xfce4-screensaver -p /saver/enabled       -t bool -s false 2>/dev/null || true
    xfconf-query -c xfce4-screensaver -p /lock/enabled     -n -t bool -s false 2>/dev/null || \
    xfconf-query -c xfce4-screensaver -p /lock/enabled        -t bool -s false 2>/dev/null || true
    log "xfconf: xfce4-screensaver disabled."

    # ── 4. xfconf-query: disable xfce4-power-manager display sleep ───────────
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled \
        -n -t bool -s false 2>/dev/null || \
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/dpms-enabled \
        -t bool -s false 2>/dev/null || true
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/blank-on-ac \
        -n -t int -s 0 2>/dev/null || \
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/blank-on-ac \
        -t int -s 0 2>/dev/null || true
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/lock-screen-suspend-hibernate \
        -n -t bool -s false 2>/dev/null || \
    xfconf-query -c xfce4-power-manager -p /xfce4-power-manager/lock-screen-suspend-hibernate \
        -t bool -s false 2>/dev/null || true
    log "xfconf: xfce4-power-manager display sleep disabled."
else
    log "xfconf-query not found — skipping xfconf settings."
fi

log "Done."
