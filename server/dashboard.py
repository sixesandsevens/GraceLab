from datetime import datetime, timezone
from flask import Blueprint, render_template
from flask_login import login_required
from extensions import db
from models import Session, Station, SessionEvent

dashboard_bp = Blueprint("dashboard", __name__)


def expire_overdue_sessions():
    """
    Mark any active session whose expires_at is in the past as expired,
    and release its station. Called before rendering any staff-facing view
    so the dashboard never shows ghost sessions from crashed clients.
    """
    now = datetime.now(timezone.utc)
    overdue = (
        Session.query
        .filter(Session.status == "active")
        .filter(Session.expires_at < now)
        .all()
    )
    for s in overdue:
        s.status = "expired"
        s.ended_at = now
        s.end_reason = "server_expired"
        db.session.add(SessionEvent(
            session_id=s.id,
            station_id=s.station_id,
            event_type="session_expired",
            message="Expired by server cleanup (client did not report end).",
        ))
        if s.station and s.station.status not in ("out_of_service", "needs_attention"):
            s.station.current_session_id = None
            s.station.status = "available"
    if overdue:
        db.session.commit()


@dashboard_bp.route("/")
@login_required
def index():
    expire_overdue_sessions()
    now = datetime.now(timezone.utc)

    active_sessions = (
        Session.query
        .filter_by(status="active")
        .order_by(Session.expires_at.asc())
        .all()
    )

    pending_codes = (
        Session.query
        .filter_by(status="created")
        .order_by(Session.created_at.desc())
        .all()
    )

    stations = Station.query.order_by(Station.display_name.asc()).all()

    # Exclude used_for_extension records — they are not real sessions
    recent_sessions = (
        Session.query
        .filter(Session.status.notin_(["created", "used_for_extension"]))
        .order_by(Session.created_at.desc())
        .limit(20)
        .all()
    )

    return render_template(
        "dashboard.html",
        active_sessions=active_sessions,
        pending_codes=pending_codes,
        stations=stations,
        recent_sessions=recent_sessions,
        now=now.replace(tzinfo=None),  # SQLite returns naive datetimes; match for template comparisons
    )
