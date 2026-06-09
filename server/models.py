from datetime import datetime, timezone
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db



class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="staff")  # staff | admin
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    last_login_at = db.Column(db.DateTime, nullable=True)

    sessions_created = db.relationship("Session", back_populates="created_by_user", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == "admin"

    def __repr__(self):
        return f"<User {self.username}>"


class Station(db.Model):
    __tablename__ = "stations"

    id = db.Column(db.Integer, primary_key=True)
    hostname = db.Column(db.String(64), unique=True, nullable=False)
    display_name = db.Column(db.String(64), nullable=False)
    station_token_hash = db.Column(db.String(256), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="offline")
    # available | in_use | offline | out_of_service | needs_attention
    last_seen = db.Column(db.DateTime, nullable=True)
    current_session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    # Client version tracking
    client_version = db.Column(db.String(32), nullable=True)
    client_update_status = db.Column(db.String(32), nullable=True)
    client_update_error = db.Column(db.Text, nullable=True)
    last_update_check_at = db.Column(db.DateTime, nullable=True)
    last_update_started_at = db.Column(db.DateTime, nullable=True)
    last_update_finished_at = db.Column(db.DateTime, nullable=True)
    desired_client_version = db.Column(db.String(32), nullable=True)

    current_session = db.relationship("Session", foreign_keys=[current_session_id])
    events = db.relationship("SessionEvent", back_populates="station", lazy="dynamic")

    def verify_token(self, token):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.station_token_hash, token)

    def __repr__(self):
        return f"<Station {self.hostname}>"


class Session(db.Model):
    __tablename__ = "sessions"

    id = db.Column(db.Integer, primary_key=True)
    code_hash = db.Column(db.String(256), nullable=False)
    code_display = db.Column(db.String(16), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="created")
    # created | active | expired | ended_by_staff | cancelled | failed
    duration_minutes = db.Column(db.Integer, nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    activation_expires_at = db.Column(db.DateTime, nullable=False)
    started_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    ended_at = db.Column(db.DateTime, nullable=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=True)
    end_reason = db.Column(db.String(64), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    created_by_user = db.relationship("User", back_populates="sessions_created")
    station = db.relationship("Station", foreign_keys=[station_id])
    events = db.relationship("SessionEvent", back_populates="session", lazy="dynamic",
                             foreign_keys="SessionEvent.session_id")

    def is_active(self):
        return self.status == "active"

    def format_expiry(self):
        """Human-readable expiration string for printing on code cards."""
        dt = self.activation_expires_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # e.g. "Sat, Jun 7 at 2:35 PM"
        s = dt.strftime("%a, %b %d at %I:%M %p")
        # strip leading zero from day: " 07" -> " 7"
        return s.replace(" 0", " ")

    def is_usable(self):
        """Code can still be entered at a workstation."""
        return (
            self.status == "created"
            and datetime.now(timezone.utc) < self.activation_expires_at.replace(tzinfo=timezone.utc)
        )

    def time_remaining_seconds(self):
        if self.status != "active" or not self.expires_at:
            return 0
        delta = self.expires_at.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds()))

    def __repr__(self):
        return f"<Session {self.code_display} [{self.status}]>"


class SessionEvent(db.Model):
    __tablename__ = "session_events"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=True)
    event_type = db.Column(db.String(64), nullable=False)
    message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    session = db.relationship("Session", back_populates="events", foreign_keys=[session_id])
    station = db.relationship("Station", back_populates="events", foreign_keys=[station_id])

    def __repr__(self):
        return f"<SessionEvent {self.event_type}>"


class Setting(db.Model):
    __tablename__ = "settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    @classmethod
    def get(cls, key, default=None):
        row = cls.query.filter_by(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = str(value)
        else:
            db.session.add(cls(key=key, value=str(value)))
        db.session.commit()

    @classmethod
    def get_int(cls, key, default):
        try:
            return int(cls.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    @classmethod
    def get_bool(cls, key, default=False):
        return str(cls.get(key, default)).lower() in ("1", "true", "yes", "on")

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    actor_username = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(64), nullable=False)
    target_type = db.Column(db.String(64), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=True)
    ip_address = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.Text, nullable=True)
    details_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<AuditLog {self.action} by {self.actor_username}>"
