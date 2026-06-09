#!/usr/bin/env bash
# Sets the GraceLab desktop background for all monitors detected by xrandr.
# Runs at guestlab login via autostart so it works regardless of which
# display connector (HDMI, DP, VGA) the machine uses.

WALLPAPER="/opt/gracelab-client/assets/GraceDesktopBackground.png"
[ -f "$WALLPAPER" ] || exit 0

sleep 2  # let xfdesktop finish initializing

_set_monitor() {
    local mon="$1"
    local base="/backdrop/screen0/monitor${mon}/workspace0"
    xfconf-query -c xfce4-desktop -np "${base}/last-image"  -t string -s "$WALLPAPER" 2>/dev/null \
        || xfconf-query -c xfce4-desktop  -p "${base}/last-image"  -s "$WALLPAPER" 2>/dev/null || true
    xfconf-query -c xfce4-desktop -np "${base}/image-style" -t int    -s 5         2>/dev/null \
        || xfconf-query -c xfce4-desktop  -p "${base}/image-style" -s 5            2>/dev/null || true
    xfconf-query -c xfce4-desktop -np "${base}/image-show"  -t bool   -s true      2>/dev/null \
        || xfconf-query -c xfce4-desktop  -p "${base}/image-show"  -s true         2>/dev/null || true
}

while IFS= read -r mon; do
    [ -n "$mon" ] && _set_monitor "$mon"
done < <(xrandr --listmonitors 2>/dev/null | awk 'NR>1 {print $NF}')

xfdesktop --reload 2>/dev/null || true
