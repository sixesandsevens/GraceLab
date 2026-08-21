#!/usr/bin/env python3
"""
Tests for GraceLab Patch B: client-side update-lock session admission.

A queued/active/failed update on the station is a session-admission lock:
the client must not start a new session while _update_locked is true, must
not interrupt a session already running just because the lock engages, and
must route back through the lock (not straight to idle) once a session ends.

Server-side admission enforcement and update-status verification are covered
in server/tests/test_update_lock.py.

Run with:
    python3 -m unittest discover -s client/tests
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gracelab_client  # noqa: E402
from test_client_lifecycle import _make_client  # noqa: E402

IDLE = gracelab_client.GraceLabClient.IDLE
SESSION_ACTIVE = gracelab_client.GraceLabClient.SESSION_ACTIVE


class UpdateLockScreenDispatchTests(unittest.TestCase):
    def test_non_failed_status_shows_pending_screen(self):
        for status in (None, "downloading", "installing"):
            client = _make_client(_update_status=status)
            client._show_update_pending = MagicMock()
            client._show_update_failed = MagicMock()

            client._show_update_lock_screen()

            client._show_update_pending.assert_called_once()
            client._show_update_failed.assert_not_called()
            self.assertEqual(client._update_lock_screen_status, status)

    def test_failed_status_shows_failed_screen(self):
        client = _make_client(_update_status="failed")
        client._show_update_pending = MagicMock()
        client._show_update_failed = MagicMock()

        client._show_update_lock_screen()

        client._show_update_failed.assert_called_once()
        client._show_update_pending.assert_not_called()


class ReturnToIdleOrUpdateTests(unittest.TestCase):
    def test_goes_idle_when_not_locked(self):
        client = _make_client(_update_locked=False)
        client._show_idle = MagicMock()
        client._show_update_lock_screen = MagicMock()

        client._return_to_available_state()

        client._show_idle.assert_called_once()
        client._show_update_lock_screen.assert_not_called()

    def test_goes_to_update_screen_when_locked(self):
        client = _make_client(_update_locked=True)
        client._show_idle = MagicMock()
        client._show_update_lock_screen = MagicMock()

        client._return_to_available_state()

        client._show_update_lock_screen.assert_called_once()
        client._show_idle.assert_not_called()


class SessionAdmissionRefusalTests(unittest.TestCase):
    """3. Client refuses to start a new session while update-locked."""

    def test_submit_code_refuses_when_locked(self):
        client = _make_client(_state=IDLE, _update_locked=True)
        client._show_update_lock_screen = MagicMock()
        client._proceed_code_validate = MagicMock()
        client._code_var = MagicMock()
        client._code_var.get.return_value = "123-456"

        client._submit_code()

        client._show_update_lock_screen.assert_called_once()
        client._proceed_code_validate.assert_not_called()
        client.api.validate.assert_not_called()

    def test_submit_code_proceeds_when_not_locked(self):
        client = _make_client(_state=IDLE, _update_locked=False, _tos_text="")
        client._show_update_lock_screen = MagicMock()
        client._proceed_code_validate = MagicMock()
        client._code_var = MagicMock()
        client._code_var.get.return_value = "123-456"

        client._submit_code()

        client._show_update_lock_screen.assert_not_called()
        client._proceed_code_validate.assert_called_once_with("123-456")

    def test_begin_open_session_refuses_when_locked(self):
        client = _make_client(_state=IDLE, _update_locked=True)
        client._show_update_lock_screen = MagicMock()
        client._proceed_open_session = MagicMock()

        client._begin_open_session()

        client._show_update_lock_screen.assert_called_once()
        client._proceed_open_session.assert_not_called()
        client.api.open_start.assert_not_called()

    def test_begin_open_session_proceeds_when_not_locked(self):
        client = _make_client(_state=IDLE, _update_locked=False, _tos_text="")
        client._show_update_lock_screen = MagicMock()
        client._proceed_open_session = MagicMock()

        client._begin_open_session()

        client._show_update_lock_screen.assert_not_called()
        client._proceed_open_session.assert_called_once()


class ServerRejectionConversionTests(unittest.TestCase):
    """Defense in depth: a stale/racy client still honors a server-side
    station_updating rejection instead of showing a generic error."""

    def test_validate_rejection_shows_update_screen_not_generic_error(self):
        client = _make_client()
        client.api.validate.return_value = {"ok": False, "error": "station_updating"}
        client._show_update_lock_screen = MagicMock()
        client._idle_with_error = MagicMock()

        client._validate_and_start("123456")

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertIs(callback, client._show_update_lock_screen)
        self.assertTrue(client._update_locked)

    def test_start_rejection_shows_update_screen_not_generic_error(self):
        client = _make_client()
        client.api.validate.return_value = {
            "ok": True, "session_id": 42, "warning_minutes": 5,
        }
        client.api.start.return_value = {"ok": False, "error": "station_updating"}
        client._show_update_lock_screen = MagicMock()
        client._idle_with_error = MagicMock()
        client._show_session_starting = MagicMock()

        client._validate_and_start("123456")

        self.assertTrue(client._update_locked)
        calls = [c.args[1] for c in client.root.after.call_args_list]
        self.assertIn(client._show_update_lock_screen, calls)
        client._idle_with_error.assert_not_called()

    def test_open_start_rejection_shows_update_screen(self):
        client = _make_client()
        client.api.open_start.return_value = {"ok": False, "error": "station_updating"}
        client._show_update_lock_screen = MagicMock()
        client._show_needs_attention = MagicMock()

        client._do_open_session()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertIs(callback, client._show_update_lock_screen)
        self.assertTrue(client._update_locked)


class ActiveSessionNotInterruptedTests(unittest.TestCase):
    """4. An update becoming pending must not interrupt a running session."""

    def test_heartbeat_does_not_touch_ui_during_active_session(self):
        client = _make_client(_state=SESSION_ACTIVE, _update_locked=False)
        client.api.heartbeat.return_value = {
            "ok": True,
            "station_status": "in_use",
            "update_pending": True,
            "update_status": "pending",
            "desired_client_version": "0.4.0",
        }
        client._show_update_lock_screen = MagicMock()
        client._show_idle = MagicMock()
        client._show_needs_attention = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client._show_update_lock_screen.assert_not_called()
        client._show_idle.assert_not_called()
        client._show_needs_attention.assert_not_called()
        # The lock is still recorded so it's honored once the session ends.
        self.assertTrue(client._update_locked)
        self.assertEqual(client._update_status, "pending")
        self.assertEqual(client._desired_client_version, "0.4.0")


class HeartbeatIdleTransitionTests(unittest.TestCase):
    def test_idle_station_shows_update_screen_when_lock_engages(self):
        client = _make_client(_state=IDLE, _update_locked=False)
        client.api.heartbeat.return_value = {
            "ok": True,
            "station_status": "available",
            "update_pending": True,
            "update_status": "installing",
            "desired_client_version": "0.4.0",
        }
        client._show_update_lock_screen = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_update_lock_screen.assert_called_once()

    def test_update_screen_returns_to_idle_when_lock_clears(self):
        client = _make_client(
            _state=gracelab_client.GraceLabClient.UPDATE_PENDING,
            _update_locked=True, _update_status="installing",
            _update_lock_screen_status="installing",
        )
        client.api.heartbeat.return_value = {
            "ok": True,
            "station_status": "available",
            "update_pending": False,
            "update_status": None,
            "desired_client_version": None,
        }
        client._show_idle = MagicMock()
        client._show_update_lock_screen = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_idle.assert_called_once()
        client._show_update_lock_screen.assert_not_called()
        self.assertFalse(client._update_locked)

    def test_needs_attention_takes_priority_over_update_screen(self):
        client = _make_client(
            _state=gracelab_client.GraceLabClient.UPDATE_PENDING,
            _update_locked=True, _update_status="installing",
            _update_lock_screen_status="installing",
        )
        client.api.heartbeat.return_value = {
            "ok": True,
            "station_status": "needs_attention",
            "update_pending": True,
            "update_status": "installing",
            "desired_client_version": "0.4.0",
        }
        client._show_idle = MagicMock()
        client._show_update_lock_screen = MagicMock()
        client._show_needs_attention = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()
        client._show_update_lock_screen.assert_not_called()
        client._show_idle.assert_not_called()


class SessionEndUpdateRaceTests(unittest.TestCase):
    """5. Session-end race: reset must route through the lock gate, never
    straight to _show_idle, so a queued update can't be raced by admission."""

    def test_run_reset_schedules_the_gate_not_show_idle_directly(self):
        client = _make_client()
        client._run_script = MagicMock(return_value=True)

        client._run_reset("sess-1")

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        # Bound-method objects aren't identical across accesses, but they
        # compare equal for the same instance+function — assertEqual is the
        # correct check here, not assertIs.
        self.assertEqual(callback, client._return_to_available_state)

    def test_session_end_shows_update_screen_when_locked(self):
        client = _make_client(_update_locked=True, _update_status="installing")
        client._run_script = MagicMock(return_value=True)
        client._show_idle = MagicMock()
        client._show_update_pending = MagicMock()
        client._show_update_failed = MagicMock()

        client._run_reset("sess-1")
        _delay, callback = client.root.after.call_args[0]
        callback()

        client._show_idle.assert_not_called()
        client._show_update_pending.assert_called_once()

    def test_session_end_returns_to_idle_when_not_locked(self):
        client = _make_client(_update_locked=False)
        client._run_script = MagicMock(return_value=True)
        client._show_idle = MagicMock()
        client._show_update_pending = MagicMock()

        client._run_reset("sess-1")
        _delay, callback = client.root.after.call_args[0]
        callback()

        client._show_idle.assert_called_once()
        client._show_update_pending.assert_not_called()


if __name__ == "__main__":
    unittest.main()
