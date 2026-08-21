from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import check_password_hash, generate_password_hash
from extensions import db, csrf
from models import Station, Session, SessionEvent, Setting

api_bp = Blueprint("api", __name__, url_prefix="/api")
csrf.exempt(api_bp)

# ---------------------------------------------------------------------------
# Per-station code attempt throttle (in-process, resets on server restart)
# 5 failures within a window → 30-second cooldown
#
# TODO: This is in-memory and assumes a single Gunicorn worker. Cooldown
# state is lost on server restart and is not shared across workers.
# For multi-worker or internet-facing deployment, replace with a
# Redis-backed store (e.g. Flask-Limiter with RedisStorage).
# ---------------------------------------------------------------------------
_FAIL_WINDOW_SECONDS = 300   # 5 minutes rolling window
_FAIL_LIMIT = 5              # failures before cooldown
_COOLDOWN_SECONDS = 30       # lockout duration

# { station_hostname: [(timestamp, ...), ...] }
_fail_log: dict = defaultdict(list)
# { station_hostname: cooldown_until_timestamp }
_cooldown_until: dict = {}


def _check_throttle(station_hostname):
    """Return (allowed: bool, retry_after_seconds: int)."""
    now = datetime.now(timezone.utc).timestamp()

    # Still in cooldown?
    until = _cooldown_until.get(station_hostname, 0)
    if now < until:
        return False, int(until - now)

    # Prune old entries outside the rolling window
    window_start = now - _FAIL_WINDOW_SECONDS
    _fail_log[station_hostname] = [t for t in _fail_log[station_hostname] if t > window_start]
    return True, 0


def _record_failure(station_hostname):
    now = datetime.now(timezone.utc).timestamp()
    _fail_log[station_hostname].append(now)
    if len(_fail_log[station_hostname]) >= _FAIL_LIMIT:
        _cooldown_until[station_hostname] = now + _COOLDOWN_SECONDS
        _fail_log[station_hostname] = []


def _clear_failures(station_hostname):
    _fail_log[station_hostname] = []
    _cooldown_until.pop(station_hostname, None)


# ---------------------------------------------------------------------------
# Station authentication
# ---------------------------------------------------------------------------

def _auth_station():
    """Return (station, error_response) from the request headers."""
    station_id = request.headers.get("X-Station-ID")
    auth_header = request.headers.get("Authorization", "")

    if not station_id or not auth_header.startswith("Bearer "):
        return None, (jsonify({"ok": False, "error": "missing_credentials"}), 401)

    token = auth_header[len("Bearer "):]
    station = Station.query.filter_by(hostname=station_id).first()

    if not station or not station.verify_token(token):
        return None, (jsonify({"ok": False, "error": "invalid_credentials"}), 401)

    return station, None


def station_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        station, err = _auth_station()
        if err:
            return err
        return f(station, *args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_event(session_id, station_id, event_type, message=None):
    db.session.add(SessionEvent(
        session_id=session_id,
        station_id=station_id,
        event_type=event_type,
        message=message,
    ))


def _server_time():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _update_station_seen(station, reported_status=None):
    station.last_seen = datetime.now(timezone.utc)
    if station.status in ("out_of_service", "needs_attention"):
        return
    # Server is authoritative: if a session is running, keep in_use regardless of what
    # the client reports. A buggy client can't accidentally free a session by heartbeating
    # "available" while a session record still exists.
    if station.current_session_id:
        station.status = "in_use"
    elif reported_status in ("available", "in_use", "needs_attention"):
        station.status = reported_status


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@api_bp.route("/station/heartbeat", methods=["POST"])
@station_required
def heartbeat(station):
    data = request.get_json(silent=True) or {}
    reported_status = data.get("status", "available")
    client_version = data.get("client_version")

    allowed = {"available", "in_use", "needs_attention"}
    if reported_status not in allowed:
        reported_status = "available"

    _update_station_seen(station, reported_status=reported_status)

    if client_version and isinstance(client_version, str):
        station.client_version = client_version[:32]

    # Self-reported by the client once it has actually switched away to the
    # admin session — same trust model as client_version above (authenticated
    # station report, no separate verification). maintenance_requested is
    # NOT settable here; only the admin dashboard sets that.
    station.maintenance_active = bool(data.get("maintenance_active", False))

    db.session.commit()

    # Include active session info so the client can recover after a reboot
    # even if its local session-state.json was lost.
    active_session_id = None
    active_expires_at = None
    active_warning_minutes = None
    active_open_mode = None
    if station.current_session_id:
        sess = db.session.get(Session, station.current_session_id)
        if sess and sess.status == "active" and sess.expires_at:
            active_session_id = sess.id
            active_expires_at = sess.expires_at.strftime("%Y-%m-%dT%H:%M:%S")
            active_warning_minutes = int(Setting.get(
                "warning_minutes", current_app.config["SESSION_WARNING_MINUTES"]
            ))
            active_open_mode = sess.code_display == "OPEN"

    # One-shot command channel (reset_gracelab | reboot). Only surfaced while
    # a command is actually outstanding (not yet complete/failed) — this is
    # what keeps a stale/duplicated heartbeat from re-triggering a completed
    # command: once the client's completion report clears pending_command_id
    # server-side, station_command simply stops appearing.
    station_command = None
    if station.pending_command_id and station.pending_command_status not in ("complete", "failed"):
        station_command = {
            "id": station.pending_command_id,
            "type": station.pending_command_type,
        }

    return jsonify({
        "ok": True,
        "server_time": _server_time(),
        "station_status": station.status,
        "active_session_id": active_session_id,
        "active_expires_at": active_expires_at,
        "active_warning_minutes": active_warning_minutes,
        "active_open_mode": active_open_mode,
        # Session-admission lock: any non-null desired_client_version means a
        # new session must not start (see the update-lock checks below).
        "update_pending": bool(station.desired_client_version),
        "update_status": station.client_update_status,
        "desired_client_version": station.desired_client_version,
        # Maintenance-mode admission lock (Patch C) — see the maintenance
        # checks in session_validate/session_start/session_open_start.
        "maintenance_requested": bool(station.maintenance_requested),
        "maintenance_active": bool(station.maintenance_active),
        "station_command": station_command,
    })


@api_bp.route("/station/config", methods=["GET"])
@station_required
def station_config(station):
    _update_station_seen(station)
    db.session.commit()

    return jsonify({
        "ok": True,
        "warning_minutes": Setting.get_int(
            "warning_minutes",
            current_app.config["SESSION_WARNING_MINUTES"]
        ),
        "organization_name": Setting.get(
            "organization_name",
            current_app.config["ORGANIZATION_NAME"]
        ),
        "open_lab_mode": Setting.get_bool("open_lab_mode", False),
        "open_session_duration_minutes": Setting.get_int("open_session_duration_minutes", 120),
        "tos_text": Setting.get("tos_text", ""),
        "update_pending": bool(station.desired_client_version),
        "update_status": station.client_update_status,
        "desired_client_version": station.desired_client_version,
        "maintenance_requested": bool(station.maintenance_requested),
        "maintenance_active": bool(station.maintenance_active),
    })


@api_bp.route("/station/command-status", methods=["POST"])
@station_required
def station_command_status(station):
    """
    Client report for the one-shot command channel (reset_gracelab | reboot).
    Replay-safety hinges on the command_id match below: a stale or duplicate
    report for a command that has already been cleared (or superseded by a
    newer one) is accepted-but-ignored rather than resurrecting/corrupting
    the current pending command.
    """
    data = request.get_json(silent=True) or {}
    command_id = data.get("command_id")
    status = data.get("status", "")
    error = data.get("error", "")

    allowed = {"acknowledged", "complete", "failed"}
    if status not in allowed:
        return jsonify({"ok": False, "error": "invalid_status"}), 400
    if not command_id:
        return jsonify({"ok": False, "error": "missing_command_id"}), 400

    if station.pending_command_id != command_id:
        return jsonify({"ok": True, "ignored": True})

    if status in ("complete", "failed"):
        _log_event(
            None, station.id,
            "client_error" if status == "failed" else "client_heartbeat",
            f"Command {station.pending_command_type} ({command_id}) {status}"
            + (f": {error}" if error else ""),
        )
        if status == "failed":
            station.status = "needs_attention"
        station.pending_command_type = None
        station.pending_command_id = None
        station.pending_command_status = status
        station.pending_command_error = error or None
    else:
        station.pending_command_status = status
        station.pending_command_error = error or None

    station.last_seen = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/station/maintenance-exit", methods=["POST"])
@station_required
def station_maintenance_exit(station):
    """
    Called by the client itself when a LOCAL "Return to GraceLab" request
    (see gracelab_client.py's _check_local_maintenance_exit_request) is
    honored, so the server-authoritative maintenance_requested flag stays in
    sync with what actually happened at the console. Idempotent — safe to
    call whether or not maintenance was actually requested.
    """
    station.maintenance_requested = False
    station.last_seen = datetime.now(timezone.utc)
    _log_event(None, station.id, "client_heartbeat", "Maintenance exit requested locally.")
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/session/validate", methods=["POST"])
@station_required
def session_validate(station):
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()

    if not code:
        return jsonify({"ok": False, "error": "missing_code"}), 400

    allowed, retry_after = _check_throttle(station.hostname)
    if not allowed:
        return jsonify({
            "ok": False,
            "error": "too_many_attempts",
            "retry_after_seconds": retry_after,
        }), 429

    if station.status == "out_of_service":
        return jsonify({"ok": False, "error": "station_out_of_service"}), 403

    # Maintenance (requested/active) or an outstanding reset/reboot command
    # is a session-admission lock, checked ahead of the update lock — an
    # admin actively working the station always takes priority over a queued
    # update. Reject here even if a stale client UI still shows the
    # code-entry screen (defense in depth alongside the client-side check).
    if station.maintenance_requested or station.maintenance_active or station.pending_command_type:
        return jsonify({"ok": False, "error": "station_maintenance"}), 403

    if station.desired_client_version:
        return jsonify({"ok": False, "error": "station_updating"}), 403

    now = datetime.now(timezone.utc)

    session = Session.query.filter_by(
        code_display=code,
        status="created",
    ).first()

    if not session:
        _record_failure(station.hostname)
        return jsonify({"ok": False, "error": "invalid_or_expired_code"})

    exp = session.activation_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now > exp:
        session.status = "expired"
        db.session.commit()
        _record_failure(station.hostname)
        return jsonify({"ok": False, "error": "invalid_or_expired_code"})

    _clear_failures(station.hostname)
    return jsonify({
        "ok": True,
        "session_id": session.id,
        "duration_minutes": session.duration_minutes,
        "warning_minutes": Setting.get_int(
            "warning_minutes",
            current_app.config["SESSION_WARNING_MINUTES"]
        ),
    })


@api_bp.route("/session/start", methods=["POST"])
@station_required
def session_start(station):
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")

    if not session_id:
        return jsonify({"ok": False, "error": "missing_session_id"}), 400

    if station.status == "out_of_service":
        return jsonify({"ok": False, "error": "station_out_of_service"}), 403

    # See session_validate above — maintenance/pending command and update
    # locks both block new admission.
    if station.maintenance_requested or station.maintenance_active or station.pending_command_type:
        return jsonify({"ok": False, "error": "station_maintenance"}), 403

    if station.desired_client_version:
        return jsonify({"ok": False, "error": "station_updating"}), 403

    if station.current_session_id:
        return jsonify({"ok": False, "error": "station_already_in_session"}), 409

    session = db.session.get(Session, session_id)
    if not session or session.status != "created":
        return jsonify({"ok": False, "error": "session_not_found_or_not_startable"}), 404

    now = datetime.now(timezone.utc)
    exp = session.activation_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now > exp:
        session.status = "expired"
        db.session.commit()
        return jsonify({"ok": False, "error": "code_expired"}), 409

    session.status = "active"
    session.started_at = now
    session.expires_at = now + timedelta(minutes=session.duration_minutes)
    session.station_id = station.id

    station.status = "in_use"
    station.current_session_id = session.id
    station.last_seen = now

    _log_event(session.id, station.id, "code_started",
               f"Session started on {station.hostname}.")
    db.session.commit()

    return jsonify({
        "ok": True,
        "expires_at": session.expires_at.strftime("%Y-%m-%dT%H:%M:%S"),
    })


@api_bp.route("/session/open-start", methods=["POST"])
@station_required
def session_open_start(station):
    if not Setting.get_bool("open_lab_mode", False):
        return jsonify({"ok": False, "error": "open_lab_mode_disabled"}), 403

    if station.status == "out_of_service":
        return jsonify({"ok": False, "error": "station_out_of_service"}), 403

    # See session_validate above — maintenance/pending command and update
    # locks both block new admission, including open-mode admission.
    if station.maintenance_requested or station.maintenance_active or station.pending_command_type:
        return jsonify({"ok": False, "error": "station_maintenance"}), 403

    if station.desired_client_version:
        return jsonify({"ok": False, "error": "station_updating"}), 403

    if station.current_session_id:
        return jsonify({"ok": False, "error": "station_already_in_session"}), 409

    duration = Setting.get_int("open_session_duration_minutes", 120)
    now = datetime.now(timezone.utc)

    session = Session(
        code_display="OPEN",
        code_hash=generate_password_hash("OPEN"),
        status="active",
        duration_minutes=duration,
        created_by_user_id=None,
        activation_expires_at=now,
        started_at=now,
        expires_at=now + timedelta(minutes=duration),
    )
    db.session.add(session)
    db.session.flush()

    session.station_id = station.id
    station.status = "in_use"
    station.current_session_id = session.id
    station.last_seen = now

    _log_event(session.id, station.id, "code_started", "Open lab session started.")
    db.session.commit()

    return jsonify({
        "ok": True,
        "session_id": session.id,
        "expires_at": session.expires_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "open_mode": True,
    })


@api_bp.route("/session/end", methods=["POST"])
@station_required
def session_end(station):
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    end_reason = data.get("end_reason", "expired")

    if not session_id:
        return jsonify({"ok": False, "error": "missing_session_id"}), 400

    session = db.session.get(Session, session_id)
    if not session:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    if session.station_id is not None and session.station_id != station.id:
        return jsonify({"ok": False, "error": "session_not_assigned_to_station"}), 403

    if session.status == "active":
        now = datetime.now(timezone.utc)
        session.status = end_reason if end_reason in (
            "expired", "ended_by_staff", "failed"
        ) else "expired"
        session.ended_at = now
        session.end_reason = end_reason

        if end_reason == "expired":
            evt = "session_expired"
        elif end_reason == "failed":
            evt = "session_failed"
        else:
            evt = "session_ended_by_staff"
        _log_event(session.id, station.id, evt, f"Ended: {end_reason}.")

    # Release the station regardless, but keep needs_attention / out_of_service
    # and also mark needs_attention for failed sessions so a script failure
    # that called /api/session/end with reason="failed" shows on the dashboard.
    if station.current_session_id == session_id:
        station.current_session_id = None
    if station.status not in ("out_of_service", "needs_attention"):
        if end_reason == "failed":
            station.status = "needs_attention"
        else:
            station.status = "available"
    station.last_seen = datetime.now(timezone.utc)

    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/session/status/<int:session_id>", methods=["GET"])
@station_required
def session_status(station, session_id):
    """
    Polled by the client during active sessions so staff actions (extend,
    end) are reflected on the workstation without waiting for local expiry.
    """
    session = db.session.get(Session, session_id)
    if not session:
        return jsonify({"ok": False, "error": "session_not_found"}), 404

    if session.station_id is not None and session.station_id != station.id:
        return jsonify({"ok": False, "error": "session_not_assigned_to_station"}), 403

    expires_str = (
        session.expires_at.strftime("%Y-%m-%dT%H:%M:%S")
        if session.expires_at else None
    )

    station_status = station.status
    station.last_seen = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({
        "ok": True,
        "session_id": session.id,
        "status": session.status,
        "expires_at": expires_str,
        "server_time": _server_time(),
        "station_status": station_status,
        "open_mode": session.code_display == "OPEN",
    })


@api_bp.route("/session/extend-by-code", methods=["POST"])
@station_required
def session_extend_by_code(station):
    """
    Consume an unused session code to extend the currently active session.
    The extension code's duration_minutes is added to the active session's
    expiry. The extension code is immediately consumed so it cannot be reused.
    """
    data = request.get_json(silent=True) or {}
    active_session_id = data.get("session_id")
    extension_code = (data.get("code") or "").strip()

    if not active_session_id or not extension_code:
        return jsonify({"ok": False, "error": "missing_fields"}), 400

    allowed, retry_after = _check_throttle(station.hostname)
    if not allowed:
        return jsonify({
            "ok": False,
            "error": "too_many_attempts",
            "retry_after_seconds": retry_after,
        }), 429

    active = db.session.get(Session, active_session_id)
    if not active or active.status != "active":
        return jsonify({"ok": False, "error": "session_not_active"}), 409

    if active.station_id != station.id:
        return jsonify({"ok": False, "error": "session_not_assigned_to_station"}), 403

    now = datetime.now(timezone.utc)

    ext = Session.query.filter_by(
        code_display=extension_code,
        status="created",
    ).first()

    if not ext:
        _record_failure(station.hostname)
        return jsonify({"ok": False, "error": "invalid_or_expired_code"})

    exp = ext.activation_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now > exp:
        ext.status = "expired"
        db.session.commit()
        return jsonify({"ok": False, "error": "invalid_or_expired_code"})

    # Consume the extension code — never becomes an active session
    ext.status = "used_for_extension"
    ext.started_at = now
    ext.expires_at = now
    ext.ended_at = now
    ext.end_reason = "used_for_extension"
    ext.station_id = station.id

    # Extend the running session
    active.expires_at = active.expires_at + timedelta(minutes=ext.duration_minutes)

    _log_event(active.id, station.id, "session_extended",
               f"Extended by {ext.duration_minutes} min via code {ext.code_display}.")
    _log_event(ext.id, station.id, "code_used_for_extension",
               f"Used to extend session {active.id}.")

    _clear_failures(station.hostname)
    db.session.commit()

    return jsonify({
        "ok": True,
        "expires_at": active.expires_at.strftime("%Y-%m-%dT%H:%M:%S"),
        "added_minutes": ext.duration_minutes,
    })


@api_bp.route("/session/event", methods=["POST"])
@station_required
def session_event(station):
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    event_type = data.get("event_type", "client_heartbeat")
    message = data.get("message")

    allowed_event_types = {
        "session_warning", "profile_reset_started", "profile_reset_success",
        "profile_reset_failed", "client_heartbeat", "client_error",
        "start_script_failed", "end_script_failed", "reset_script_failed",
        "maintenance_enter_failed", "maintenance_exit_failed",
    }
    if event_type not in allowed_event_types:
        event_type = "client_error"

    if session_id is not None:
        target = db.session.get(Session, session_id)
        if target and target.station_id is not None and target.station_id != station.id:
            return jsonify({"ok": False, "error": "session_not_assigned_to_station"}), 403

    _log_event(session_id, station.id, event_type, message)

    # maintenance_enter_failed is logged for visibility but is not fatal —
    # the display switch itself may have failed while the (more important)
    # admission lock still holds, so the station isn't actually unsafe. See
    # gracelab_client.py's _do_enter_maintenance for the full reasoning.
    station_fatal = {
        "start_script_failed", "end_script_failed", "reset_script_failed",
        "profile_reset_failed", "maintenance_exit_failed",
    }
    if event_type in station_fatal:
        station.status = "needs_attention"

    station.last_seen = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"ok": True})
