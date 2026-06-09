#!/usr/bin/env python3
"""
GraceLab workstation client.

Displays a fullscreen code-entry screen, validates codes against the server,
manages a timed guest session, and reports events.

Dependencies: Python 3 stdlib only (tkinter is part of stdlib).
Config: client_config.ini (copy from client_config.ini.example)

States:
  IDLE -> VALIDATING -> SESSION_STARTING -> SESSION_ACTIVE
       -> SESSION_WARNING -> SESSION_ENDING -> RESETTING -> IDLE

  Any state -> NEEDS_ATTENTION  (on unrecoverable error)
  IDLE      -> OFFLINE          (server unreachable)
  OFFLINE   -> IDLE             (server back)
"""

import configparser
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import tkinter as tk
from tkinter import font as tkfont

CLIENT_VERSION = "0.2.0"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("gracelab")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATHS = [
    os.path.join(os.path.dirname(__file__), "client_config.ini"),
    "/etc/gracelab/client_config.ini",
    "/opt/gracelab-client/client_config.ini",
]


def load_config():
    cfg = configparser.ConfigParser()
    cfg.read_dict({
        "server":  {"url": "http://localhost:5000"},
        "station": {"hostname": "LAB-PC-01", "token": ""},
        "session": {"warning_minutes": "5", "heartbeat_interval_seconds": "30",
                    "sync_interval_seconds": "12"},
        "paths":   {
            "reset_script": "",
            "start_script": "",
            "end_script": "",
        },
        "ui": {"fullscreen": "true", "organization_name": "Grace Marketplace"},
    })
    read = cfg.read(_CONFIG_PATHS)
    if not read:
        log.warning("No client_config.ini found; using built-in defaults.")
    return cfg

# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

class APIError(Exception):
    pass


class GraceLabAPI:
    def __init__(self, cfg):
        self._base = cfg.get("server", "url").rstrip("/")
        self._hostname = cfg.get("station", "hostname")
        self._token = cfg.get("station", "token")
        self._timeout = 8

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "X-Station-ID": self._hostname,
            "Authorization": f"Bearer {self._token}",
        }

    def _post(self, path, body):
        url = f"{self._base}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except Exception:
                raise APIError(f"HTTP {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise APIError(f"Connection failed: {e}")

    def _get(self, path):
        url = f"{self._base}{path}"
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())
        except (urllib.error.URLError, OSError) as e:
            raise APIError(f"Connection failed: {e}")

    def heartbeat(self, status, current_session_id=None):
        return self._post("/api/station/heartbeat", {
            "hostname": self._hostname,
            "status": status,
            "current_session_id": current_session_id,
            "client_version": CLIENT_VERSION,
        })

    def get_config(self):
        return self._get("/api/station/config")

    def validate(self, code):
        return self._post("/api/session/validate", {"code": code})

    def start(self, session_id):
        return self._post("/api/session/start", {
            "session_id": session_id,
            "hostname": self._hostname,
        })

    def end(self, session_id, reason="expired"):
        return self._post("/api/session/end", {
            "session_id": session_id,
            "end_reason": reason,
        })

    def event(self, session_id, event_type, message=None):
        return self._post("/api/session/event", {
            "session_id": session_id,
            "event_type": event_type,
            "message": message,
        })

    def session_status(self, session_id):
        return self._get(f"/api/session/status/{session_id}")

    def open_start(self):
        return self._post("/api/session/open-start", {})

    def extend_by_code(self, session_id, code):
        return self._post("/api/session/extend-by-code", {
            "session_id": session_id,
            "code": code,
        })

# ---------------------------------------------------------------------------
# Colors / layout constants
# ---------------------------------------------------------------------------

GUEST_TIMER_FILE   = "/tmp/gracelab-session.json"
GUEST_LOGOUT_FLAG  = "/tmp/gracelab-guest-logout"

BG          = "#111827"
FG          = "#f9fafb"
FG_MUTED    = "#9ca3af"
ACCENT      = "#1a56db"
ACCENT_DARK = "#1643b5"
WARN_BG     = "#78350f"
WARN_FG     = "#fef3c7"
ERROR_FG    = "#fca5a5"
SUCCESS_FG  = "#86efac"
ENTRY_BG    = "#1f2937"
ENTRY_FG    = "#f9fafb"
BTN_BG      = ACCENT
BTN_FG      = "#ffffff"

PAD = 20

# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class GraceLabClient:

    # --- States ---
    IDLE            = "IDLE"
    VALIDATING      = "VALIDATING"
    SESSION_STARTING = "SESSION_STARTING"
    SESSION_ACTIVE  = "SESSION_ACTIVE"
    SESSION_WARNING = "SESSION_WARNING"
    SESSION_ENDING  = "SESSION_ENDING"
    RESETTING       = "RESETTING"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    OFFLINE         = "OFFLINE"
    TOS_PENDING     = "TOS_PENDING"

    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.api = GraceLabAPI(cfg)

        self._state = self.IDLE
        self._session_id = None
        self._expires_at = None          # float epoch
        self._warning_seconds = int(cfg.get("session", "warning_minutes")) * 60
        self._heartbeat_interval = int(cfg.get("session", "heartbeat_interval_seconds"))
        self._sync_interval = int(cfg.get("session", "sync_interval_seconds"))
        self._org_name = cfg.get("ui", "organization_name")
        self._fullscreen = cfg.getboolean("ui", "fullscreen")

        self._open_lab_mode = False
        self._tos_text = ""
        self._open_session_duration_minutes = 120
        self._is_open_session = False
        self._tos_callback = None

        self._warning_fired = False
        self._timer_job = None           # after() handle for session countdown
        self._heartbeat_job = None
        self._sync_job = None            # after() handle for session status poll
        self._code_trace_id = None       # trace handle for code entry formatter
        self._last_guestlab_switch = 0   # epoch of last dm-tool switch-to-user guestlab call

        self._build_ui()
        self._show_idle()
        self._schedule_heartbeat()
        self._check_orphaned_session()

        # Fetch org name from server (non-blocking)
        threading.Thread(target=self._fetch_server_config, daemon=True).start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.root.configure(bg=BG)
        self.root.title("GraceLab")

        if self._fullscreen:
            self.root.after(150, lambda: self.root.attributes("-fullscreen", True))
        else:
            self.root.geometry("900x600")

        # Prevent accidental close / alt-F4
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        # Escape only works in NEEDS_ATTENTION to allow admin recovery
        self.root.bind("<Escape>", self._on_escape)

        self._main = tk.Frame(self.root, bg=BG)
        self._main.pack(fill=tk.BOTH, expand=True)

        self._make_fonts()

    def _make_fonts(self):
        self._f_org     = tkfont.Font(family="DejaVu Sans", size=18, weight="bold")
        self._f_heading = tkfont.Font(family="DejaVu Sans", size=28, weight="bold")
        self._f_body    = tkfont.Font(family="DejaVu Sans", size=14)
        self._f_small   = tkfont.Font(family="DejaVu Sans", size=11)
        self._f_code    = tkfont.Font(family="DejaVu Sans Mono", size=36, weight="bold")
        self._f_timer   = tkfont.Font(family="DejaVu Sans Mono", size=48, weight="bold")
        self._f_entry   = tkfont.Font(family="DejaVu Sans Mono", size=32)
        self._f_btn     = tkfont.Font(family="DejaVu Sans", size=16, weight="bold")

    def _clear(self):
        for w in self._main.winfo_children():
            w.destroy()

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------

    def _show_idle(self):
        self._set_state(self.IDLE)
        self._cancel_timer()
        self._is_open_session = False
        self._clear()

        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(outer, text=self._org_name, bg=BG, fg=FG_MUTED,
                 font=self._f_org).pack(pady=(0, 4))
        tk.Label(outer, text="Computer Lab", bg=BG, fg=FG_MUTED,
                 font=self._f_body).pack(pady=(0, 40))

        if self._open_lab_mode:
            self._build_idle_open(outer)
        else:
            self._build_idle_code_entry(outer)

    def _build_idle_open(self, outer):
        tk.Label(outer, text="Welcome", bg=BG, fg=FG,
                 font=self._f_heading).pack(pady=(0, 20))
        tk.Label(outer, text="Press Begin to start your session.",
                 bg=BG, fg=FG_MUTED, font=self._f_body).pack(pady=(0, 30))
        tk.Button(
            outer, text="Begin Session",
            font=self._f_btn, bg=BTN_BG, fg=BTN_FG,
            activebackground=ACCENT_DARK, activeforeground=BTN_FG,
            relief=tk.FLAT, cursor="hand2", padx=40, pady=14,
            command=self._begin_open_session,
        ).pack()
        tk.Label(outer, text="Files saved on this computer are deleted when your session ends.",
                 bg=BG, fg=FG_MUTED, font=self._f_small).pack(pady=(30, 0))

    def _build_idle_code_entry(self, outer):
        tk.Label(outer, text="Enter Session Code", bg=BG, fg=FG,
                 font=self._f_heading).pack(pady=(0, 20))

        entry_frame = tk.Frame(outer, bg=BG)
        entry_frame.pack(pady=(0, 8))

        self._code_var = tk.StringVar()
        self._code_entry = tk.Entry(
            entry_frame,
            textvariable=self._code_var,
            font=self._f_entry,
            width=9,
            justify=tk.CENTER,
            bg=ENTRY_BG, fg=ENTRY_FG,
            insertbackground=ENTRY_FG,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=2,
            highlightcolor=ACCENT,
            highlightbackground="#374151",
        )
        self._code_entry.pack(ipady=10, ipadx=10)
        self._code_entry.focus_set()

        self._code_trace_id = self._code_var.trace_add("write", self._format_code_entry)
        self._code_entry.bind("<Return>", lambda e: self._submit_code())

        self._msg_label = tk.Label(outer, text="", bg=BG, fg=ERROR_FG, font=self._f_small)
        self._msg_label.pack(pady=(6, 0))

        tk.Button(
            outer, text="Start Session",
            font=self._f_btn, bg=BTN_BG, fg=BTN_FG,
            activebackground=ACCENT_DARK, activeforeground=BTN_FG,
            relief=tk.FLAT, cursor="hand2", padx=30, pady=12,
            command=self._submit_code,
        ).pack(pady=(20, 0))

        tk.Label(outer, text="Ask staff if you need a session code.",
                 bg=BG, fg=FG_MUTED, font=self._f_small).pack(pady=(30, 4))
        tk.Label(outer, text="Files saved on this computer are deleted when your session ends.",
                 bg=BG, fg=FG_MUTED, font=self._f_small).pack()

    def _show_tos(self):
        self._set_state(self.TOS_PENDING)
        self._clear()

        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0, rely=0, relwidth=1, relheight=1)

        inner = tk.Frame(outer, bg=BG)
        inner.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(inner, text=self._org_name, bg=BG, fg=FG_MUTED,
                 font=self._f_org).pack(pady=(0, 8))
        tk.Label(inner, text="Terms of Service", bg=BG, fg=FG,
                 font=self._f_heading).pack(pady=(0, 16))

        text_frame = tk.Frame(inner, bg=BG, width=620, height=260)
        text_frame.pack(pady=(0, 20))
        text_frame.pack_propagate(False)

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        tos_box = tk.Text(
            text_frame,
            bg="#111827", fg=FG,
            font=self._f_body,
            wrap=tk.WORD,
            relief=tk.FLAT,
            bd=0,
            padx=14, pady=12,
            yscrollcommand=scrollbar.set,
        )
        tos_box.insert(tk.END, self._tos_text)
        tos_box.config(state=tk.DISABLED)
        tos_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=tos_box.yview)

        tk.Label(inner,
                 text="By clicking I Agree you accept these terms and your session will begin.",
                 bg=BG, fg=FG_MUTED, font=self._f_small).pack(pady=(0, 16))

        btn_frame = tk.Frame(inner, bg=BG)
        btn_frame.pack()

        tk.Button(
            btn_frame, text="I Agree",
            font=self._f_btn, bg=BTN_BG, fg=BTN_FG,
            activebackground=ACCENT_DARK, activeforeground=BTN_FG,
            relief=tk.FLAT, cursor="hand2", padx=30, pady=12,
            command=self._tos_agree,
        ).pack(side=tk.LEFT, padx=(0, 12))

        tk.Button(
            btn_frame, text="No Thanks",
            font=self._f_btn, bg="#374151", fg=FG_MUTED,
            activebackground="#4b5563", activeforeground=FG,
            relief=tk.FLAT, cursor="hand2", padx=30, pady=12,
            command=self._show_idle,
        ).pack(side=tk.LEFT)

    def _tos_agree(self):
        if self._tos_callback:
            cb = self._tos_callback
            self._tos_callback = None
            cb()

    def _begin_open_session(self):
        if self._state != self.IDLE:
            return
        if self._tos_text:
            self._tos_callback = self._proceed_open_session
            self._show_tos()
        else:
            self._proceed_open_session()

    def _proceed_open_session(self):
        self._show_session_starting()
        threading.Thread(target=self._do_open_session, daemon=True).start()

    def _do_open_session(self):
        try:
            result = self.api.open_start()
        except APIError as e:
            log.warning("Open start failed (server unreachable?): %s", e)
            self.root.after(0, self._show_offline)
            self.root.after(5000, self._reconnect_loop)
            return

        if not result.get("ok"):
            err = result.get("error", "unknown")
            log.warning("Open start rejected: %s", err)
            self.root.after(0, lambda: self._show_needs_attention(
                "Could not start session. Please ask staff for help."
            ))
            return

        expires_str = result.get("expires_at", "")
        session_id = result.get("session_id")
        self._session_id = session_id
        self._expires_at = self._parse_expires(expires_str)
        self._is_open_session = True
        log.info("Open session %s started, expires %s", session_id, expires_str)
        self._save_session_state()

        start_script = self.cfg.get("paths", "start_script")
        start_ok = self._run_script(start_script, "start", failure_event="start_script_failed")
        if not start_ok:
            try:
                self.api.end(session_id, "failed")
            except APIError:
                pass
            self._session_id = None
            self._expires_at = None
            self._is_open_session = False
            self.root.after(0, lambda: self._show_needs_attention(
                "Session start script failed. Please ask staff for help."
            ))
            return

        self.root.after(0, self._show_session_active)
        self.root.after(self._sync_interval * 1000, self._sync_tick)

    def _show_validating(self):
        self._set_state(self.VALIDATING)
        self._clear()
        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        tk.Label(outer, text="Checking code…", bg=BG, fg=FG, font=self._f_heading).pack()

    def _show_session_starting(self):
        self._set_state(self.SESSION_STARTING)
        self._clear()
        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        tk.Label(outer, text="Starting session…", bg=BG, fg=FG, font=self._f_heading).pack()

    def _show_session_active(self):
        self._set_state(self.SESSION_ACTIVE)
        self._warning_fired = False
        self._clear()

        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(outer, text=self._org_name, bg=BG, fg=FG_MUTED, font=self._f_org).pack(pady=(0, 30))
        tk.Label(outer, text="Session Active", bg=BG, fg=SUCCESS_FG, font=self._f_heading).pack()

        self._timer_label = tk.Label(outer, text="--:--", bg=BG, fg=FG, font=self._f_timer)
        self._timer_label.pack(pady=20)

        tk.Label(outer, text="Time remaining", bg=BG, fg=FG_MUTED, font=self._f_body).pack()
        tk.Label(outer,
                 text="Files saved here will be deleted when your session ends.",
                 bg=BG, fg=FG_MUTED, font=self._f_small).pack(pady=(30, 0))

        self._tick()

    def _show_warning(self):
        self._set_state(self.SESSION_WARNING)
        self._clear()

        outer = tk.Frame(self._main, bg=WARN_BG)
        outer.place(relx=0, rely=0, relwidth=1, relheight=1)

        inner = tk.Frame(outer, bg=WARN_BG)
        inner.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(inner, text="⚠  Session Ending Soon", bg=WARN_BG, fg=WARN_FG,
                 font=self._f_heading).pack(pady=(0, 20))

        self._timer_label = tk.Label(inner, text="--:--", bg=WARN_BG, fg=WARN_FG,
                                     font=self._f_timer)
        self._timer_label.pack(pady=10)

        tk.Label(inner, text="Please save anything important to your own storage.",
                 bg=WARN_BG, fg=WARN_FG, font=self._f_body).pack(pady=(10, 4))

        # ── Extension code entry ──────────────────────────────────────────
        tk.Label(inner, text="Have another code? Enter it below to extend your session.",
                 bg=WARN_BG, fg=WARN_FG, font=self._f_small).pack(pady=(14, 4))

        ext_frame = tk.Frame(inner, bg=WARN_BG)
        ext_frame.pack()

        self._ext_var = tk.StringVar()
        self._ext_entry = tk.Entry(
            ext_frame,
            textvariable=self._ext_var,
            font=self._f_entry,
            width=9,
            justify=tk.CENTER,
            bg=ENTRY_BG, fg=ENTRY_FG,
            insertbackground=ENTRY_FG,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=2,
            highlightcolor=ACCENT,
            highlightbackground="#374151",
        )
        self._ext_entry.pack(side=tk.LEFT, ipady=8, ipadx=8)
        self._ext_entry.focus_set()

        self._ext_trace_id = self._ext_var.trace_add("write", self._format_ext_entry)
        self._ext_entry.bind("<Return>", lambda e: self._submit_extension())

        tk.Button(
            ext_frame, text="Extend",
            font=self._f_btn, bg=BTN_BG, fg=BTN_FG,
            activebackground=ACCENT_DARK, activeforeground=BTN_FG,
            relief=tk.FLAT, cursor="hand2", padx=16, pady=8,
            command=self._submit_extension,
        ).pack(side=tk.LEFT, padx=(10, 0))

        self._ext_msg = tk.Label(inner, text="", bg=WARN_BG, fg=ERROR_FG, font=self._f_small)
        self._ext_msg.pack(pady=(6, 0))
        # ─────────────────────────────────────────────────────────────────

        self._tick()

    def _show_ending(self, message="Your session has ended."):
        self._cancel_timer()
        self._cancel_sync()
        self._set_state(self.SESSION_ENDING)
        self._clear()

        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(outer, text=message, bg=BG, fg=FG, font=self._f_heading).pack(pady=(0, 16))
        tk.Label(outer, text="This computer is resetting for the next user.",
                 bg=BG, fg=FG_MUTED, font=self._f_body).pack()

    def _show_resetting(self):
        self._set_state(self.RESETTING)
        self._clear()

        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(outer, text="Resetting…", bg=BG, fg=FG_MUTED, font=self._f_heading).pack()

    def _show_needs_attention(self, reason=""):
        self._set_state(self.NEEDS_ATTENTION)
        self._cancel_timer()
        self._cancel_sync()
        self._clear()

        outer = tk.Frame(self._main, bg="#1c0a00")
        outer.place(relx=0, rely=0, relwidth=1, relheight=1)

        inner = tk.Frame(outer, bg="#1c0a00")
        inner.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(inner, text="This station needs attention.", bg="#1c0a00", fg=ERROR_FG,
                 font=self._f_heading).pack(pady=(0, 12))
        if reason:
            tk.Label(inner, text=reason, bg="#1c0a00", fg=FG_MUTED,
                     font=self._f_small, wraplength=500, justify=tk.CENTER).pack()
        tk.Label(inner, text="Please ask staff for help.",
                 bg="#1c0a00", fg=FG_MUTED, font=self._f_body).pack(pady=(20, 0))

    def _show_offline(self):
        self._set_state(self.OFFLINE)
        self._clear()

        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(outer, text="Server Offline", bg=BG, fg=ERROR_FG, font=self._f_heading).pack()
        tk.Label(outer,
                 text="This computer cannot reach the GraceLab server.\nPlease ask staff for help.",
                 bg=BG, fg=FG_MUTED, font=self._f_body, justify=tk.CENTER).pack(pady=20)

    def _show_recovering(self):
        self._set_state(self.IDLE)
        self._clear()
        outer = tk.Frame(self._main, bg=BG)
        outer.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
        tk.Label(outer, text="Please wait…", bg=BG, fg=FG, font=self._f_heading).pack()
        tk.Label(outer, text="Checking previous session status.",
                 bg=BG, fg=FG_MUTED, font=self._f_body).pack(pady=(12, 0))

    # ------------------------------------------------------------------
    # Session state persistence (survives reboots)
    # ------------------------------------------------------------------

    def _session_state_path(self):
        return self.cfg.get("paths", "session_state",
                            fallback="/opt/gracelab-client/session-state.json")

    def _save_session_state(self):
        import datetime
        if not self._session_id or not self._expires_at:
            return
        try:
            state = {
                "session_id": self._session_id,
                "expires_at": datetime.datetime.utcfromtimestamp(
                    self._expires_at).strftime("%Y-%m-%dT%H:%M:%S"),
                "open_mode": self._is_open_session,
            }
            path = self._session_state_path()
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            os.replace(tmp, path)
        except Exception as e:
            log.warning("Could not save session state: %s", e)

    def _clear_session_state(self):
        try:
            path = self._session_state_path()
            if os.path.exists(path):
                os.unlink(path)
        except Exception as e:
            log.warning("Could not clear session state: %s", e)
        self._clear_guest_timer_file()
        self._clear_guest_logout_flag()

    def _clear_guest_logout_flag(self):
        try:
            if os.path.exists(GUEST_LOGOUT_FLAG):
                os.unlink(GUEST_LOGOUT_FLAG)
        except Exception as e:
            log.warning("Could not clear guest logout flag: %s", e)

    def _write_guest_timer_file(self):
        if self._is_open_session:
            return
        import datetime
        if not self._expires_at:
            return
        try:
            data = {
                "expires_at": datetime.datetime.utcfromtimestamp(
                    self._expires_at).strftime("%Y-%m-%dT%H:%M:%S"),
                "warning_seconds": self._warning_seconds,
            }
            tmp = GUEST_TIMER_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, GUEST_TIMER_FILE)
        except Exception as e:
            log.warning("Could not write guest timer file: %s", e)

    def _clear_guest_timer_file(self):
        try:
            if os.path.exists(GUEST_TIMER_FILE):
                os.unlink(GUEST_TIMER_FILE)
        except Exception as e:
            log.warning("Could not clear guest timer file: %s", e)

    # ------------------------------------------------------------------
    # Startup orphan recovery
    # ------------------------------------------------------------------

    def _check_orphaned_session(self):
        path = self._session_state_path()
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                saved = json.load(f)
        except Exception:
            self._clear_session_state()
            return
        session_id = saved.get("session_id")
        expires_at_str = saved.get("expires_at")
        open_mode = saved.get("open_mode", False)
        if not session_id or not expires_at_str:
            self._clear_session_state()
            return
        log.info("Found orphaned session %s from previous run — checking with server.", session_id)
        self._show_recovering()
        threading.Thread(
            target=self._recover_orphaned_session,
            args=(session_id, expires_at_str, open_mode),
            daemon=True,
        ).start()

    def _recover_orphaned_session(self, session_id, expires_at_str, open_mode=False):
        try:
            result = self.api.session_status(session_id)
        except APIError as e:
            log.warning("Server unreachable during orphan recovery (%s) — running cleanup.", e)
            self._session_id = session_id
            self._expires_at = self._parse_expires(expires_at_str)
            self._clear_session_state()
            self.root.after(0, lambda: self._show_ending("Clearing previous session…"))
            self._end_and_reset("reboot")
            return

        server_status = result.get("status")
        expires_str = result.get("expires_at", expires_at_str)
        expires_at = self._parse_expires(expires_str)
        remaining = expires_at - time.time()

        if server_status == "active" and remaining > 0:
            log.info("Resuming session %s with %.0f s remaining.", session_id, remaining)
            self._session_id = session_id
            self._expires_at = expires_at
            self._is_open_session = open_mode or result.get("open_mode", False)
            self._warning_seconds = result.get("warning_minutes", 5) * 60
            self._save_session_state()
            self._write_guest_timer_file()
            start_script = self.cfg.get("paths", "start_script")
            start_ok = self._run_script(start_script, "start",
                                        failure_event="start_script_failed")
            if start_ok:
                self.root.after(0, self._show_session_active)
                self.root.after(self._sync_interval * 1000, self._sync_tick)
            else:
                self._clear_session_state()
                try:
                    self.api.end(session_id, "failed")
                except APIError:
                    pass
                self.root.after(0, lambda: self._show_needs_attention(
                    "Session resume failed. Please ask staff for help."
                ))
        else:
            log.info("Orphaned session %s status=%s remaining=%.0f s — cleaning up.",
                     session_id, server_status, remaining)
            self._session_id = session_id
            self._expires_at = expires_at
            self._clear_session_state()
            self.root.after(0, lambda: self._show_ending("Clearing previous session…"))
            self._end_and_reset(server_status or "reboot")

    # ------------------------------------------------------------------
    # Code entry
    # ------------------------------------------------------------------

    def _format_code_entry(self, *_):
        raw = self._code_var.get().replace("-", "")
        raw = "".join(c for c in raw if c.isdigit())[:6]
        formatted = raw[:3] + "-" + raw[3:] if len(raw) > 3 else raw
        # Remove before set to prevent re-entry, restore after
        self._code_var.trace_remove("write", self._code_trace_id)
        self._code_var.set(formatted)
        self._code_trace_id = self._code_var.trace_add("write", self._format_code_entry)
        self.root.after(0, lambda: self._code_entry.icursor(tk.END))

    def _submit_code(self):
        if self._state != self.IDLE:
            return
        code = self._code_var.get().strip()
        if not code:
            return
        if self._tos_text:
            self._tos_callback = lambda: self._proceed_code_validate(code)
            self._show_tos()
        else:
            self._proceed_code_validate(code)

    def _proceed_code_validate(self, code):
        self._show_validating()
        threading.Thread(target=self._validate_and_start, args=(code,), daemon=True).start()

    def _validate_and_start(self, code):
        try:
            result = self.api.validate(code)
        except APIError as e:
            log.warning("Validate failed (server unreachable?): %s", e)
            self.root.after(0, self._show_offline)
            self.root.after(5000, self._reconnect_loop)
            return

        if not result.get("ok"):
            self.root.after(0, lambda: self._idle_with_error(
                "That code is invalid, expired, or already used.\n"
                "Please check the code or ask staff for help."
            ))
            return

        session_id = result["session_id"]
        warning_secs = result.get("warning_minutes", 5) * 60
        self._warning_seconds = warning_secs

        self.root.after(0, self._show_session_starting)

        try:
            start_result = self.api.start(session_id)
        except APIError as e:
            log.warning("Start failed: %s", e)
            self.root.after(0, lambda: self._idle_with_error(
                "Could not start session. Please ask staff for help."
            ))
            return

        if not start_result.get("ok"):
            err = start_result.get("error", "unknown")
            log.warning("Start rejected: %s", err)
            self.root.after(0, lambda: self._idle_with_error(
                "Could not start session. Please ask staff for help."
            ))
            return

        expires_str = start_result.get("expires_at", "")
        self._session_id = session_id
        self._expires_at = self._parse_expires(expires_str)
        log.info("Session %s started, expires %s", session_id, expires_str)
        self._save_session_state()
        self._write_guest_timer_file()

        start_script = self.cfg.get("paths", "start_script")
        start_ok = self._run_script(start_script, "start",
                                    failure_event="start_script_failed")
        if not start_ok:
            # Report failure and immediately end the session so the server
            # doesn't keep the station marked in_use.
            try:
                self.api.end(session_id, "failed")
            except APIError:
                pass
            self.root.after(0, lambda: self._show_needs_attention(
                "Session start script failed. Please ask staff for help."
            ))
            self._session_id = None
            self._expires_at = None
            return

        self.root.after(0, self._show_session_active)
        self.root.after(self._sync_interval * 1000, self._sync_tick)

    def _idle_with_error(self, msg):
        self._show_idle()
        if hasattr(self, "_msg_label"):
            self._msg_label.config(text=msg)

    def _format_ext_entry(self, *_):
        raw = self._ext_var.get().replace("-", "")
        raw = "".join(c for c in raw if c.isdigit())[:6]
        formatted = raw[:3] + "-" + raw[3:] if len(raw) > 3 else raw
        self._ext_var.trace_remove("write", self._ext_trace_id)
        self._ext_var.set(formatted)
        self._ext_entry.icursor(tk.END)
        self._ext_trace_id = self._ext_var.trace_add("write", self._format_ext_entry)

    def _submit_extension(self):
        if self._state != self.SESSION_WARNING:
            return
        code = self._ext_var.get().strip()
        if not code:
            return
        self._ext_entry.config(state=tk.DISABLED)
        self._ext_msg.config(text="Checking code…", fg=WARN_FG)
        threading.Thread(
            target=self._do_extend_by_code, args=(code,), daemon=True
        ).start()

    def _do_extend_by_code(self, code):
        try:
            result = self.api.extend_by_code(self._session_id, code)
        except APIError as e:
            log.warning("Extend-by-code failed (server unreachable?): %s", e)
            self.root.after(0, self._ext_reset_with_error,
                            "Server unreachable. Try again.")
            return

        if not result.get("ok"):
            self.root.after(0, self._ext_reset_with_error,
                            "Invalid or already-used code.")
            return

        expires_str = result.get("expires_at", "")
        added = result.get("added_minutes", 0)
        new_expires = self._parse_expires(expires_str)
        log.info("Session extended by %d min via code. New expiry: %s", added, expires_str)

        def _apply():
            self._expires_at = new_expires
            self._warning_fired = False
            self._save_session_state()
            self._write_guest_timer_file()
            self._show_session_active()

        self.root.after(0, _apply)

    def _ext_reset_with_error(self, msg):
        if self._state != self.SESSION_WARNING:
            return
        try:
            self._ext_entry.config(state=tk.NORMAL)
            self._ext_var.set("")
            self._ext_msg.config(text=msg, fg=ERROR_FG)
            self._ext_entry.focus_set()
        except tk.TclError:
            pass

    @staticmethod
    def _parse_expires(s):
        import datetime
        try:
            dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
        except Exception:
            return time.time() + 3600

    # ------------------------------------------------------------------
    # Session timer
    # ------------------------------------------------------------------

    def _tick(self):
        if self._state not in (self.SESSION_ACTIVE, self.SESSION_WARNING):
            return

        # Guest clicked "End Session" from their desktop.
        if os.path.exists(GUEST_LOGOUT_FLAG):
            try:
                os.unlink(GUEST_LOGOUT_FLAG)
            except OSError:
                pass
            self._on_guest_logout()
            return

        # Re-switch to guestlab periodically so that if light-locker or the
        # greeter ever surfaces the gracelab session mid-session, it snaps back
        # automatically within ~60 s without requiring staff intervention.
        now = time.time()
        if now - self._last_guestlab_switch >= 60:
            self._last_guestlab_switch = now
            threading.Thread(target=self._ensure_guestlab_active, daemon=True).start()

        remaining = max(0, self._expires_at - now)
        mins, secs = divmod(int(remaining), 60)
        self._timer_label.config(text=f"{mins:02d}:{secs:02d}")

        if remaining <= 0:
            self._on_session_expired()
            return

        if remaining <= self._warning_seconds and not self._warning_fired and not self._is_open_session:
            self._warning_fired = True
            self._on_warning()

        self._timer_job = self.root.after(1000, self._tick)

    def _cancel_timer(self):
        if self._timer_job:
            self.root.after_cancel(self._timer_job)
            self._timer_job = None

    def _cancel_sync(self):
        if self._sync_job:
            self.root.after_cancel(self._sync_job)
            self._sync_job = None

    def _on_warning(self):
        self._show_warning()
        threading.Thread(target=self._send_warning_event, daemon=True).start()

    def _send_warning_event(self):
        try:
            self.api.event(self._session_id, "session_warning", "Warning screen shown.")
        except APIError:
            pass

    # ------------------------------------------------------------------
    # Session sync loop — polls server so staff actions reach the client
    # ------------------------------------------------------------------

    def _sync_tick(self):
        if self._state not in (self.SESSION_ACTIVE, self.SESSION_WARNING):
            return
        threading.Thread(target=self._do_sync, daemon=True).start()

    def _do_sync(self):
        if not self._session_id:
            return
        try:
            result = self.api.session_status(self._session_id)
        except APIError as e:
            log.warning("Session sync failed (server unreachable?): %s", e)
            # Don't end the session — let the local timer remain authoritative
            # if the network is temporarily unavailable.
            self._sync_job = self.root.after(self._sync_interval * 1000, self._sync_tick)
            return

        self.root.after(0, lambda: self._handle_sync_result(result))

    def _handle_sync_result(self, result):
        if self._state not in (self.SESSION_ACTIVE, self.SESSION_WARNING):
            return

        if not result.get("ok"):
            log.warning("Sync returned not-ok: %s", result)
            self._sync_job = self.root.after(self._sync_interval * 1000, self._sync_tick)
            return

        server_status = result.get("status")
        station_status = result.get("station_status")

        # Staff ended the session from the dashboard
        if server_status in ("ended_by_staff", "cancelled", "expired", "failed"):
            log.info("Server reports session %s — ending locally.", server_status)
            self._cancel_timer()
            self._cancel_sync()
            _ending_messages = {
                "ended_by_staff": "Your session has been ended by staff.",
                "cancelled":      "This session is no longer available.",
                "expired":        "Your session has ended.",
                "failed":         "This session ended due to an error.",
            }
            msg = _ending_messages.get(server_status, "Your session has ended.")
            self._show_ending(msg)
            threading.Thread(target=self._end_and_reset, args=(server_status,), daemon=True).start()
            return

        # Station marked out of service or needs attention
        if station_status in ("out_of_service", "needs_attention"):
            log.warning("Station status is %s — showing attention screen.", station_status)
            self._cancel_timer()
            self._cancel_sync()
            self._show_needs_attention(f"This station has been marked {station_status.replace('_', ' ')}.")
            return

        # Staff extended the session — update local expiry
        new_expires_str = result.get("expires_at")
        if new_expires_str:
            new_expires = self._parse_expires(new_expires_str)
            if new_expires != self._expires_at:
                delta = new_expires - self._expires_at
                log.info("Session expiry updated by %.0f seconds (server sync).", delta)
                self._expires_at = new_expires
                self._save_session_state()
                self._write_guest_timer_file()
                # If we were in warning state but extension pushed us back past the threshold,
                # go back to active screen so the amber screen doesn't stay up.
                remaining = new_expires - time.time()
                if self._state == self.SESSION_WARNING and remaining > self._warning_seconds:
                    self._warning_fired = False
                    self._show_session_active()

        self._sync_job = self.root.after(self._sync_interval * 1000, self._sync_tick)

    def _on_session_expired(self):
        self._cancel_timer()
        self._show_ending()
        threading.Thread(target=self._end_and_reset, args=("expired",), daemon=True).start()

    def _on_guest_logout(self):
        self._cancel_timer()
        self._show_ending("Session ended.")
        threading.Thread(target=self._end_and_reset, args=("guest_logout",), daemon=True).start()

    def _ensure_guestlab_active(self):
        override = "/run/gracelab-admin-override"
        try:
            if time.time() - os.stat(override).st_mtime < 4 * 3600:
                return
        except OSError:
            pass
        try:
            env = dict(os.environ)
            env.setdefault("XDG_SEAT_PATH", "/org/freedesktop/DisplayManager/Seat0")
            subprocess.run(["dm-tool", "switch-to-user", "guestlab"],
                           env=env, timeout=3, capture_output=True)
        except Exception:
            pass

    def _run_script(self, script_path, label, timeout=120, failure_event="client_error"):
        """
        Run a lifecycle script. Returns True on success, False on failure.
        Reports failure_event to the server on failure so the dashboard can
        mark the station needs_attention. Does nothing if path is empty.

        script_path may be a plain path or a command line (e.g. "sudo /path/script.sh").
        Parsed with shlex so sudoers entries work without shell=True.
        """
        import shlex
        if not script_path:
            log.info("No %s script configured — skipping.", label)
            return True

        cmd = shlex.split(script_path)
        executable = cmd[0]
        file_to_check = cmd[1] if executable == "sudo" and len(cmd) > 1 else executable

        if not os.path.isfile(file_to_check):
            log.warning("%s script not found: %s — skipping.", label, file_to_check)
            return True

        log.info("Running %s script: %s", label, script_path)
        try:
            result = subprocess.run(
                cmd,
                timeout=timeout,
                check=True,
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                log.info("%s stdout: %s", label, result.stdout.strip())
            log.info("%s script completed OK.", label)
            return True
        except subprocess.CalledProcessError as e:
            msg = f"{label} script exited {e.returncode}: {e.stderr.strip() or e.stdout.strip()}"
            log.error("%s", msg)
            try:
                self.api.event(self._session_id, failure_event, msg)
            except APIError:
                pass
            return False
        except subprocess.TimeoutExpired:
            msg = f"{label} script timed out after {timeout}s."
            log.error("%s", msg)
            try:
                self.api.event(self._session_id, failure_event, msg)
            except APIError:
                pass
            return False
        except Exception as e:
            msg = f"{label} script error: {e}"
            log.error("%s", msg)
            try:
                self.api.event(self._session_id, failure_event, msg)
            except APIError:
                pass
            return False

    def _end_and_reset(self, reason):
        sid = self._session_id
        self._clear_session_state()

        # Run end_script before reporting to server so a failure marks
        # the station needs_attention rather than silently returning available.
        end_script = self.cfg.get("paths", "end_script")
        end_ok = self._run_script(end_script, "end",
                                  failure_event="end_script_failed")

        # Switch display back to gracelab now that guestlab is dead.
        # Called from the client process (runs as gracelab in gracelab's session)
        # so LightDM honours it after whatever the greeter did on session death.
        env = dict(os.environ)
        env.setdefault("XDG_SEAT_PATH", "/org/freedesktop/DisplayManager/Seat0")
        try:
            subprocess.run(["dm-tool", "switch-to-user", "gracelab"],
                           env=env, timeout=5, capture_output=True)
            log.info("Switched display back to gracelab.")
        except Exception as e:
            log.warning("dm-tool switch-to-user gracelab failed: %s", e)

        try:
            self.api.end(sid, reason)
        except APIError as e:
            log.warning("Could not report session end: %s", e)

        if not end_ok:
            self.root.after(0, lambda: self._show_needs_attention(
                "Session end script failed. Please ask staff for help."
            ))
            return

        self.root.after(0, self._show_resetting)
        self._run_reset(sid)

    def _run_reset(self, sid):
        reset_script = self.cfg.get("paths", "reset_script")

        try:
            self.api.event(sid, "profile_reset_started")
        except APIError:
            pass

        reset_ok = self._run_script(reset_script, "reset",
                                    failure_event="reset_script_failed")

        if reset_ok:
            try:
                self.api.event(sid, "profile_reset_success",
                               "Guest profile reset completed.")
            except APIError:
                pass
            self._session_id = None
            self._expires_at = None
            self.root.after(0, self._show_idle)
        else:
            try:
                self.api.event(sid, "profile_reset_failed", "Reset script failed.")
            except APIError:
                pass
            self.root.after(0, lambda: self._show_needs_attention(
                "Profile reset failed. Please ask staff for help."
            ))

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _schedule_heartbeat(self):
        self._heartbeat_job = self.root.after(
            self._heartbeat_interval * 1000,
            self._heartbeat_tick,
        )

    def _heartbeat_tick(self):
        threading.Thread(target=self._send_heartbeat, daemon=True).start()

    def _send_heartbeat(self):
        status = "in_use" if self._state in (
            self.SESSION_ACTIVE, self.SESSION_WARNING, self.SESSION_STARTING
        ) else "available"
        try:
            resp = self.api.heartbeat(status, self._session_id)
            station_status = resp.get("station_status") if resp else None
            if self._state == self.OFFLINE:
                log.info("Server back online.")
                self.root.after(0, self._show_idle)
            elif self._state == self.NEEDS_ATTENTION and station_status == "available":
                log.info("Station cleared by server — returning to idle.")
                self.root.after(0, self._show_idle)
            elif self._state == self.IDLE and station_status in ("needs_attention", "out_of_service"):
                log.warning("Server reports station %s — showing attention screen.", station_status)
                self.root.after(0, lambda s=station_status: self._show_needs_attention(
                    f"This station has been marked {s.replace('_', ' ')}."
                ))
        except APIError as e:
            log.warning("Heartbeat failed: %s", e)
            if self._state == self.IDLE:
                self.root.after(0, self._show_offline)
        finally:
            self._schedule_heartbeat()

    def _reconnect_loop(self):
        if self._state != self.OFFLINE:
            return
        threading.Thread(target=self._try_reconnect, daemon=True).start()

    def _try_reconnect(self):
        try:
            self.api.heartbeat("available")
            log.info("Reconnected to server.")
            self.root.after(0, self._show_idle)
        except APIError:
            self.root.after(10000, self._reconnect_loop)

    # ------------------------------------------------------------------
    # Server config fetch
    # ------------------------------------------------------------------

    def _fetch_server_config(self):
        try:
            result = self.api.get_config()
            if result.get("ok"):
                if result.get("organization_name"):
                    self._org_name = result["organization_name"]
                self._open_lab_mode = result.get("open_lab_mode", False)
                self._tos_text = result.get("tos_text", "")
                self._open_session_duration_minutes = result.get(
                    "open_session_duration_minutes", 120
                )
                if self._state == self.IDLE:
                    self.root.after(0, self._show_idle)
        except APIError:
            pass

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _set_state(self, state):
        log.info("State: %s -> %s", self._state, state)
        self._state = state

    def _on_escape(self, event):
        # Only allow exiting fullscreen in NEEDS_ATTENTION so labadmin
        # can recover without a full reboot. In all other states, ignore.
        if self._state == self.NEEDS_ATTENTION:
            self.root.attributes("-fullscreen", False)
            self.root.geometry("900x600")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    cfg = load_config()

    if not cfg.get("station", "token"):
        log.error("No station token configured. Edit client_config.ini.")
        sys.exit(1)

    root = tk.Tk()
    app = GraceLabClient(root, cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
