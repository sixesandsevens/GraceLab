#!/usr/bin/env python3
"""
Tests for the Begin Session announcement banner (settings + station config
API) and for the station Delete button's visibility on the stations page.

Run with (server deps required — see server/requirements.txt):
    FLASK_ENV=testing python3 -m unittest discover -s server/tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("FLASK_ENV", "testing")

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import AuditLog, Setting, Station, User  # noqa: E402

STATION_TOKEN = "test-station-token"


class AnnouncementTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.station = Station(
            hostname="lab-1",
            display_name="Lab 1",
            station_token_hash=generate_password_hash(STATION_TOKEN),
            status="available",
        )
        db.session.add(self.station)
        admin = User(username="admin", role="admin")
        admin.set_password("password123")
        db.session.add(admin)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _config(self):
        return self.client.get(
            "/api/station/config",
            headers={"X-Station-ID": "lab-1", "Authorization": f"Bearer {STATION_TOKEN}"},
        ).get_json()

    def _save_settings(self, announcement):
        return self.client.post("/admin/settings", data={
            "organization_name": "Grace Marketplace",
            "default_session_minutes": "60",
            "code_expiration_minutes": "1440",
            "warning_minutes": "10",
            "station_offline_after_seconds": "90",
            "batch_code_max_count": "36",
            "client_update_policy": "idle_only",
            "client_update_channel": "stable",
            "open_session_duration_minutes": "60",
            "announcement_text": announcement,
        })


class AnnouncementTests(AnnouncementTestCase):
    def test_config_has_empty_announcement_by_default(self):
        self.assertEqual(self._config()["announcement"], "")

    def test_set_update_and_clear_announcement(self):
        self.assertEqual(self._save_settings("  Printing has been fixed!  ").status_code, 302)
        self.assertEqual(self._config()["announcement"], "Printing has been fixed!")

        self._save_settings("Printer 2 is out of paper.")
        self.assertEqual(self._config()["announcement"], "Printer 2 is out of paper.")

        resp = self.client.post("/admin/announcement/clear")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._config()["announcement"], "")
        self.assertTrue(AuditLog.query.filter_by(action="settings_changed").count() >= 3)

    def test_blank_announcement_removes_it(self):
        self._save_settings("Printing has been fixed!")
        self._save_settings("")
        self.assertEqual(self._config()["announcement"], "")

    def test_announcement_too_long_is_rejected(self):
        resp = self._save_settings("x" * 301)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(Setting.get("announcement_text"))

    def test_settings_page_shows_remove_button_only_when_set(self):
        self.assertNotIn(b"Remove Announcement", self.client.get("/admin/settings").data)
        self._save_settings("Printing has been fixed!")
        self.assertIn(b"Remove Announcement", self.client.get("/admin/settings").data)


class StationDeleteButtonTests(AnnouncementTestCase):
    def test_delete_shown_for_available_station(self):
        page = self.client.get("/admin/stations/").data
        self.assertIn(f"/admin/stations/{self.station.id}/delete".encode(), page)

    def test_delete_available_station(self):
        resp = self.client.post(f"/admin/stations/{self.station.id}/delete")
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(db.session.get(Station, self.station.id))


if __name__ == "__main__":
    unittest.main()
