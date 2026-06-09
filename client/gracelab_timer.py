#!/usr/bin/env python3
"""
GraceLab session timer overlay.
Runs in the guestlab XFCE session via autostart.
Reads /tmp/gracelab-session.json (written by gracelab_client.py) for expiry info.
Displays a compact countdown widget pinned to the top-center of the screen.
Exits when the session file disappears.
"""

import datetime
import json
import os
import sys
import time
import tkinter as tk
from tkinter import font as tkfont

TIMER_FILE = "/tmp/gracelab-session.json"
POLL_MS = 5000
STARTUP_WAIT_S = 30

BG_ACTIVE = "#1f2937"
FG_ACTIVE = "#f9fafb"
FG_LABEL  = "#9ca3af"
BG_WARN   = "#78350f"
FG_WARN   = "#fef3c7"
BG_URGENT = "#7f1d1d"
FG_URGENT = "#fecaca"

WINDOW_W = 200
WINDOW_H = 72


def _read_session_file():
    try:
        with open(TIMER_FILE) as f:
            data = json.load(f)
        dt = datetime.datetime.strptime(data["expires_at"], "%Y-%m-%dT%H:%M:%S")
        expires_ts = dt.replace(tzinfo=datetime.timezone.utc).timestamp()
        warning_secs = int(data.get("warning_seconds", 300))
        return expires_ts, warning_secs
    except Exception:
        return None, 300


class TimerOverlay:

    def __init__(self, root):
        self.root = root
        self.expires_at = None
        self.warning_secs = 300

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=BG_ACTIVE)

        root.update_idletasks()
        sw = root.winfo_screenwidth()
        x = (sw - WINDOW_W) // 2
        root.geometry(f"{WINDOW_W}x{WINDOW_H}+{x}+0")

        f_label = tkfont.Font(family="DejaVu Sans", size=10)
        f_timer = tkfont.Font(family="DejaVu Sans Mono", size=26, weight="bold")

        self._lbl_text = tk.Label(
            root, text="Time Remaining",
            bg=BG_ACTIVE, fg=FG_LABEL, font=f_label,
        )
        self._lbl_text.pack(pady=(8, 0))

        self._lbl_time = tk.Label(
            root, text="--:--",
            bg=BG_ACTIVE, fg=FG_ACTIVE, font=f_timer,
        )
        self._lbl_time.pack()

        self._load_session()
        self._tick()
        self._poll()

    def _load_session(self):
        expires_at, warning_secs = _read_session_file()
        if expires_at is not None:
            self.expires_at = expires_at
            self.warning_secs = warning_secs

    def _set_colors(self, bg, fg, fg_label):
        self.root.configure(bg=bg)
        self._lbl_text.configure(bg=bg, fg=fg_label)
        self._lbl_time.configure(bg=bg, fg=fg)

    def _tick(self):
        if self.expires_at is None:
            self._load_session()
            self.root.after(1000, self._tick)
            return

        remaining = max(0, self.expires_at - time.time())
        mins, secs = divmod(int(remaining), 60)
        self._lbl_time.config(text=f"{mins:02d}:{secs:02d}")

        if remaining <= 60:
            self._set_colors(BG_URGENT, FG_URGENT, FG_URGENT)
        elif remaining <= self.warning_secs:
            self._set_colors(BG_WARN, FG_WARN, FG_WARN)
        else:
            self._set_colors(BG_ACTIVE, FG_ACTIVE, FG_LABEL)

        self.root.after(1000, self._tick)

    def _poll(self):
        if not os.path.exists(TIMER_FILE):
            self.root.destroy()
            return
        self._load_session()
        self.root.after(POLL_MS, self._poll)


def _is_open_session():
    try:
        with open(TIMER_FILE) as f:
            return json.load(f).get("open_mode", False)
    except Exception:
        return False


def main():
    deadline = time.time() + STARTUP_WAIT_S
    while not os.path.exists(TIMER_FILE):
        if time.time() > deadline:
            sys.exit(0)
        time.sleep(1)

    if _is_open_session():
        sys.exit(0)

    root = tk.Tk()
    TimerOverlay(root)
    root.mainloop()


if __name__ == "__main__":
    main()
