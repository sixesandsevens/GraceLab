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
    "default_session_minutes":       "60",
    "code_expiration_minutes":       "1440",
    "warning_minutes":               "5",
    "organization_name":             "Grace Marketplace",
    "ticket_footer":                 "Ask staff if you need more time.",
    "station_offline_after_seconds": "90",
    "batch_code_max_count":          "36",
    "client_updates_enabled":        "true",
    "client_update_policy":          "idle_only",
    "client_update_channel":         "stable",
    "client_min_supported_version":  "",
    "client_stable_version":         "",
    "client_beta_version":           "",
    "open_lab_mode":                 "false",
    "open_session_duration_minutes": "120",
    "tos_text": (
        "By using this computer, you agree to use it for lawful purposes only. "
        "Do not share personal information or save sensitive documents on this machine. "
        "Files saved on this computer are permanently deleted when your session ends."
    ),
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

# DDL for sessions table with created_by_user_id nullable.
# Used when migrating from the original NOT NULL schema.
_SESSIONS_NULLABLE_DDL = """
CREATE TABLE sessions_new (
    id INTEGER NOT NULL,
    code_hash VARCHAR(256) NOT NULL,
    code_display VARCHAR(16) NOT NULL,
    status VARCHAR(32) NOT NULL,
    duration_minutes INTEGER NOT NULL,
    created_by_user_id INTEGER,
    created_at DATETIME NOT NULL,
    activation_expires_at DATETIME NOT NULL,
    started_at DATETIME,
    expires_at DATETIME,
    ended_at DATETIME,
    station_id INTEGER,
    end_reason VARCHAR(64),
    notes TEXT,
    PRIMARY KEY (id),
    FOREIGN KEY(created_by_user_id) REFERENCES users (id),
    FOREIGN KEY(station_id) REFERENCES stations (id)
)
"""

app = create_app()

with app.app_context():
    db.create_all()
    print("Database tables created (or already exist).")

    engine = db.engine
    with engine.connect() as conn:
        # Add new columns to existing tables
        for table, col, col_type in _MIGRATIONS:
            try:
                conn.execute(db.text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
                conn.commit()
                print(f"  Added column {table}.{col}")
            except Exception:
                pass  # column already exists

        # Make sessions.created_by_user_id nullable if it was created NOT NULL.
        # SQLite doesn't support ALTER COLUMN, so we recreate the table.
        try:
            result = conn.execute(db.text("PRAGMA table_info(sessions)"))
            col_nullability = {row[1]: row[3] for row in result.fetchall()}
            if col_nullability.get("created_by_user_id") == 1:
                print("  Migrating sessions table: removing NOT NULL from created_by_user_id...")
                conn.execute(db.text("PRAGMA foreign_keys = OFF"))
                conn.execute(db.text(_SESSIONS_NULLABLE_DDL))
                conn.execute(db.text("INSERT INTO sessions_new SELECT * FROM sessions"))
                conn.execute(db.text("DROP TABLE sessions"))
                conn.execute(db.text("ALTER TABLE sessions_new RENAME TO sessions"))
                conn.execute(db.text("PRAGMA foreign_keys = ON"))
                conn.commit()
                print("  Migration complete.")
        except Exception as e:
            print(f"  Warning: sessions nullable migration check failed: {e}")

    for key, value in DEFAULT_SETTINGS.items():
        if not Setting.query.filter_by(key=key).first():
            db.session.add(Setting(key=key, value=value))
    db.session.commit()
    print("Default settings seeded.")

print("Done.")
