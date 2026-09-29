from datetime import datetime, timezone
from urllib.parse import urlsplit
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Length
from extensions import db, login_manager
from models import User
from audit import log_audit
from limiter import limiter

auth_bp = Blueprint("auth", __name__)


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(1, 64)])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Stay logged in")
    submit = SubmitField("Log In")


@login_manager.user_loader
def load_user(user_id):
    try:
        user = db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
    # Check on every request, including sessions restored from remember cookies.
    return user if user and user.active else None


def _local_redirect(target):
    """Only accept an absolute local path, never a browser-normalized host."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    if "\\" in target or any(ord(char) < 32 or ord(char) == 127 for char in target):
        return False
    parsed = urlsplit(target)
    return not parsed.scheme and not parsed.netloc


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute; 30 per hour")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and user.active and user.check_password(form.password.data):
            login_user(user, remember=form.remember_me.data)
            user.last_login_at = datetime.now(timezone.utc)
            log_audit("login_success", target_type="user", target_id=user.id,
                      details={"username": user.username})
            db.session.commit()
            next_page = request.args.get("next")
            if not _local_redirect(next_page):
                next_page = url_for("dashboard.index")
            return redirect(next_page)
        log_audit("login_failed",
                  details={"username": form.username.data})
        db.session.commit()
        flash("Invalid username or password.", "danger")

    return render_template("login.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    log_audit("logout", target_type="user", target_id=current_user.id,
              details={"username": current_user.username})
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
