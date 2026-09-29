"""Regression checks for the first hardening release; no production DB access."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from auth import _local_redirect
from config import ProductionConfig
from extensions import db
from models import Session, Setting, Station, User
from werkzeug.security import generate_password_hash


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.app.config["RATELIMIT_ENABLED"] = False
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()
            user = User(username="review-admin", role="admin")
            user.set_password("test-password")
            station = Station(
                hostname="test-station", display_name="Test Station",
                station_token_hash=generate_password_hash("test-token"),
                status="available",
                last_seen=datetime.now(timezone.utc) - timedelta(minutes=10),
            )
            code = Session(
                code_display="123-456", code_hash="unused-in-this-test",
                duration_minutes=60,
                activation_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            db.session.add_all([user, station, code])
            db.session.commit()
            self.user_id, self.station_id, self.code_id = user.id, station.id, code.id
            Setting.set("open_lab_mode", "true")
        self.headers = {"X-Station-ID": "test-station", "Authorization": "Bearer test-token"}

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def login(self, next_page="/", remember=False):
        return self.client.post("/login", query_string={"next": next_page}, data={
            "username": "review-admin", "password": "test-password",
            "remember_me": "y" if remember else "",
        })

    def deactivate(self):
        with self.app.app_context():
            db.session.get(User, self.user_id).active = False
            db.session.commit()

    def set_status(self, status):
        with self.app.app_context():
            db.session.get(Station, self.station_id).status = status
            db.session.commit()

    def test_deactivation_revokes_existing_admin_session(self):
        self.login()
        self.assertEqual(self.client.get("/admin/users").status_code, 200)
        self.deactivate()
        response = self.client.get("/admin/users")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_deactivated_remember_cookie_cannot_restore_access(self):
        self.login(remember=True)
        self.client.delete_cookie(self.app.config["SESSION_COOKIE_NAME"])
        self.deactivate()
        self.assertEqual(self.client.get("/admin/users").status_code, 302)

    def test_inactive_user_cannot_log_in(self):
        self.deactivate()
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.get("/admin/users").status_code, 302)

    def test_active_user_remember_cookie_still_works(self):
        self.login(remember=True)
        self.client.delete_cookie(self.app.config["SESSION_COOKIE_NAME"])
        self.assertEqual(self.client.get("/admin/users").status_code, 200)

    def test_external_next_redirect_is_replaced(self):
        self.assertEqual(self.login("//example.org").location, "/")

    def test_local_next_redirect_is_preserved(self):
        self.assertEqual(self.login("/admin/sessions/?page=2").location, "/admin/sessions/?page=2")

    def test_invalid_session_user_id_is_anonymous(self):
        with self.client.session_transaction() as session:
            session["_user_id"] = "invalid"
        self.assertEqual(self.client.get("/admin/users").status_code, 302)

    def test_fault_blocks_all_admission_routes_without_consuming_code(self):
        self.set_status("needs_attention")
        for path, body in (
            ("validate", {"code": "123-456"}),
            ("start", {"session_id": self.code_id}),
            ("open-start", {}),
        ):
            with self.subTest(path=path):
                response = self.client.post("/api/session/" + path, headers=self.headers, json=body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json["error"], "station_needs_attention")
        with self.app.app_context():
            self.assertEqual(db.session.get(Session, self.code_id).status, "created")
            self.assertEqual(Session.query.count(), 1)
            self.assertIsNone(db.session.get(Station, self.station_id).current_session_id)

    def test_offline_reconnect_keeps_legacy_start_compatible(self):
        self.set_status("offline")
        response = self.client.post("/api/session/start", headers=self.headers,
                                    json={"session_id": self.code_id})
        self.assertTrue(response.json["ok"])

    def test_pending_reboot_is_not_reported_as_maintenance(self):
        with self.app.app_context():
            station = db.session.get(Station, self.station_id)
            station.pending_command_type = "reboot"
            station.pending_command_id = "test-command"
            db.session.commit()
        for path, body in (("validate", {"code": "123-456"}),
                           ("start", {"session_id": self.code_id}), ("open-start", {})):
            with self.subTest(path=path):
                response = self.client.post("/api/session/" + path, headers=self.headers, json=body)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json["error"], "station_command_pending")

    def test_offline_page_and_heartbeat_do_not_clear_fault(self):
        self.login()
        self.set_status("needs_attention")
        self.assertEqual(self.client.get("/admin/stations/").status_code, 200)
        response = self.client.post("/api/station/heartbeat", headers=self.headers,
                                    json={"status": "available"})
        self.assertEqual(response.json["station_status"], "needs_attention")

    def test_available_station_still_becomes_offline(self):
        self.login()
        self.client.get("/admin/stations/")
        with self.app.app_context():
            self.assertEqual(db.session.get(Station, self.station_id).status, "offline")

    def test_active_session_can_end_while_station_needs_attention(self):
        self.client.post("/api/session/start", headers=self.headers,
                         json={"session_id": self.code_id})
        self.set_status("needs_attention")
        response = self.client.post("/api/session/end", headers=self.headers,
                                    json={"session_id": self.code_id, "end_reason": "expired"})
        self.assertTrue(response.json["ok"])
        with self.app.app_context():
            self.assertEqual(db.session.get(Station, self.station_id).status, "needs_attention")
            self.assertEqual(db.session.get(Session, self.code_id).status, "expired")


class ConfigurationTests(unittest.TestCase):
    def test_redirect_normalization_attacks_are_rejected(self):
        for target in ("//evil.example", "///evil.example", "/\\evil.example",
                       "https://evil.example", "/\nevil.example", "", None):
            with self.subTest(target=target):
                self.assertFalse(_local_redirect(target))

    def test_production_rejects_missing_and_placeholder_secrets(self):
        for value in ("", "  ", "dev-secret-change-in-production", "CHANGE_ME_BEFORE_PRODUCTION"):
            with self.subTest(value=value), patch.dict(os.environ, {"SECRET_KEY": value}):
                with self.assertRaises(RuntimeError):
                    ProductionConfig.validate()
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                ProductionConfig.validate()

    def test_existing_non_placeholder_secret_is_compatible(self):
        with patch.dict(os.environ, {"SECRET_KEY": "existing-deployment-key"}):
            ProductionConfig.validate()
