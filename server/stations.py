import os
import secrets
from datetime import datetime, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, Regexp
from werkzeug.security import generate_password_hash
from extensions import db
from models import Station, Session, SessionEvent, AuditLog, Setting
from audit import log_audit
from updates import _package_filename

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
    offline_threshold = Setting.get_int(
        "station_offline_after_seconds",
        current_app.config["STATION_OFFLINE_AFTER_SECONDS"],
    )
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

    updates_enabled = Setting.get_bool("client_updates_enabled", False)
    stable_version = Setting.get("client_stable_version", "")

    return render_template("stations.html", stations=stations,
                           updates_enabled=updates_enabled,
                           stable_version=stable_version)


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


@stations_bp.route("/<int:station_id>/push-update", methods=["POST"])
@login_required
@_admin_required
def push_update(station_id):
    """
    Queue an update for this station. This is admission control, not a
    remote interrupt: the station stops admitting new guest sessions
    immediately, but any session already in progress finishes normally and
    the update installs once the station goes idle. Also doubles as the
    "Retry Update" action — re-pushing after a failure clears the stale
    failed status and re-queues the same target.
    """
    station = db.get_or_404(Station, station_id)
    target = Setting.get("client_stable_version", "")
    if not target:
        flash("No stable version is published in Settings → Client Updates.", "danger")
        return redirect(url_for("stations.list_stations"))

    # Don't admission-lock a station for an update we can't actually serve —
    # mirrors update_check()'s package_not_found defense on the client side.
    updates_dir = current_app.config["UPDATES_DIR"]
    filename = _package_filename(target)
    if not os.path.isfile(os.path.join(updates_dir, filename)):
        flash(f"Cannot queue v{target} for {station.display_name} — package "
              f"{filename} was not found on the server. Upload it in "
              f"Client Updates first.", "danger")
        return redirect(url_for("stations.list_stations"))

    station.desired_client_version = target
    station.client_update_status = None
    station.client_update_error = None
    log_audit("station_update_pushed", target_type="station", target_id=station.id,
              station_id=station.id, details={"target_version": target})
    db.session.commit()
    flash(f"Update to v{target} queued for {station.display_name}. "
          f"New guest sessions are blocked until it installs; any session "
          f"already in progress finishes normally first.", "success")
    return redirect(url_for("stations.list_stations"))


@stations_bp.route("/<int:station_id>/cancel-update", methods=["POST"])
@login_required
@_admin_required
def cancel_update(station_id):
    station = db.get_or_404(Station, station_id)

    if station.client_update_status in ("downloading", "installing"):
        # The client-side installer isn't remotely interruptible, so clearing
        # the lock here would just let a not-yet-finished install race the
        # admission check. Reject rather than attempt an unsafe interruption.
        flash(f"{station.display_name} is already {station.client_update_status} "
              f"v{station.desired_client_version} — cannot safely cancel mid-install. "
              f"Wait for it to finish, then retry if it fails.", "danger")
        return redirect(url_for("stations.list_stations"))

    station.desired_client_version = None
    station.client_update_status = None
    station.client_update_error = None
    log_audit("station_update_cancelled", target_type="station", target_id=station.id,
              station_id=station.id)
    db.session.commit()
    flash(f"Pending update cancelled for {station.display_name}.", "info")
    return redirect(url_for("stations.list_stations"))


@stations_bp.route("/<int:station_id>/delete", methods=["POST"])
@login_required
@_admin_required
def delete_station(station_id):
    station = db.get_or_404(Station, station_id)

    if station.current_session_id or station.status == "in_use":
        flash(f"Cannot delete {station.display_name} — it has an active session.", "danger")
        return redirect(url_for("stations.list_stations"))

    hostname = station.hostname
    display_name = station.display_name

    # Nullify FK references so history is preserved
    SessionEvent.query.filter_by(station_id=station_id).update({"station_id": None})
    Session.query.filter_by(station_id=station_id).update({"station_id": None})
    AuditLog.query.filter_by(station_id=station_id).update({"station_id": None})

    log_audit("station_deleted", target_type="station", target_id=station_id,
              details={"hostname": hostname, "display_name": display_name})
    db.session.delete(station)
    db.session.commit()

    flash(f"Station {display_name} ({hostname}) deleted.", "success")
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
