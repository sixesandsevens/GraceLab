import secrets
from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, Regexp
from werkzeug.security import generate_password_hash
from extensions import db
from models import Station, SessionEvent
from audit import log_audit

stations_bp = Blueprint("stations", __name__, url_prefix="/admin/stations")

_HOSTNAME_RE = r'^[A-Za-z0-9][A-Za-z0-9\-]{0,62}$'


class NewStationForm(FlaskForm):
    hostname = StringField(
        "Hostname",
        validators=[DataRequired(), Length(1, 64), Regexp(_HOSTNAME_RE,
            message="Hostname must contain only letters, digits, and hyphens.")],
    )
    display_name = StringField("Display Name", validators=[DataRequired(), Length(1, 64)])
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Register Station")


def _admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin():
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return decorated


@stations_bp.route("/")
@login_required
def list_stations():
    stations = Station.query.order_by(Station.display_name.asc()).all()
    offline_threshold = current_app.config["STATION_OFFLINE_AFTER_SECONDS"]
    now = datetime.now(timezone.utc)

    changed = False
    for st in stations:
        if st.status in ("available", "in_use", "needs_attention") and st.last_seen:
            last = st.last_seen
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if (now - last).total_seconds() > offline_threshold:
                st.status = "offline"
                changed = True
    if changed:
        db.session.commit()

    return render_template("stations.html", stations=stations)


@stations_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_station():
    if not current_user.is_admin():
        flash("Admin access required.", "danger")
        return redirect(url_for("stations.list_stations"))

    form = NewStationForm()
    token = None

    if form.validate_on_submit():
        existing = Station.query.filter_by(hostname=form.hostname.data).first()
        if existing:
            flash("A station with that hostname already exists.", "danger")
            return render_template("station_new.html", form=form, token=None)

        token = secrets.token_urlsafe(32)
        station = Station(
            hostname=form.hostname.data,
            display_name=form.display_name.data,
            station_token_hash=generate_password_hash(token),
            status="offline",
            notes=form.notes.data or None,
        )
        db.session.add(station)
        db.session.flush()

        db.session.add(SessionEvent(
            station_id=station.id,
            event_type="station_registered",
            message=f"Registered by {current_user.username}.",
        ))
        log_audit("station_registered", target_type="station", target_id=station.id,
                  station_id=station.id,
                  details={"hostname": station.hostname, "display_name": station.display_name})
        db.session.commit()

        flash(f"Station {station.hostname} registered. Copy the token — it won't be shown again.", "success")
        return render_template("station_new.html", form=NewStationForm(), token=token,
                               new_station=station)

    return render_template("station_new.html", form=form, token=None)


@stations_bp.route("/<int:station_id>/rotate-token", methods=["POST"])
@login_required
@_admin_required
def rotate_token(station_id):
    station = db.get_or_404(Station, station_id)
    token = secrets.token_urlsafe(32)
    station.station_token_hash = generate_password_hash(token)
    db.session.add(SessionEvent(
        station_id=station.id,
        event_type="station_token_rotated",
        message=f"Token rotated by {current_user.username}.",
    ))
    log_audit("station_token_rotated", target_type="station", target_id=station.id,
              station_id=station.id)
    db.session.commit()
    return render_template("station_token_rotated.html", station=station, token=token)


@stations_bp.route("/<int:station_id>/out-of-service", methods=["POST"])
@login_required
def mark_out_of_service(station_id):
    if not current_user.is_admin():
        flash("Admin access required.", "danger")
        return redirect(url_for("stations.list_stations"))

    station = db.get_or_404(Station, station_id)
    station.status = "out_of_service"
    db.session.add(SessionEvent(
        station_id=station.id,
        event_type="station_marked_out_of_service",
        message=f"Marked out of service by {current_user.username}.",
    ))
    log_audit("station_status_changed", target_type="station", target_id=station.id,
              station_id=station.id, details={"status": "out_of_service"})
    db.session.commit()
    flash(f"{station.display_name} marked out of service.", "info")
    return redirect(url_for("stations.list_stations"))


@stations_bp.route("/<int:station_id>/return-to-service", methods=["POST"])
@login_required
def return_to_service(station_id):
    if not current_user.is_admin():
        flash("Admin access required.", "danger")
        return redirect(url_for("stations.list_stations"))

    station = db.get_or_404(Station, station_id)
    station.status = "offline"
    db.session.add(SessionEvent(
        station_id=station.id,
        event_type="station_returned_to_service",
        message=f"Returned to service by {current_user.username}.",
    ))
    log_audit("station_status_changed", target_type="station", target_id=station.id,
              station_id=station.id, details={"status": "offline (returned to service)"})
    db.session.commit()
    flash(f"{station.display_name} returned to service.", "success")
    return redirect(url_for("stations.list_stations"))
