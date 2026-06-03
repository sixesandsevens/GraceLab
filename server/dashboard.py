from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template
from flask_login import login_required
from models import Session, Station, SessionEvent

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    now = datetime.utcnow()

    active_sessions = (
        Session.query
        .filter_by(status="active")
        .order_by(Session.expires_at.asc())
        .all()
    )

    # Unused codes staff may need to reprint or cancel
    pending_codes = (
        Session.query
        .filter_by(status="created")
        .order_by(Session.created_at.desc())
        .all()
    )

    stations = Station.query.order_by(Station.display_name.asc()).all()

    recent_sessions = (
        Session.query
        .filter(Session.status.notin_(["created"]))
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
        now=now,
    )
