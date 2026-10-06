"""Small view helpers shared by the staff UI blueprints."""

import re
from datetime import datetime, timezone

from flask import redirect, request


def redirect_back(default_url):
    """
    Redirect to the form's `next` field when it is a local path, else to
    default_url. Lets an action posted from a station's page land back on it
    (on a phone, being dumped on the full station list is a long scroll).
    Only same-site paths are honoured: no scheme, no host, no "//" or "\\".
    """
    target = request.form.get("next", "")
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return redirect(target)
    return redirect(default_url)


def ago(dt, now=None):
    """'just now', '3 min ago', '2 h ago', '4 days ago' for a UTC datetime."""
    if dt is None:
        return "never"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{max(1, round(secs / 60))} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    days = secs // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def to_local(dt):
    """
    Convert a stored datetime to the server's local time zone (the lab's).
    The DB stores UTC; SQLite hands it back naive, so naive means UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


_LOCAL_FORMATS = {
    "time": "%I:%M %p",                  # 1:48 PM
    "seconds": "%I:%M:%S %p",            # 1:48:05 PM
    "date": "%Y-%m-%d",                  # 2026-10-06
    "datetime": "%Y-%m-%d %I:%M %p",     # 2026-10-06 1:48 PM
    "full": "%Y-%m-%d %I:%M:%S %p %Z",   # 2026-10-06 1:48:05 PM EDT
    "ticket": "%a, %b %d at %I:%M %p",   # Tue, Oct 6 at 1:48 PM
}


def localtime(dt, style="datetime", default="—"):
    """Format a stored UTC datetime in local time; see _LOCAL_FORMATS."""
    if dt is None:
        return default
    s = to_local(dt).strftime(_LOCAL_FORMATS[style])
    # Drop leading zeros on hours and days: "01:48 PM" -> "1:48 PM", "Oct 06" -> "Oct 6"
    return re.sub(r"(^|[ ,])0(\d)", r"\1\2", s)


def register(app):
    app.add_template_filter(ago, "ago")
    app.add_template_filter(localtime, "localtime")
