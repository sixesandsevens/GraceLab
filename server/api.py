from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import check_password_hash
from extensions import db, csrf
from models import Station, Session, SessionEvent, Setting

api_bp = Blueprint("api", __name__, url_prefix="/api")
csrf.exempt(api_bp)


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

    # Only accept known statuses from the client
    allowed = {"available", "in_use", "needs_attention"}
    if reported_status not in allowed:
        reported_status = "available"

    _update_station_seen(station, reported_status=reported_status)
    db.session.commit()

    return jsonify({
        "ok": True,
        "server_time": _server_time(),
        "station_status": station.status,
    })


@api_bp.route("/station/config", methods=["GET"])
@station_required
def station_config(station):
    _update_station_seen(station)
    db.session.commit()

    return jsonify({
        "ok": True,
        "warning_minutes": int(Setting.get(
            "warning_minutes",
            current_app.config["SESSION_WARNING_MINUTES"]
        )),
        "organization_name": Setting.get(
            "organization_name",
            current_app.config["ORGANIZATION_NAME"]
        ),
    })


@api_bp.route("/session/validate", methods=["POST"])
@station_required
def session_validate(station):
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()

    if not code:
        return jsonify({"ok": False, "error": "missing_code"}), 400

    # Rate-limit surface is small (local LAN only), but we still reject blanks/garbage fast.
    # Full rate limiting can be added in Phase 3 if needed.

    now = datetime.now(timezone.utc)

    session = Session.query.filter_by(
        code_display=code,
        status="created",
    ).first()

    if not session:
        return jsonify({"ok": False, "error": "invalid_or_expired_code"})

    # Check activation window hasn't elapsed
    exp = session.activation_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now > exp:
        session.status = "expired"
        db.session.commit()
        return jsonify({"ok": False, "error": "invalid_or_expired_code"})

    if station.status == "out_of_service":
        return jsonify({"ok": False, "error": "station_out_of_service"}), 403

    return jsonify({
        "ok": True,
        "session_id": session.id,
        "duration_minutes": session.duration_minutes,
        "warning_minutes": int(Setting.get(
            "warning_minutes",
            current_app.config["SESSION_WARNING_MINUTES"]
        )),
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
    }
    if event_type not in allowed_event_types:
        event_type = "client_error"

    if session_id is not None:
        target = db.session.get(Session, session_id)
        if target and target.station_id is not None and target.station_id != station.id:
            return jsonify({"ok": False, "error": "session_not_assigned_to_station"}), 403

    _log_event(session_id, station.id, event_type, message)

    station_fatal = {
        "start_script_failed", "end_script_failed", "reset_script_failed",
        "profile_reset_failed",
    }
    if event_type in station_fatal:
        station.status = "needs_attention"

    station.last_seen = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify({"ok": True})
