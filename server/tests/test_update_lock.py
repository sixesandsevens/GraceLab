#!/usr/bin/env python3
"""
Tests for GraceLab Patch B: server-side update-lock enforcement.

Covers the session-admission lock (api.py), update-status version
verification (updates.py), and the admin push/cancel/retry actions
(stations.py). Uses Flask's test client against an in-memory sqlite DB —
this is the first server test suite in the project, so it establishes the
pattern (see config.py's TestingConfig).

Run with (server deps required — see server/requirements.txt):
    FLASK_ENV=testing python3 -m unittest discover -s server/tests
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("FLASK_ENV", "testing")

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from extensions import db  # noqa: E402
from models import Session, Setting, Station, User  # noqa: E402

STATION_TOKEN = "test-station-token"


class UpdateLockTestCase(unittest.TestCase):
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
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # -- helpers ----------------------------------------------------------

    def _station_headers(self):
        return {
            "X-Station-ID": self.station.hostname,
            "Authorization": f"Bearer {STATION_TOKEN}",
        }

    def _refresh_station(self):
        db.session.refresh(self.station)

    def _make_code(self, code="123456", minutes=60):
        sess = Session(
            code_display=code,
            code_hash=generate_password_hash(code),
            status="created",
            duration_minutes=minutes,
            activation_expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.session.add(sess)
        db.session.commit()
        return sess

    def _login_admin(self):
        admin = User(username="admin", role="admin")
        admin.set_password("password123")
        db.session.add(admin)
        db.session.commit()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(admin.id)
            sess["_fresh"] = True
        return admin


class UpdateStatusVerificationTests(UpdateLockTestCase):
    """6. Update success / 7. Update mismatch / 8. Update failure."""

    def test_complete_with_matching_version_clears_lock(self):
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        resp = self.client.post(
            "/api/station/update-status", headers=self._station_headers(),
            json={"status": "complete", "version": "0.4.0"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])

        self._refresh_station()
        self.assertIsNone(self.station.desired_client_version)
        self.assertEqual(self.station.client_update_status, "complete")
        self.assertEqual(self.station.client_version, "0.4.0")

    def test_complete_with_mismatched_version_does_not_clear_lock(self):
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        resp = self.client.post(
            "/api/station/update-status", headers=self._station_headers(),
            json={"status": "complete", "version": "0.3.9"},
        )
        self.assertEqual(resp.status_code, 200)

        self._refresh_station()
        # The lock must survive a completion report for the wrong version —
        # a mismatched report must never be trusted to reopen the station.
        self.assertEqual(self.station.desired_client_version, "0.4.0")
        self.assertEqual(self.station.client_update_status, "failed")
        self.assertIsNotNone(self.station.client_update_error)

    def test_failed_status_leaves_station_locked(self):
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        resp = self.client.post(
            "/api/station/update-status", headers=self._station_headers(),
            json={"status": "failed", "version": "0.4.0", "error": "checksum mismatch"},
        )
        self.assertEqual(resp.status_code, 200)

        self._refresh_station()
        self.assertEqual(self.station.desired_client_version, "0.4.0")
        self.assertEqual(self.station.client_update_status, "failed")
        self.assertEqual(self.station.client_update_error, "checksum mismatch")

    def test_complete_with_no_prior_target_just_records_version(self):
        # No desired_client_version was queued (e.g. channel-wide auto
        # update) — completion just records the version, nothing to unlock.
        resp = self.client.post(
            "/api/station/update-status", headers=self._station_headers(),
            json={"status": "complete", "version": "0.4.0"},
        )
        self.assertEqual(resp.status_code, 200)
        self._refresh_station()
        self.assertIsNone(self.station.desired_client_version)
        self.assertEqual(self.station.client_version, "0.4.0")


class SessionAdmissionLockTests(UpdateLockTestCase):
    """10. Server-side admission is rejected when the update lock is active."""

    def test_session_validate_rejected_when_update_locked(self):
        self._make_code("111111")
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "111111"},
        )
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "station_updating")
        self.assertEqual(resp.status_code, 403)

    def test_session_validate_allowed_when_no_lock(self):
        self._make_code("222222")
        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "222222"},
        )
        self.assertTrue(resp.get_json()["ok"])

    def test_session_start_rejected_when_update_locked(self):
        sess = self._make_code("333333")
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        resp = self.client.post(
            "/api/session/start", headers=self._station_headers(),
            json={"session_id": sess.id},
        )
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "station_updating")
        self.assertEqual(resp.status_code, 403)

        # And the session must not have been consumed/started.
        db.session.refresh(sess)
        self.assertEqual(sess.status, "created")

    def test_open_start_rejected_when_update_locked(self):
        Setting.set("open_lab_mode", "true")
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        resp = self.client.post(
            "/api/session/open-start", headers=self._station_headers(), json={},
        )
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "station_updating")
        self.assertEqual(resp.status_code, 403)

    def test_open_start_allowed_when_no_lock(self):
        Setting.set("open_lab_mode", "true")
        resp = self.client.post(
            "/api/session/open-start", headers=self._station_headers(), json={},
        )
        self.assertTrue(resp.get_json()["ok"])


class HeartbeatAndConfigReportUpdateFieldsTests(UpdateLockTestCase):
    def test_heartbeat_reports_update_lock_fields(self):
        self.station.desired_client_version = "0.4.0"
        self.station.client_update_status = "downloading"
        db.session.commit()

        resp = self.client.post(
            "/api/station/heartbeat", headers=self._station_headers(),
            json={"status": "available"},
        )
        data = resp.get_json()
        self.assertTrue(data["update_pending"])
        self.assertEqual(data["update_status"], "downloading")
        self.assertEqual(data["desired_client_version"], "0.4.0")

    def test_heartbeat_reports_no_lock_when_clear(self):
        resp = self.client.post(
            "/api/station/heartbeat", headers=self._station_headers(),
            json={"status": "available"},
        )
        data = resp.get_json()
        self.assertFalse(data["update_pending"])
        self.assertIsNone(data["desired_client_version"])

    def test_station_config_reports_update_lock_fields(self):
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        resp = self.client.get("/api/station/config", headers=self._station_headers())
        data = resp.get_json()
        self.assertTrue(data["update_pending"])
        self.assertEqual(data["desired_client_version"], "0.4.0")


class AdminPushCancelRetryTests(UpdateLockTestCase):
    """9. Cancellation, plus push/retry admin actions."""

    def setUp(self):
        super().setUp()
        self._login_admin()
        Setting.set("client_stable_version", "0.4.0")

    def test_push_update_queues_target_and_resets_status(self):
        self.station.client_update_status = "failed"
        self.station.client_update_error = "previous failure"
        db.session.commit()

        resp = self.client.post(f"/admin/stations/{self.station.id}/push-update")
        self.assertEqual(resp.status_code, 302)

        self._refresh_station()
        self.assertEqual(self.station.desired_client_version, "0.4.0")
        self.assertIsNone(self.station.client_update_status)
        self.assertIsNone(self.station.client_update_error)

    def test_cancel_update_clears_lock_when_not_installing(self):
        self.station.desired_client_version = "0.4.0"
        self.station.client_update_status = None
        db.session.commit()

        resp = self.client.post(f"/admin/stations/{self.station.id}/cancel-update")
        self.assertEqual(resp.status_code, 302)

        self._refresh_station()
        self.assertIsNone(self.station.desired_client_version)
        self.assertIsNone(self.station.client_update_status)

    def test_cancel_update_rejected_while_installing(self):
        self.station.desired_client_version = "0.4.0"
        self.station.client_update_status = "installing"
        db.session.commit()

        resp = self.client.post(f"/admin/stations/{self.station.id}/cancel-update")
        self.assertEqual(resp.status_code, 302)

        self._refresh_station()
        # Rejected: the lock must survive an in-progress install.
        self.assertEqual(self.station.desired_client_version, "0.4.0")
        self.assertEqual(self.station.client_update_status, "installing")

    def test_cancel_update_rejected_while_downloading(self):
        self.station.desired_client_version = "0.4.0"
        self.station.client_update_status = "downloading"
        db.session.commit()

        self.client.post(f"/admin/stations/{self.station.id}/cancel-update")

        self._refresh_station()
        self.assertEqual(self.station.desired_client_version, "0.4.0")

    def test_cancelled_station_resumes_normal_admission(self):
        self._make_code("444444")
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        self.client.post(f"/admin/stations/{self.station.id}/cancel-update")

        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "444444"},
        )
        self.assertTrue(resp.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
