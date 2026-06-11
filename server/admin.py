from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, IntegerField, SelectField, BooleanField, TextAreaField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, EqualTo
from extensions import db
from models import AuditLog, Setting, User
from audit import log_audit

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_admin():
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard.index"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@admin_bp.route("/audit")
@login_required
@_admin_required
def audit():
    page = request.args.get("page", 1, type=int)
    q = AuditLog.query.order_by(AuditLog.created_at.desc())

    filter_action = request.args.get("action", "").strip()
    filter_actor = request.args.get("actor", "").strip()
    if filter_action:
        q = q.filter(AuditLog.action == filter_action)
    if filter_actor:
        q = q.filter(AuditLog.actor_username == filter_actor)

    entries = q.paginate(page=page, per_page=50, error_out=False)
    actions = [r[0] for r in db.session.query(AuditLog.action).distinct().order_by(AuditLog.action).all()]
    return render_template("audit.html", entries=entries,
                           filter_action=filter_action, filter_actor=filter_actor,
                           actions=actions)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class SettingsForm(FlaskForm):
    organization_name = StringField(
        "Organization Name",
        validators=[DataRequired(), Length(1, 128)],
    )
    ticket_footer = StringField(
        "Session Ticket Footer",
        validators=[Optional(), Length(0, 256)],
    )
    default_session_minutes = IntegerField(
        "Default Session Duration (minutes)",
        validators=[DataRequired(), NumberRange(min=5, max=480)],
    )
    code_expiration_minutes = IntegerField(
        "Session Code Valid For (minutes)",
        validators=[DataRequired(), NumberRange(min=60, max=4320)],
    )
    warning_minutes = IntegerField(
        "Warn Guest Before Session Ends (minutes)",
        validators=[DataRequired(), NumberRange(min=1, max=60)],
    )
    station_offline_after_seconds = IntegerField(
        "Mark Station Offline After (seconds)",
        validators=[DataRequired(), NumberRange(min=30, max=600)],
    )
    batch_code_max_count = IntegerField(
        "Max Batch Code Count",
        validators=[DataRequired(), NumberRange(min=1, max=100)],
    )
    client_updates_enabled = BooleanField("Enable Client Auto-Updates")
    client_update_policy = SelectField(
        "Client Update Policy",
        choices=[
            ("disabled", "Disabled"),
            ("manual", "Manual (dashboard only)"),
            ("idle_only", "Idle Only (recommended)"),
            ("force", "Force (even during sessions)"),
        ],
    )
    client_update_channel = SelectField(
        "Client Update Channel",
        choices=[("stable", "Stable"), ("beta", "Beta")],
    )
    client_min_supported_version = StringField(
        "Minimum Supported Client Version",
        validators=[Optional(), Length(0, 32)],
    )
    client_stable_version = StringField(
        "Published Stable Version",
        validators=[Optional(), Length(0, 32)],
    )
    client_beta_version = StringField(
        "Published Beta Version",
        validators=[Optional(), Length(0, 32)],
    )
    open_lab_mode = BooleanField("Enable Open Lab Mode (no session codes required)")
    open_session_duration_minutes = IntegerField(
        "Open Session Duration (minutes)",
        validators=[Optional(), NumberRange(min=15, max=480)],
    )
    tos_text = TextAreaField(
        "Terms of Service",
        validators=[Optional(), Length(0, 2000)],
    )
    submit = SubmitField("Save Settings")


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@_admin_required
def settings():
    cfg = current_app.config
    form = SettingsForm()

    if request.method == "GET":
        form.organization_name.data = Setting.get("organization_name", cfg["ORGANIZATION_NAME"])
        form.ticket_footer.data = Setting.get("ticket_footer", cfg["TICKET_FOOTER"])
        form.default_session_minutes.data = Setting.get_int("default_session_minutes", cfg["DEFAULT_SESSION_MINUTES"])
        form.code_expiration_minutes.data = Setting.get_int("code_expiration_minutes", cfg["CODE_EXPIRATION_MINUTES"])
        form.warning_minutes.data = Setting.get_int("warning_minutes", cfg["SESSION_WARNING_MINUTES"])
        form.station_offline_after_seconds.data = Setting.get_int(
            "station_offline_after_seconds", cfg["STATION_OFFLINE_AFTER_SECONDS"]
        )
        form.batch_code_max_count.data = Setting.get_int("batch_code_max_count", 36)
        form.client_updates_enabled.data = Setting.get_bool("client_updates_enabled", False)
        form.client_update_policy.data = Setting.get("client_update_policy", "idle_only")
        form.client_update_channel.data = Setting.get("client_update_channel", "stable")
        form.client_min_supported_version.data = Setting.get("client_min_supported_version", "")
        form.client_stable_version.data = Setting.get("client_stable_version", "")
        form.client_beta_version.data = Setting.get("client_beta_version", "")
        form.open_lab_mode.data = Setting.get_bool("open_lab_mode", False)
        form.open_session_duration_minutes.data = Setting.get_int("open_session_duration_minutes", 120)
        form.tos_text.data = Setting.get("tos_text", "")

    if form.validate_on_submit():
        warn = form.warning_minutes.data
        default_dur = form.default_session_minutes.data
        if warn >= default_dur:
            flash("Warning minutes must be less than the default session duration.", "danger")
            return render_template("settings.html", form=form)

        changed = {}
        pairs = [
            ("organization_name",           form.organization_name.data),
            ("ticket_footer",               form.ticket_footer.data or ""),
            ("default_session_minutes",     str(default_dur)),
            ("code_expiration_minutes",     str(form.code_expiration_minutes.data)),
            ("warning_minutes",             str(warn)),
            ("station_offline_after_seconds", str(form.station_offline_after_seconds.data)),
            ("batch_code_max_count",        str(form.batch_code_max_count.data)),
            ("client_updates_enabled",      "true" if form.client_updates_enabled.data else "false"),
            ("client_update_policy",        form.client_update_policy.data),
            ("client_update_channel",       form.client_update_channel.data),
            ("client_min_supported_version", form.client_min_supported_version.data or ""),
            ("client_stable_version",        form.client_stable_version.data or ""),
            ("client_beta_version",          form.client_beta_version.data or ""),
            ("open_lab_mode",               "true" if form.open_lab_mode.data else "false"),
            ("open_session_duration_minutes", str(form.open_session_duration_minutes.data or 120)),
            ("tos_text",                    form.tos_text.data or ""),
        ]
        for key, new_val in pairs:
            old_val = Setting.get(key)
            if old_val != new_val:
                changed[key] = {"from": old_val, "to": new_val}
            setting_row = Setting.query.filter_by(key=key).first()
            if setting_row:
                setting_row.value = new_val
            else:
                db.session.add(Setting(key=key, value=new_val))

        if changed:
            log_audit("settings_changed", details={"changed": changed})
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))

    return render_template("settings.html", form=form)


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

class CreateUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(3, 64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(8, 128)])
    confirm  = PasswordField("Confirm Password", validators=[DataRequired(), EqualTo("password", message="Passwords must match.")])
    role     = SelectField("Role", choices=[("staff", "Staff"), ("admin", "Admin")])
    submit   = SubmitField("Create User")


class EditUserForm(FlaskForm):
    role     = SelectField("Role", choices=[("staff", "Staff"), ("admin", "Admin")])
    active   = BooleanField("Active")
    new_password = PasswordField("New Password (leave blank to keep current)", validators=[Optional(), Length(8, 128)])
    submit   = SubmitField("Save Changes")


@admin_bp.route("/users")
@login_required
@_admin_required
def list_users():
    users = User.query.order_by(User.username).all()
    return render_template("users.html", users=users)


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@_admin_required
def create_user():
    form = CreateUserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data.strip()).first():
            flash("That username is already taken.", "danger")
            return render_template("user_form.html", form=form, title="New User")
        user = User(username=form.username.data.strip(), role=form.role.data)
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.flush()
        log_audit("user_created", target_type="user", target_id=user.id,
                  details={"username": user.username, "role": user.role})
        db.session.commit()
        flash(f"User '{user.username}' created.", "success")
        return redirect(url_for("admin.list_users"))
    return render_template("user_form.html", form=form, title="New User")


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@_admin_required
def edit_user(user_id):
    user = db.get_or_404(User, user_id)
    form = EditUserForm()
    if request.method == "GET":
        form.role.data   = user.role
        form.active.data = user.active
    if form.validate_on_submit():
        if user.id == current_user.id and form.role.data != "admin":
            flash("You cannot remove your own admin role.", "danger")
            return render_template("user_form.html", form=form, title=f"Edit — {user.username}", user=user)
        changed = {}
        if user.role != form.role.data:
            changed["role"] = {"from": user.role, "to": form.role.data}
            user.role = form.role.data
        if user.active != form.active.data:
            changed["active"] = {"from": user.active, "to": form.active.data}
            user.active = form.active.data
        if form.new_password.data:
            user.set_password(form.new_password.data)
            changed["password"] = "reset"
        if changed:
            log_audit("user_edited", target_type="user", target_id=user.id,
                      details={"username": user.username, "changed": changed})
        db.session.commit()
        flash(f"User '{user.username}' updated.", "success")
        return redirect(url_for("admin.list_users"))
    return render_template("user_form.html", form=form, title=f"Edit — {user.username}", user=user)


@admin_bp.route("/users/<int:user_id>/deactivate", methods=["POST"])
@login_required
@_admin_required
def deactivate_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash("You cannot deactivate your own account.", "danger")
        return redirect(url_for("admin.list_users"))
    user.active = False
    log_audit("user_deactivated", target_type="user", target_id=user.id,
              details={"username": user.username})
    db.session.commit()
    flash(f"User '{user.username}' deactivated.", "info")
    return redirect(url_for("admin.list_users"))
