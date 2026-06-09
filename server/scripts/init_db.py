#!/usr/bin/env python3
"""Initialize the database and seed default settings.

Safe to run on an existing database — creates missing tables and adds
missing columns to existing tables without touching existing data.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from extensions import db
from models import Setting

DEFAULT_SETTINGS = {
    "default_session_minutes":      "60",
    "code_expiration_minutes":      "1440",
    "warning_minutes":              "5",
    "organization_name":            "Grace Marketplace",
    "ticket_footer":                "Ask staff if you need more time.",
    "station_offline_after_seconds": "90",
    "batch_code_max_count":         "36",
    "client_updates_enabled":       "false",
    "client_update_policy":         "idle_only",
    "client_update_channel":        "stable",
    "client_min_supported_version": "",
}

# Columns to add to existing tables if they are missing.
# (table, column_name, column_definition)
_MIGRATIONS = [
    ("stations", "client_version",          "TEXT"),
    ("stations", "client_update_status",     "TEXT"),
    ("stations", "client_update_error",      "TEXT"),
    ("stations", "last_update_check_at",     "DATETIME"),
    ("stations", "last_update_started_at",   "DATETIME"),
    ("stations", "last_update_finished_at",  "DATETIME"),
    ("stations", "desired_client_version",   "TEXT"),
]

app = create_app()

with app.app_context():
    db.create_all()
    print("Database tables created (or already exist).")

    engine = db.engine
    with engine.connect() as conn:
        for table, col, col_type in _MIGRATIONS:
            try:
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
                print(f"  Added column {table}.{col}")
            except Exception:
                pass  # column already exists

    for key, value in DEFAULT_SETTINGS.items():
        if not Setting.query.filter_by(key=key).first():
            db.session.add(Setting(key=key, value=value))
    db.session.commit()
    print("Default settings seeded.")

print("Done.")
