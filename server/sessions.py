import secrets
import string
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, Response
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import IntegerField, TextAreaField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Optional
from werkzeug.security import generate_password_hash
from extensions import db
from models import Session, SessionEvent, Setting
from audit import log_audit

sessions_bp = Blueprint("sessions", __name__, url_prefix="/admin/sessions")


class NewSessionForm(FlaskForm):
    duration_minutes = IntegerField(
        "Duration (minutes)",
        validators=[DataRequired(), NumberRange(min=5, max=480)],
    )
    notes = TextAreaField("Notes (optional)", validators=[Optional()])
    submit = SubmitField("Generate Session Code")


class BatchSessionForm(FlaskForm):
    quantity = IntegerField(
        "Number of codes",
        validators=[DataRequired(), NumberRange(min=1, max=100)],
    )
    duration_minutes = IntegerField(
        "Duration (minutes)",
        validators=[DataRequired(), NumberRange(min=5, max=480)],
    )
    submit = SubmitField("Generate & Print")


def generate_code():
    digits = [secrets.choice(string.digits) for _ in range(6)]
    return f"{''.join(digits[:3])}-{''.join(digits[3:])}"


def generate_unique_code():
    for _ in range(20):
        code = generate_code()
        existing = Session.query.filter(
            Session.code_display == code,
            Session.status.in_(["created", "active"]),
        ).first()
        if not existing:
            return code
    raise RuntimeError("Could not generate a unique session code after 20 attempts.")


@sessions_bp.route("/")
@login_required
def list_sessions():
    page = request.args.get("page", 1, type=int)
    sessions = (
        Session.query
        .order_by(Session.created_at.desc())
        .paginate(page=page, per_page=25, error_out=False)
    )
    return render_template("sessions.html", sessions=sessions)


@sessions_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_session():
    form = NewSessionForm()

    default_minutes = Setting.get_int("default_session_minutes",
                                     current_app.config["DEFAULT_SESSION_MINUTES"])
    if request.method == "GET":
        form.duration_minutes.data = default_minutes

    if form.validate_on_submit():
        code = generate_unique_code()
        code_expiration = Setting.get_int("code_expiration_minutes",
                                         current_app.config["CODE_EXPIRATION_MINUTES"])
        now = datetime.now(timezone.utc)

        session = Session(
            code_display=code,
            code_hash=generate_password_hash(code),
            status="created",
            duration_minutes=form.duration_minutes.data,
            created_by_user_id=current_user.id,
            activation_expires_at=now + timedelta(minutes=code_expiration),
            notes=form.notes.data or None,
        )
        db.session.add(session)
        db.session.flush()

        db.session.add(SessionEvent(
            session_id=session.id,
            event_type="code_created",
            message=f"Session code created by {current_user.username}.",
        ))
        log_audit("session_code_created", target_type="session", target_id=session.id,
                  session_id=session.id,
                  details={"duration_minutes": form.duration_minutes.data})
        db.session.commit()

        flash(f"Session code {code} created.", "success")
        return redirect(url_for("sessions.ticket", session_id=session.id))

    return render_template("session_new.html", form=form)


@sessions_bp.route("/<int:session_id>")
@login_required
def detail(session_id):
    session = db.get_or_404(Session, session_id)
    events = session.events.order_by(SessionEvent.created_at.asc()).all()
    return render_template("session_detail.html", session=session, events=events)


@sessions_bp.route("/<int:session_id>/ticket")
@login_required
def ticket(session_id):
    session = db.get_or_404(Session, session_id)
    org_name = Setting.get("organization_name", current_app.config["ORGANIZATION_NAME"])
    ticket_footer = Setting.get("ticket_footer", current_app.config["TICKET_FOOTER"])
    return render_template("session_ticket.html", session=session,
                           org_name=org_name, ticket_footer=ticket_footer)


@sessions_bp.route("/<int:session_id>/cancel", methods=["POST"])
@login_required
def cancel(session_id):
    session = db.get_or_404(Session, session_id)
    if session.status != "created":
        flash("Only unused codes can be cancelled.", "warning")
        return redirect(url_for("sessions.detail", session_id=session_id))

    session.status = "cancelled"
    session.ended_at = datetime.now(timezone.utc)
    db.session.add(SessionEvent(
        session_id=session.id,
        event_type="code_cancelled",
        message=f"Cancelled by {current_user.username}.",
    ))
    log_audit("session_code_cancelled", target_type="session", target_id=session.id,
              session_id=session.id)
    db.session.commit()
    flash(f"Session code {session.code_display} cancelled.", "info")
    return redirect(url_for("sessions.list_sessions"))


@sessions_bp.route("/<int:session_id>/extend", methods=["POST"])
@login_required
def extend(session_id):
    session = db.get_or_404(Session, session_id)
    if session.status != "active":
        flash("Only active sessions can be extended.", "warning")
        return redirect(url_for("sessions.detail", session_id=session_id))

    minutes = request.form.get("minutes", 15, type=int)
    if minutes not in (15, 30):
        minutes = 15

    session.expires_at = session.expires_at + timedelta(minutes=minutes)
    db.session.add(SessionEvent(
        session_id=session.id,
        event_type="session_extended",
        message=f"Extended by {minutes} minutes by {current_user.username}.",
    ))
    log_audit("session_extended_by_staff", target_type="session", target_id=session.id,
              session_id=session.id,
              station_id=session.station_id,
              details={"minutes_added": minutes})
    db.session.commit()
    flash(f"Session extended by {minutes} minutes.", "success")
    return redirect(url_for("dashboard.index"))


@sessions_bp.route("/batch", methods=["GET", "POST"])
@login_required
def batch():
    form = BatchSessionForm()

    default_minutes = Setting.get_int("default_session_minutes",
                                     current_app.config["DEFAULT_SESSION_MINUTES"])
    if request.method == "GET":
        form.quantity.data = 9
        form.duration_minutes.data = default_minutes

    if form.validate_on_submit():
        max_count = Setting.get_int("batch_code_max_count", 36)
        if form.quantity.data > max_count:
            flash(f"Batch size cannot exceed {max_count} (set in Settings).", "danger")
            return render_template("batch_form.html", form=form)

        code_expiration = Setting.get_int("code_expiration_minutes",
                                         current_app.config["CODE_EXPIRATION_MINUTES"])
        now = datetime.now(timezone.utc)
        sessions = []

        for _ in range(form.quantity.data):
            code = generate_unique_code()
            s = Session(
                code_display=code,
                code_hash=generate_password_hash(code),
                status="created",
                duration_minutes=form.duration_minutes.data,
                created_by_user_id=current_user.id,
                activation_expires_at=now + timedelta(minutes=code_expiration),
            )
            db.session.add(s)
            db.session.flush()
            db.session.add(SessionEvent(
                session_id=s.id,
                event_type="code_created",
                message=f"Batch code created by {current_user.username}.",
            ))
            sessions.append(s)

        log_audit("session_codes_batch_created",
                  details={"quantity": form.quantity.data,
                           "duration_minutes": form.duration_minutes.data})
        db.session.commit()

        org_name = Setting.get("organization_name", current_app.config["ORGANIZATION_NAME"])
        ticket_footer = Setting.get("ticket_footer", current_app.config["TICKET_FOOTER"])

        html = render_template("batch_print.html", sessions=sessions,
                               org_name=org_name, ticket_footer=ticket_footer)

        from weasyprint import HTML as WeasyprintHTML
        pdf = WeasyprintHTML(string=html, base_url=request.host_url).write_pdf()

        return Response(
            pdf,
            mimetype="application/pdf",
            headers={"Content-Disposition": "inline; filename=session-codes.pdf"},
        )

    return render_template("batch_form.html", form=form)


@sessions_bp.route("/<int:session_id>/end", methods=["POST"])
@login_required
def end(session_id):
    session = db.get_or_404(Session, session_id)
    if session.status != "active":
        flash("Only active sessions can be ended.", "warning")
        return redirect(url_for("sessions.detail", session_id=session_id))

    now = datetime.now(timezone.utc)
    session.status = "ended_by_staff"
    session.ended_at = now
    session.end_reason = "ended_by_staff"
    db.session.add(SessionEvent(
        session_id=session.id,
        event_type="session_ended_by_staff",
        message=f"Ended by {current_user.username}.",
    ))
    log_audit("session_ended_by_staff", target_type="session", target_id=session.id,
              session_id=session.id,
              station_id=session.station_id)

    if session.station:
        session.station.current_session_id = None
        if session.station.status not in ("out_of_service", "needs_attention"):
            session.station.status = "available"

    db.session.commit()
    flash(f"Session {session.code_display} ended.", "info")
    return redirect(url_for("dashboard.index"))
