import json
from flask import request
from flask_login import current_user
from extensions import db
from models import AuditLog


def log_audit(action, target_type=None, target_id=None,
              station_id=None, session_id=None, details=None):
    actor_id = None
    actor_username = None
    try:
        if current_user and not current_user.is_anonymous:
            actor_id = current_user.id
            actor_username = current_user.username
    except Exception:
        pass

    try:
        forwarded = request.headers.get("X-Forwarded-For")
        ip = (forwarded.split(",")[0].strip() if forwarded else request.remote_addr)
        ua = request.headers.get("User-Agent")
    except Exception:
        ip = None
        ua = None

    db.session.add(AuditLog(
        actor_user_id=actor_id,
        actor_username=actor_username,
        action=action,
        target_type=target_type,
        target_id=target_id,
        station_id=station_id,
        session_id=session_id,
        ip_address=ip,
        user_agent=ua,
        details_json=json.dumps(details or {}),
    ))
