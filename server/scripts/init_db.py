#!/usr/bin/env python3
"""Initialize the database and seed default settings."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import Setting

DEFAULT_SETTINGS = {
    "default_session_minutes": "60",
    "code_expiration_minutes": "1440",
    "warning_minutes": "5",
    "organization_name": "Grace Marketplace",
    "ticket_footer": "Ask staff if you need more time.",
}

app = create_app()

with app.app_context():
    db.create_all()
    print("Database tables created.")

    for key, value in DEFAULT_SETTINGS.items():
        if not Setting.query.filter_by(key=key).first():
            db.session.add(Setting(key=key, value=value))
    db.session.commit()
    print("Default settings seeded.")

print("Done.")
