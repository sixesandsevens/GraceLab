#!/usr/bin/env python3
"""
Tests for GraceLab Patch C: server-side maintenance mode and the one-shot
station command channel (reset_gracelab | reboot).

Covers the maintenance admission lock (api.py), the admin enter/exit
maintenance actions and command-issuing actions (stations.py), and
command-status replay safety. Follows the same pattern as
server/tests/test_update_lock.py (Patch B) — see that file for the
TestingConfig/Flask-test-client setup this project uses.

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
from models import Session, Station, User  # noqa: E402

STATION_TOKEN = "test-station-token"


class MaintenanceTestCase(unittest.TestCase):
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

    def _heartbeat(self, **body):
        body.setdefault("status", "available")
        return self.client.post(
            "/api/station/heartbeat", headers=self._station_headers(), json=body,
        )


class EnterMaintenanceTests(MaintenanceTestCase):
    """1. Maintenance request while idle / 2. during an active session."""

    def setUp(self):
        super().setUp()
        self._login_admin()

    def test_enter_maintenance_sets_requested_flag(self):
        resp = self.client.post(f"/admin/stations/{self.station.id}/enter-maintenance")
        self.assertEqual(resp.status_code, 302)
        self._refresh_station()
        self.assertTrue(self.station.maintenance_requested)

    def test_enter_maintenance_admission_locked_while_idle(self):
        self._make_code("111111")
        self.client.post(f"/admin/stations/{self.station.id}/enter-maintenance")

        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "111111"},
        )
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "station_maintenance")
        self.assertEqual(resp.status_code, 403)

    def test_enter_maintenance_does_not_touch_active_session(self):
        # Station has an active session (current_session_id set).
        sess = self._make_code("222222")
        sess.status = "active"
        self.station.current_session_id = sess.id
        self.station.status = "in_use"
        db.session.commit()

        self.client.post(f"/admin/stations/{self.station.id}/enter-maintenance")

        self._refresh_station()
        db.session.refresh(sess)
        # The request is recorded, but nothing about the running session
        # changed — the admin route must not touch it.
        self.assertTrue(self.station.maintenance_requested)
        self.assertEqual(self.station.current_session_id, sess.id)
        self.assertEqual(sess.status, "active")
        self.assertEqual(self.station.status, "in_use")

    def test_open_start_rejected_when_maintenance_requested(self):
        from models import Setting
        Setting.set("open_lab_mode", "true")
        self.client.post(f"/admin/stations/{self.station.id}/enter-maintenance")

        resp = self.client.post(
            "/api/session/open-start", headers=self._station_headers(), json={},
        )
        data = resp.get_json()
        self.assertEqual(data["error"], "station_maintenance")

    def test_session_start_rejected_when_maintenance_requested(self):
        sess = self._make_code("333333")
        self.client.post(f"/admin/stations/{self.station.id}/enter-maintenance")

        resp = self.client.post(
            "/api/session/start", headers=self._station_headers(),
            json={"session_id": sess.id},
        )
        data = resp.get_json()
        self.assertEqual(data["error"], "station_maintenance")
        db.session.refresh(sess)
        self.assertEqual(sess.status, "created")

    def test_maintenance_takes_priority_over_update_lock(self):
        self._make_code("444444")
        self.station.desired_client_version = "0.4.0"
        db.session.commit()
        self.client.post(f"/admin/stations/{self.station.id}/enter-maintenance")

        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "444444"},
        )
        # Maintenance is checked first — station_maintenance wins even
        # though an update is also queued.
        self.assertEqual(resp.get_json()["error"], "station_maintenance")


class ExitMaintenanceTests(MaintenanceTestCase):
    """5. Exit maintenance / 6. Maintenance + update pending interaction."""

    def setUp(self):
        super().setUp()
        self._login_admin()

    def test_exit_maintenance_clears_requested_flag(self):
        self.station.maintenance_requested = True
        db.session.commit()

        resp = self.client.post(f"/admin/stations/{self.station.id}/exit-maintenance")
        self.assertEqual(resp.status_code, 302)
        self._refresh_station()
        self.assertFalse(self.station.maintenance_requested)

    def test_exit_maintenance_reopens_admission_when_no_other_lock(self):
        self._make_code("555555")
        self.station.maintenance_requested = True
        db.session.commit()

        self.client.post(f"/admin/stations/{self.station.id}/exit-maintenance")

        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "555555"},
        )
        self.assertTrue(resp.get_json()["ok"])

    def test_exit_maintenance_does_not_bypass_update_lock(self):
        """6. Update lock must still win once maintenance clears."""
        self._make_code("666666")
        self.station.maintenance_requested = True
        self.station.desired_client_version = "0.4.0"
        db.session.commit()

        self.client.post(f"/admin/stations/{self.station.id}/exit-maintenance")

        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "666666"},
        )
        data = resp.get_json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "station_updating")

    def test_local_maintenance_exit_endpoint_clears_flag(self):
        """The client-authenticated local-exit endpoint the gracelab process
        itself calls (not an admin dashboard action)."""
        self.station.maintenance_requested = True
        db.session.commit()

        resp = self.client.post(
            "/api/station/maintenance-exit", headers=self._station_headers(), json={},
        )
        self.assertTrue(resp.get_json()["ok"])
        self._refresh_station()
        self.assertFalse(self.station.maintenance_requested)


class AdminOverrideCannotBeGuestTriggeredTests(MaintenanceTestCase):
    """4. Admin override: a station's own heartbeat can never set
    maintenance_requested — only the admin dashboard route can."""

    def test_heartbeat_cannot_set_maintenance_requested(self):
        resp = self._heartbeat(maintenance_requested=True, status="available")
        self.assertTrue(resp.get_json()["ok"])
        self._refresh_station()
        self.assertFalse(self.station.maintenance_requested)

    def test_heartbeat_self_reports_maintenance_active(self):
        # maintenance_active IS legitimately client-reported (it just says
        # "I have switched away"), unlike maintenance_requested.
        resp = self._heartbeat(maintenance_active=True)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self._refresh_station()
        self.assertTrue(self.station.maintenance_active)

    def test_session_validate_endpoint_has_no_way_to_request_maintenance(self):
        # There is no field in /api/session/validate's request body that can
        # influence maintenance_requested at all — confirm a guest-facing
        # code-entry POST can't touch it regardless of body content.
        self._make_code("777777")
        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "777777", "maintenance_requested": True},
        )
        self.assertTrue(resp.get_json()["ok"])
        self._refresh_station()
        self.assertFalse(self.station.maintenance_requested)


class StationCommandChannelTests(MaintenanceTestCase):
    """7. Remote GraceLab reset / 9. Remote reboot / 10. Replay safety."""

    def setUp(self):
        super().setUp()
        self._login_admin()

    def test_reset_gracelab_issues_command(self):
        resp = self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")
        self.assertEqual(resp.status_code, 302)
        self._refresh_station()
        self.assertEqual(self.station.pending_command_type, "reset_gracelab")
        self.assertIsNotNone(self.station.pending_command_id)

    def test_reset_command_appears_in_heartbeat_until_cleared(self):
        self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")
        self._refresh_station()
        command_id = self.station.pending_command_id

        resp = self._heartbeat()
        data = resp.get_json()
        self.assertEqual(data["station_command"], {"id": command_id, "type": "reset_gracelab"})

        # Client reports completion — command must stop appearing.
        self.client.post(
            "/api/station/command-status", headers=self._station_headers(),
            json={"command_id": command_id, "status": "complete"},
        )
        resp2 = self._heartbeat()
        self.assertIsNone(resp2.get_json()["station_command"])

    def test_reset_command_admission_locked_while_pending(self):
        self._make_code("888888")
        self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")

        resp = self.client.post(
            "/api/session/validate", headers=self._station_headers(),
            json={"code": "888888"},
        )
        self.assertEqual(resp.get_json()["error"], "station_maintenance")

    def test_reset_failure_marks_needs_attention_and_clears_command(self):
        """8. Reset failure — station must not return to guest-admittable idle."""
        self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")
        self._refresh_station()
        command_id = self.station.pending_command_id

        resp = self.client.post(
            "/api/station/command-status", headers=self._station_headers(),
            json={"command_id": command_id, "status": "failed", "error": "reset script failed"},
        )
        self.assertTrue(resp.get_json()["ok"])
        self._refresh_station()
        self.assertEqual(self.station.status, "needs_attention")
        self.assertIsNone(self.station.pending_command_id)
        self.assertEqual(self.station.pending_command_status, "failed")

    def test_reboot_issues_command_when_idle(self):
        resp = self.client.post(f"/admin/stations/{self.station.id}/reboot")
        self.assertEqual(resp.status_code, 302)
        self._refresh_station()
        self.assertEqual(self.station.pending_command_type, "reboot")

    def test_reboot_rejected_when_session_active(self):
        sess = self._make_code("999999")
        sess.status = "active"
        self.station.current_session_id = sess.id
        db.session.commit()

        resp = self.client.post(f"/admin/stations/{self.station.id}/reboot")
        self.assertEqual(resp.status_code, 302)
        self._refresh_station()
        self.assertIsNone(self.station.pending_command_type)

    def test_cannot_issue_second_command_while_one_pending(self):
        self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")
        self._refresh_station()
        first_id = self.station.pending_command_id

        self.client.post(f"/admin/stations/{self.station.id}/reboot")
        self._refresh_station()
        # The reboot attempt must not have overwritten the in-flight reset.
        self.assertEqual(self.station.pending_command_type, "reset_gracelab")
        self.assertEqual(self.station.pending_command_id, first_id)

    def test_stale_command_id_report_is_ignored(self):
        """10. A report for a command_id that doesn't match the current
        pending command must not clear/corrupt it (replay safety)."""
        self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")
        self._refresh_station()
        real_id = self.station.pending_command_id

        resp = self.client.post(
            "/api/station/command-status", headers=self._station_headers(),
            json={"command_id": "not-the-real-id", "status": "complete"},
        )
        self.assertTrue(resp.get_json()["ok"])
        self._refresh_station()
        # Untouched — the stale/foreign report was a no-op.
        self.assertEqual(self.station.pending_command_id, real_id)
        self.assertEqual(self.station.pending_command_type, "reset_gracelab")

    def test_new_command_after_completion_gets_a_fresh_id(self):
        """A second, later command is allowed once the first is done, and
        gets its own id — completed commands never replay."""
        self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")
        self._refresh_station()
        first_id = self.station.pending_command_id
        self.client.post(
            "/api/station/command-status", headers=self._station_headers(),
            json={"command_id": first_id, "status": "complete"},
        )

        self.client.post(f"/admin/stations/{self.station.id}/reset-gracelab")
        self._refresh_station()
        self.assertIsNotNone(self.station.pending_command_id)
        self.assertNotEqual(self.station.pending_command_id, first_id)


if __name__ == "__main__":
    unittest.main()
