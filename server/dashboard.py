from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template
from flask_login import login_required
from models import Session, Station, SessionEvent

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@dashboard_bp.route("/dashboard")
@login_required
def index():
    now = datetime.now(timezone.utc)

    active_sessions = (
        Session.query
        .filter_by(status="active")
        .order_by(Session.expires_at.asc())
        .all()
    )

    stations = Station.query.order_by(Station.display_name.asc()).all()

    recent_sessions = (
        Session.query
        .filter(Session.status != "created")
        .order_by(Session.created_at.desc())
        .limit(20)
        .all()
    )

    recent_events = (
        SessionEvent.query
        .order_by(SessionEvent.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "dashboard.html",
        active_sessions=active_sessions,
        stations=stations,
        recent_sessions=recent_sessions,
        recent_events=recent_events,
        now=now,
    )
