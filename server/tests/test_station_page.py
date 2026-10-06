#!/usr/bin/env python3
"""
Tests for the per-station page (mobile troubleshooting) and for actions
returning to the page they were posted from.

Run with (server deps required — see server/requirements.txt):
    FLASK_ENV=testing python3 -m unittest discover -s server/tests
"""

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("FLASK_ENV", "testing")

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Session, SessionEvent, Station, User  # noqa: E402
from ui import ago, localtime  # noqa: E402


class StationPageTestCase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.client = self.app.test_client()

        self.station = Station(
            hostname="lab-1",
            display_name="Lab 1",
            station_token_hash=generate_password_hash("tok"),
            status="available",
            last_seen=datetime.now(timezone.utc),
        )
        db.session.add(self.station)
        db.session.commit()
        self.url = f"/admin/stations/{self.station.id}"

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _login(self, role="admin"):
        user = User(username=role, role=role)
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

    def _start_session(self):
        now = datetime.now(timezone.utc)
        sess = Session(
            code_display="123456",
            code_hash=generate_password_hash("123456"),
            status="active",
            duration_minutes=60,
            activation_expires_at=now + timedelta(hours=1),
            started_at=now,
            expires_at=now + timedelta(minutes=40),
            station_id=self.station.id,
        )
        db.session.add(sess)
        db.session.commit()
        self.station.current_session_id = sess.id
        self.station.status = "in_use"
        db.session.commit()
        return sess


class StationPageRenderTests(StationPageTestCase):
    def test_admin_sees_session_actions_and_events(self):
        self._login("admin")
        self._start_session()
        db.session.add(SessionEvent(station_id=self.station.id, event_type="client_error",
                                    message="Command reset_gracelab (x) failed: boom"))
        db.session.commit()

        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Lab 1", html)
        self.assertIn("123456", html)
        self.assertIn("End Session", html)
        self.assertIn("+15 min", html)
        self.assertIn("Reset GraceLab", html)
        self.assertIn("boom", html)
        # Actions posted from here come back here.
        self.assertIn(f'name="next" value="{self.url}"', html)
        # A session is active, so Reboot is not offered.
        self.assertNotIn(">Reboot<", html)

    def test_staff_sees_status_but_no_admin_actions(self):
        self._login("staff")
        self._start_session()
        html = self.client.get(self.url).get_data(as_text=True)
        self.assertIn("End Session", html)
        self.assertNotIn("Reset GraceLab", html)
        self.assertNotIn("+15 min", html)

    def test_confirm_text_is_js_safe(self):
        self.station.display_name = "Bob's <PC>"
        db.session.commit()
        self._login("admin")
        html = self.client.get(self.url).get_data(as_text=True)
        self.assertNotIn("Bob's", html)
        self.assertNotIn("<PC>", html)

    def test_refreshes_only_while_something_is_in_flight(self):
        self._login("admin")
        self.assertNotIn('http-equiv="refresh"', self.client.get(self.url).get_data(as_text=True))
        self.client.post(f"{self.url}/reset-gracelab")
        self.assertIn('http-equiv="refresh"', self.client.get(self.url).get_data(as_text=True))

    def test_stale_station_shows_offline(self):
        self.station.last_seen = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.session.commit()
        self._login("admin")
        html = self.client.get(self.url).get_data(as_text=True)
        self.assertIn("status-offline", html)
        self.assertIn("Restart the computer itself", html)

    def test_list_and_dashboard_link_to_station_page(self):
        self._login("admin")
        for page in ("/admin/stations/", "/"):
            html = self.client.get(page).get_data(as_text=True)
            self.assertIn(f'href="{self.url}"', html, page)

    def test_missing_station_is_404(self):
        self._login("admin")
        self.assertEqual(self.client.get("/admin/stations/999").status_code, 404)


class RedirectBackTests(StationPageTestCase):
    def setUp(self):
        super().setUp()
        self._login("admin")

    def test_action_returns_to_station_page(self):
        resp = self.client.post(f"{self.url}/enter-maintenance", data={"next": self.url})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith(self.url))

    def test_action_without_next_returns_to_list(self):
        resp = self.client.post(f"{self.url}/enter-maintenance")
        self.assertTrue(resp.headers["Location"].endswith("/admin/stations/"))

    def test_rejected_action_still_returns_to_station_page(self):
        self._start_session()
        resp = self.client.post(f"{self.url}/reboot", data={"next": self.url})
        self.assertTrue(resp.headers["Location"].endswith(self.url))
        db.session.refresh(self.station)
        self.assertIsNone(self.station.pending_command_type)

    def test_offsite_next_is_ignored(self):
        for bad in ("//evil.example/x", "https://evil.example/", "\\\\evil.example", "javascript:alert(1)"):
            resp = self.client.post(f"{self.url}/enter-maintenance", data={"next": bad})
            self.assertTrue(resp.headers["Location"].endswith("/admin/stations/"), bad)

    def test_session_end_and_extend_return_to_station_page(self):
        sess = self._start_session()
        resp = self.client.post(f"/admin/sessions/{sess.id}/extend", data={"minutes": 15, "next": self.url})
        self.assertTrue(resp.headers["Location"].endswith(self.url))
        resp = self.client.post(f"/admin/sessions/{sess.id}/end", data={"next": self.url})
        self.assertTrue(resp.headers["Location"].endswith(self.url))
        db.session.refresh(sess)
        self.assertEqual(sess.status, "ended_by_staff")

    def test_delete_goes_to_list(self):
        resp = self.client.post(f"{self.url}/delete", data={"next": self.url})
        self.assertTrue(resp.headers["Location"].endswith("/admin/stations/"))


class DashboardOfflineTests(StationPageTestCase):
    def test_dashboard_marks_stale_station_offline(self):
        self.station.last_seen = datetime.now(timezone.utc) - timedelta(minutes=10)
        db.session.commit()
        self._login("staff")
        self.client.get("/")
        db.session.refresh(self.station)
        self.assertEqual(self.station.status, "offline")


class AgoTests(unittest.TestCase):
    def test_buckets(self):
        now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(ago(None), "never")
        self.assertEqual(ago(now - timedelta(seconds=10), now), "just now")
        self.assertEqual(ago(now - timedelta(minutes=3), now), "3 min ago")
        self.assertEqual(ago(now - timedelta(hours=2), now), "2 h ago")
        self.assertEqual(ago(now - timedelta(days=1), now), "1 day ago")
        self.assertEqual(ago(datetime(2026, 10, 2, 12, 0), now), "4 days ago")  # naive = UTC


class LocalTimeTests(unittest.TestCase):
    """Stored times are UTC; staff and printed tickets see lab-local time."""

    def setUp(self):
        self._tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()

    def tearDown(self):
        if self._tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._tz
        time.tzset()

    def test_styles(self):
        dt = datetime(2026, 10, 6, 17, 48, 5)  # naive = UTC, 1:48 PM EDT
        self.assertEqual(localtime(dt, "time"), "1:48 PM")
        self.assertEqual(localtime(dt, "seconds"), "1:48:05 PM")
        self.assertEqual(localtime(dt), "2026-10-06 1:48 PM")
        self.assertEqual(localtime(dt, "full"), "2026-10-06 1:48:05 PM EDT")
        self.assertEqual(localtime(dt, "ticket"), "Tue, Oct 6 at 1:48 PM")
        self.assertEqual(localtime(dt.replace(tzinfo=timezone.utc), "time"), "1:48 PM")

    def test_standard_time(self):
        self.assertEqual(localtime(datetime(2026, 12, 1, 17, 0), "full"), "2026-12-01 12:00:00 PM EST")

    def test_missing(self):
        self.assertEqual(localtime(None), "—")
        self.assertEqual(localtime(None, "datetime", "never"), "never")

    def test_ticket_expiry_is_local(self):
        sess = Session(activation_expires_at=datetime(2026, 6, 7, 18, 35))
        self.assertEqual(sess.format_expiry(), "Sun, Jun 7 at 2:35 PM")


if __name__ == "__main__":
    unittest.main()
