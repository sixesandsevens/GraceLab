#!/usr/bin/env python3
"""
Tests for GraceLab Patch C: client-side maintenance mode and the one-shot
station command channel (reset_gracelab | reboot).

Maintenance is a session-admission lock like Patch B's update lock, but
outranks it: _return_to_available_state checks maintenance first. Entering
maintenance never interrupts a running session (mirrors Patch B's update-lock
non-interruption); the one-shot reset/reboot commands are dispatched through
an explicit allowlist with an in-memory replay guard backed by the server's
own command_id clearing (see server/tests/test_maintenance.py).

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
MAINTENANCE_ACTIVE = gracelab_client.GraceLabClient.MAINTENANCE_ACTIVE
UPDATE_PENDING = gracelab_client.GraceLabClient.UPDATE_PENDING


def _heartbeat_body(**overrides):
    body = {
        "ok": True,
        "station_status": "available",
        "maintenance_requested": False,
        "update_pending": False,
        "update_status": None,
        "desired_client_version": None,
        "station_command": None,
    }
    body.update(overrides)
    return body


class ReturnToAvailableStatePriorityTests(unittest.TestCase):
    """5/6. Maintenance outranks the update lock; both outrank idle."""

    def test_maintenance_takes_priority_over_update_lock(self):
        client = _make_client(_maintenance_requested=True, _update_locked=True)
        client._enter_maintenance = MagicMock()
        client._show_update_lock_screen = MagicMock()
        client._show_idle = MagicMock()

        client._return_to_available_state()

        client._enter_maintenance.assert_called_once()
        client._show_update_lock_screen.assert_not_called()
        client._show_idle.assert_not_called()

    def test_update_lock_wins_when_no_maintenance(self):
        client = _make_client(_maintenance_requested=False, _update_locked=True)
        client._enter_maintenance = MagicMock()
        client._show_update_lock_screen = MagicMock()

        client._return_to_available_state()

        client._show_update_lock_screen.assert_called_once()
        client._enter_maintenance.assert_not_called()

    def test_idle_when_neither_lock_active(self):
        client = _make_client(_maintenance_requested=False, _update_locked=False)
        client._enter_maintenance = MagicMock()
        client._show_update_lock_screen = MagicMock()
        client._show_idle = MagicMock()

        client._return_to_available_state()

        client._show_idle.assert_called_once()
        client._enter_maintenance.assert_not_called()
        client._show_update_lock_screen.assert_not_called()


class SessionAdmissionRefusalTests(unittest.TestCase):
    """3. Client refuses to start a new session while maintenance is requested."""

    def test_submit_code_refuses_when_maintenance_requested(self):
        client = _make_client(_state=IDLE, _maintenance_requested=True)
        client._enter_maintenance = MagicMock()
        client._proceed_code_validate = MagicMock()
        client._code_var = MagicMock()
        client._code_var.get.return_value = "123-456"

        client._submit_code()

        client._enter_maintenance.assert_called_once()
        client._proceed_code_validate.assert_not_called()
        client.api.validate.assert_not_called()

    def test_begin_open_session_refuses_when_maintenance_requested(self):
        client = _make_client(_state=IDLE, _maintenance_requested=True)
        client._enter_maintenance = MagicMock()
        client._proceed_open_session = MagicMock()

        client._begin_open_session()

        client._enter_maintenance.assert_called_once()
        client._proceed_open_session.assert_not_called()
        client.api.open_start.assert_not_called()


class ServerRejectionConversionTests(unittest.TestCase):
    """Defense in depth: a stale/racy client still honors a server-side
    station_maintenance rejection instead of a generic error."""

    def test_validate_rejection_enters_maintenance(self):
        client = _make_client()
        client.api.validate.return_value = {"ok": False, "error": "station_maintenance"}
        client._enter_maintenance = MagicMock()
        client._idle_with_error = MagicMock()

        client._validate_and_start("123456")

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertIs(callback, client._enter_maintenance)
        self.assertTrue(client._maintenance_requested)
        client._idle_with_error.assert_not_called()

    def test_open_start_rejection_enters_maintenance(self):
        client = _make_client()
        client.api.open_start.return_value = {"ok": False, "error": "station_maintenance"}
        client._enter_maintenance = MagicMock()
        client._show_needs_attention = MagicMock()

        client._do_open_session()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertIs(callback, client._enter_maintenance)
        self.assertTrue(client._maintenance_requested)


class HeartbeatMaintenanceTransitionTests(unittest.TestCase):
    """1. Idle + requested -> enter maintenance. 2. Active session untouched."""

    def test_idle_station_enters_maintenance_when_requested(self):
        client = _make_client(_state=IDLE)
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=True)
        client._enter_maintenance = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertIs(callback, client._enter_maintenance)

    def test_active_session_not_interrupted_by_maintenance_request(self):
        client = _make_client(_state=SESSION_ACTIVE, _maintenance_requested=False)
        client.api.heartbeat.return_value = _heartbeat_body(
            station_status="in_use", maintenance_requested=True,
        )
        client._enter_maintenance = MagicMock()
        client._show_needs_attention = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client._enter_maintenance.assert_not_called()
        client._show_needs_attention.assert_not_called()
        # The lock is recorded so it's honored once the session ends.
        self.assertTrue(client._maintenance_requested)

    def test_maintenance_active_exits_when_server_clears_request(self):
        client = _make_client(_state=MAINTENANCE_ACTIVE, _maintenance_requested=True)
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=False)
        client._exit_maintenance = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertIs(callback, client._exit_maintenance)

    def test_maintenance_active_stays_put_while_still_requested(self):
        client = _make_client(_state=MAINTENANCE_ACTIVE, _maintenance_requested=True)
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=True)
        client._exit_maintenance = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_not_called()
        client._exit_maintenance.assert_not_called()

    def test_needs_attention_takes_priority_during_maintenance(self):
        client = _make_client(_state=MAINTENANCE_ACTIVE, _maintenance_requested=True)
        client.api.heartbeat.return_value = _heartbeat_body(
            station_status="needs_attention", maintenance_requested=True,
        )
        client._exit_maintenance = MagicMock()
        client._show_needs_attention = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()
        client._exit_maintenance.assert_not_called()

    def test_heartbeat_reports_confirmed_maintenance_active(self):
        client = _make_client(_state=MAINTENANCE_ACTIVE, _maintenance_requested=True,
                              _maintenance_confirmed=True, _session_id=None,
                              # Recent, so this test doesn't also trigger a
                              # real background refresh thread as a side effect.
                              _last_override_refresh=gracelab_client.time.time())
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=True)
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.api.heartbeat.assert_called_once_with("available", None, maintenance_active=True)

    def test_heartbeat_does_not_report_active_until_confirmed(self):
        """4. self._state == MAINTENANCE_ACTIVE alone must not be reported
        as maintenance_active — only _maintenance_confirmed does."""
        client = _make_client(_state=MAINTENANCE_ACTIVE, _maintenance_requested=True,
                              _maintenance_confirmed=False, _session_id=None)
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=True)
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.api.heartbeat.assert_called_once_with("available", None, maintenance_active=False)

    def test_update_pending_state_enters_maintenance_when_requested(self):
        client = _make_client(_state=UPDATE_PENDING, _update_locked=True,
                              _update_status="installing", _update_lock_screen_status="installing")
        client.api.heartbeat.return_value = _heartbeat_body(
            maintenance_requested=True, update_pending=True, update_status="installing",
        )
        client._enter_maintenance = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertIs(callback, client._enter_maintenance)


class SessionEndEntersMaintenanceTests(unittest.TestCase):
    """2 (tail)/6. When a session ends with maintenance requested, the
    client enters maintenance instead of idle or the update screen."""

    def test_run_reset_enters_maintenance_when_requested(self):
        client = _make_client(_maintenance_requested=True)
        client._run_script = MagicMock(return_value=True)
        client._enter_maintenance = MagicMock()
        client._show_idle = MagicMock()
        client._show_update_pending = MagicMock()

        client._run_reset("sess-1")
        _delay, callback = client.root.after.call_args[0]
        callback()

        client._enter_maintenance.assert_called_once()
        client._show_idle.assert_not_called()
        client._show_update_pending.assert_not_called()


class AdminOverrideHelperTests(unittest.TestCase):
    def test_override_active_when_fresh(self):
        client = _make_client()
        stat_result = MagicMock(st_mtime=gracelab_client.time.time() - 60)
        with patch.object(gracelab_client.os, "stat", return_value=stat_result):
            self.assertTrue(client._admin_override_active())

    def test_override_inactive_when_stale(self):
        client = _make_client()
        stat_result = MagicMock(st_mtime=gracelab_client.time.time() - 5 * 3600)
        with patch.object(gracelab_client.os, "stat", return_value=stat_result):
            self.assertFalse(client._admin_override_active())

    def test_override_inactive_when_file_missing(self):
        client = _make_client()
        with patch.object(gracelab_client.os, "stat", side_effect=OSError):
            self.assertFalse(client._admin_override_active())

    def test_ensure_guestlab_active_skips_switch_when_override_active(self):
        client = _make_client()
        client._admin_override_active = MagicMock(return_value=True)
        with patch.object(gracelab_client.subprocess, "run") as mock_run:
            client._ensure_guestlab_active()
        mock_run.assert_not_called()

    def test_ensure_guestlab_active_switches_when_no_override(self):
        client = _make_client()
        client._admin_override_active = MagicMock(return_value=False)
        with patch.object(gracelab_client.subprocess, "run") as mock_run:
            client._ensure_guestlab_active()
        mock_run.assert_called_once()


class EnterMaintenanceTests(unittest.TestCase):
    def test_enter_maintenance_shows_screen_and_spawns_worker(self):
        client = _make_client()
        client._show_maintenance_active = MagicMock()
        with patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._enter_maintenance()

        client._show_maintenance_active.assert_called_once()
        mock_thread.assert_called_once()
        # Bound methods aren't identical across accesses, but compare equal
        # for the same instance+function — assertEqual, not assertIs.
        self.assertEqual(mock_thread.call_args.kwargs["target"], client._do_enter_maintenance)

    def test_do_enter_maintenance_confirms_when_override_verified_and_switch_succeeds(self):
        client = _make_client()
        client._enable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=True)
        client._switch_to_admin_session = MagicMock(return_value=True)

        client._do_enter_maintenance()

        client._enable_admin_override.assert_called_once()
        client._admin_override_active.assert_called_once()
        client._switch_to_admin_session.assert_called_once()
        self.assertTrue(client._maintenance_confirmed)

    def test_do_enter_maintenance_survives_display_switch_failure_but_not_confirmed(self):
        """4. Display switch failure must not report a healthy confirmed state,
        but the override succeeded so this stays short of needs_attention."""
        client = _make_client()
        client._enable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=True)
        client._switch_to_admin_session = MagicMock(return_value=False)
        client.api.event = MagicMock()
        client._show_needs_attention = MagicMock()

        client._do_enter_maintenance()  # must not raise

        self.assertFalse(client._maintenance_confirmed)
        client.api.event.assert_called_once()
        self.assertEqual(client.api.event.call_args.args[1], "maintenance_enter_failed")
        client._show_needs_attention.assert_not_called()

    def test_do_enter_maintenance_fails_safe_when_helper_reports_ok_but_sentinel_missing(self):
        """2. The helper reporting success is not proof — the sentinel must
        be independently verified. A mismatch must not be treated as entered."""
        client = _make_client()
        client._enable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=False)  # sentinel not actually there
        client._switch_to_admin_session = MagicMock()
        client.api.event = MagicMock()

        client._do_enter_maintenance()

        client._switch_to_admin_session.assert_not_called()
        self.assertFalse(client._maintenance_confirmed)
        client.api.event.assert_called_once()
        self.assertEqual(client.api.event.call_args.args[1], "maintenance_override_failed")

    def test_do_enter_maintenance_fails_safe_when_helper_script_itself_fails(self):
        """2. Missing/failed sudo helper (e.g. sudoers not reprovisioned yet)
        must not be treated as entered maintenance."""
        client = _make_client()
        client._enable_admin_override = MagicMock(return_value=False)
        client._admin_override_active = MagicMock(return_value=False)
        client._switch_to_admin_session = MagicMock()
        client.api.event = MagicMock()

        client._do_enter_maintenance()

        client._switch_to_admin_session.assert_not_called()
        self.assertFalse(client._maintenance_confirmed)
        self.assertEqual(client.api.event.call_args.args[1], "maintenance_override_failed")

    def test_do_enter_maintenance_override_failure_shows_needs_attention_and_keeps_lock(self):
        client = _make_client(_maintenance_requested=True)
        client._enable_admin_override = MagicMock(return_value=False)
        client._admin_override_active = MagicMock(return_value=False)
        client.api.event = MagicMock()
        client._show_needs_attention = MagicMock()

        client._do_enter_maintenance()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()
        # Admission stays locked regardless of the failure.
        self.assertTrue(client._maintenance_requested)


class ExitMaintenanceTests(unittest.TestCase):
    def test_exit_maintenance_noop_when_not_active(self):
        client = _make_client(_state=IDLE)
        with patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._exit_maintenance()
        mock_thread.assert_not_called()

    def test_exit_maintenance_shows_resetting_and_spawns_worker(self):
        client = _make_client(_state=MAINTENANCE_ACTIVE)
        with patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._exit_maintenance()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertEqual(callback, client._show_resetting)
        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["target"], client._do_exit_maintenance)

    def test_do_exit_maintenance_success_returns_to_available_state(self):
        client = _make_client(_maintenance_requested=True, _maintenance_confirmed=True)
        client._switch_to_gracelab = MagicMock(return_value=True)
        client._switch_to_gracelab_with_retries = MagicMock()
        client._disable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=False)  # confirmed cleared
        client._run_script = MagicMock(return_value=True)
        client._show_needs_attention = MagicMock()

        client._do_exit_maintenance()

        client._switch_to_gracelab_with_retries.assert_not_called()
        client._disable_admin_override.assert_called_once()
        client._admin_override_active.assert_called_once()
        self.assertFalse(client._maintenance_requested)
        self.assertFalse(client._maintenance_confirmed)
        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        self.assertEqual(callback, client._return_to_available_state)
        client._show_needs_attention.assert_not_called()

    def test_do_exit_maintenance_retries_switch_on_first_failure(self):
        client = _make_client()
        client._switch_to_gracelab = MagicMock(return_value=False)
        client._switch_to_gracelab_with_retries = MagicMock(return_value=True)
        client._disable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=False)
        client._run_script = MagicMock(return_value=True)

        client._do_exit_maintenance()

        client._switch_to_gracelab_with_retries.assert_called_once()

    def test_do_exit_maintenance_switch_failure_shows_needs_attention_keeps_lock(self):
        client = _make_client(_maintenance_requested=True)
        client._switch_to_gracelab = MagicMock(return_value=False)
        client._switch_to_gracelab_with_retries = MagicMock(return_value=False)
        client._disable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=False)
        client._run_script = MagicMock(return_value=True)
        client._show_needs_attention = MagicMock()
        client.api.event = MagicMock()

        client._do_exit_maintenance()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()
        # Do not return the station to guest service on a failed exit.
        self.assertTrue(client._maintenance_requested)

    def test_do_exit_maintenance_override_still_active_shows_needs_attention_keeps_lock(self):
        """2. If the override can't be verified disabled, exit must be
        treated as failed and the station must not return to guest service."""
        client = _make_client(_maintenance_requested=True)
        client._switch_to_gracelab = MagicMock(return_value=True)
        client._disable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=True)  # still active!
        client._run_script = MagicMock(return_value=True)
        client._show_needs_attention = MagicMock()
        client.api.event = MagicMock()

        client._do_exit_maintenance()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()
        self.assertTrue(client._maintenance_requested)
        self.assertEqual(client.api.event.call_args.args[1], "maintenance_override_failed")

    def test_do_exit_maintenance_disable_script_failure_treated_as_override_still_active(self):
        client = _make_client(_maintenance_requested=True)
        client._switch_to_gracelab = MagicMock(return_value=True)
        client._disable_admin_override = MagicMock(return_value=False)
        client._admin_override_active = MagicMock(return_value=True)
        client._run_script = MagicMock(return_value=True)
        client._show_needs_attention = MagicMock()

        client._do_exit_maintenance()

        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()
        self.assertTrue(client._maintenance_requested)

    def test_do_exit_maintenance_guest_cleanup_failure_shows_needs_attention_clears_lock(self):
        """Override+display already confirmed fine here — this degrades to
        the same needs_attention path a normal reset-script failure uses,
        so the lock clears (mirrors _run_reset's existing behavior)."""
        client = _make_client(_maintenance_requested=True)
        client._switch_to_gracelab = MagicMock(return_value=True)
        client._disable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=False)
        client._run_script = MagicMock(return_value=False)  # end_script fails
        client._show_needs_attention = MagicMock()

        client._do_exit_maintenance()

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()
        self.assertFalse(client._maintenance_requested)


class LocalMaintenanceExitRequestTests(unittest.TestCase):
    def test_ignored_when_not_in_maintenance(self):
        client = _make_client(_state=IDLE)
        client._exit_maintenance = MagicMock()
        with patch.object(gracelab_client.os.path, "exists", return_value=True):
            client._check_local_maintenance_exit_request()
        client._exit_maintenance.assert_not_called()

    def test_noop_when_flag_absent(self):
        client = _make_client(_state=MAINTENANCE_ACTIVE)
        client._exit_maintenance = MagicMock()
        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._check_local_maintenance_exit_request()
        client._exit_maintenance.assert_not_called()

    def test_triggers_exit_and_reports_when_flagged(self):
        client = _make_client(_state=MAINTENANCE_ACTIVE)
        client._exit_maintenance = MagicMock()
        with patch.object(gracelab_client.os.path, "exists", return_value=True), \
             patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._check_local_maintenance_exit_request()
        client._exit_maintenance.assert_called_once()
        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["target"], client._report_local_maintenance_exit)


class StationCommandDispatchTests(unittest.TestCase):
    """Explicit allowlist — only reset_gracelab/reboot dispatch a handler."""

    def test_reset_gracelab_dispatches_handler_thread(self):
        client = _make_client()
        with patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._dispatch_station_command("cmd-1", "reset_gracelab")

        mock_thread.assert_called_once()
        kwargs = mock_thread.call_args.kwargs
        self.assertEqual(kwargs["target"], client._handle_reset_gracelab_command)
        self.assertEqual(kwargs["args"], ("cmd-1",))
        mock_thread.return_value.start.assert_called_once()

    def test_reboot_dispatches_handler_thread(self):
        client = _make_client()
        with patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._dispatch_station_command("cmd-2", "reboot")

        kwargs = mock_thread.call_args.kwargs
        self.assertEqual(kwargs["target"], client._handle_reboot_command)
        self.assertEqual(kwargs["args"], ("cmd-2",))

    def test_unknown_command_type_reports_failed_and_spawns_nothing(self):
        client = _make_client()
        client.api.command_status = MagicMock()
        with patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._dispatch_station_command("cmd-3", "delete_everything")

        mock_thread.assert_not_called()
        client.api.command_status.assert_called_once_with(
            "cmd-3", "failed", "Unsupported command type: delete_everything")

    def test_heartbeat_dispatches_command_once_for_same_id(self):
        """10. Command replay safety — the client-side in-memory guard."""
        client = _make_client(_state=IDLE)
        client.api.heartbeat.return_value = _heartbeat_body(
            station_command={"id": "cmd-4", "type": "reset_gracelab"},
        )
        client._dispatch_station_command = MagicMock()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False):
            client._send_heartbeat()
            client._send_heartbeat()

        client._dispatch_station_command.assert_called_once_with("cmd-4", "reset_gracelab")


class HandleResetGracelabCommandTests(unittest.TestCase):
    """7. Remote GraceLab reset / 8. Reset failure safety."""

    def test_reset_command_acknowledges_then_completes_on_success(self):
        client = _make_client(_state=IDLE)
        client._end_and_reset = MagicMock(return_value=True)

        client._handle_reset_gracelab_command("cmd-5")

        client._end_and_reset.assert_called_once_with("remote_reset")
        calls = [c.args for c in client.api.command_status.call_args_list]
        self.assertIn(("cmd-5", "acknowledged", None), calls)
        self.assertIn(("cmd-5", "complete", None), calls)

    def test_reset_command_reports_failed_when_teardown_fails(self):
        client = _make_client(_state=IDLE)
        client._end_and_reset = MagicMock(return_value=False)

        client._handle_reset_gracelab_command("cmd-6")

        calls = [c.args for c in client.api.command_status.call_args_list]
        self.assertTrue(any(c[0] == "cmd-6" and c[1] == "failed" for c in calls))

    def test_reset_command_ends_active_session_with_staff_message(self):
        client = _make_client(_state=SESSION_ACTIVE)
        client._end_and_reset = MagicMock(return_value=True)
        client._show_ending = MagicMock()
        client._show_resetting = MagicMock()

        client._handle_reset_gracelab_command("cmd-7")

        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_ending.assert_called_once()
        client._show_resetting.assert_not_called()


class HandleRebootCommandTests(unittest.TestCase):
    """9. Remote reboot: allowed idle, rejected while a session is active."""

    def test_reboot_rejected_when_session_active(self):
        client = _make_client(_state=SESSION_ACTIVE)
        client._run_script = MagicMock()

        client._handle_reboot_command("cmd-8")

        client.api.command_status.assert_called_once_with(
            "cmd-8", "failed", "Rejected: a guest session is currently active.")
        client._run_script.assert_not_called()

    def test_reboot_acknowledges_before_invoking_helper(self):
        """1. No false 'complete' before the helper is actually invoked —
        only 'acknowledged' is reported first."""
        client = _make_client(_state=IDLE)
        call_order = []
        client.api.command_status = MagicMock(
            side_effect=lambda *a: call_order.append(("report", a)))
        client._run_script = MagicMock(
            side_effect=lambda *a, **k: call_order.append(("script",)) or True)

        client._handle_reboot_command("cmd-9")

        self.assertEqual(call_order[0], ("report", ("cmd-9", "acknowledged", None)))
        self.assertEqual(call_order[1], ("script",))
        self.assertEqual(call_order[2], ("report", ("cmd-9", "complete", None)))

    def test_reboot_helper_success_reports_complete(self):
        client = _make_client(_state=IDLE)
        client._run_script = MagicMock(return_value=True)

        client._handle_reboot_command("cmd-10")

        calls = [c.args for c in client.api.command_status.call_args_list]
        self.assertIn(("cmd-10", "acknowledged", None), calls)
        self.assertIn(("cmd-10", "complete", None), calls)
        self.assertNotIn(("cmd-10", "failed"), [c[:2] for c in calls])

    def test_reboot_helper_failure_reports_failed_not_complete(self):
        """1. A helper that runs but fails must report 'failed', never a
        false 'complete'."""
        client = _make_client(_state=IDLE)
        client._run_script = MagicMock(return_value=False)

        client._handle_reboot_command("cmd-11")

        calls = [c.args for c in client.api.command_status.call_args_list]
        self.assertIn(("cmd-11", "acknowledged", None), calls)
        self.assertTrue(any(c[0] == "cmd-11" and c[1] == "failed" for c in calls))
        self.assertNotIn(("cmd-11", "complete", None), calls)

    def test_reboot_missing_helper_reports_failed_not_complete(self):
        """1. _run_script's default 'missing script = skip = success'
        semantics must NOT apply to reboot — required=True must be passed."""
        client = _make_client(_state=IDLE)
        client.cfg.get.return_value = ""  # no reboot_script configured

        client._handle_reboot_command("cmd-12")

        calls = [c.args for c in client.api.command_status.call_args_list]
        self.assertTrue(any(c[0] == "cmd-12" and c[1] == "failed" for c in calls))
        self.assertNotIn(("cmd-12", "complete", None), calls)

    def test_reboot_uses_required_run_script(self):
        client = _make_client(_state=IDLE)
        client._run_script = MagicMock(return_value=True)

        client._handle_reboot_command("cmd-13")

        client._run_script.assert_called_once()
        self.assertTrue(client._run_script.call_args.kwargs.get("required"))


class RunScriptRequiredTests(unittest.TestCase):
    """1. _run_script's required=True: missing/unconfigured must fail, not
    silently succeed — without changing the default (required=False) used
    by every other lifecycle script call site."""

    def test_required_false_missing_script_still_skips_successfully(self):
        client = _make_client()
        client.cfg.get.return_value = ""
        self.assertTrue(client._run_script("", "start"))

    def test_required_true_empty_path_fails(self):
        client = _make_client()
        client.api.event = MagicMock()
        ok = client._run_script("", "reboot", failure_event="reboot_failed", required=True)
        self.assertFalse(ok)
        client.api.event.assert_called_once()
        self.assertEqual(client.api.event.call_args.args[1], "reboot_failed")

    def test_required_true_nonexistent_file_fails(self):
        client = _make_client()
        client.api.event = MagicMock()
        ok = client._run_script("/no/such/reboot_station.sh", "reboot",
                                failure_event="reboot_failed", required=True)
        self.assertFalse(ok)
        client.api.event.assert_called_once()


class AdminOverrideRefreshTests(unittest.TestCase):
    """3. Bounded lease refresh for maintenance sessions outlasting the
    4-hour sentinel window."""

    def test_confirmed_maintenance_refreshes_after_interval_elapses(self):
        client = _make_client(
            _state=MAINTENANCE_ACTIVE, _maintenance_requested=True,
            _maintenance_confirmed=True,
            _last_override_refresh=gracelab_client.time.time()
                - gracelab_client.GraceLabClient.ADMIN_OVERRIDE_REFRESH_SECONDS - 1,
        )
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=True)
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False), \
             patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._send_heartbeat()

        mock_thread.assert_called_once()
        self.assertEqual(mock_thread.call_args.kwargs["target"], client._refresh_admin_override)

    def test_confirmed_maintenance_does_not_refresh_before_interval(self):
        client = _make_client(
            _state=MAINTENANCE_ACTIVE, _maintenance_requested=True,
            _maintenance_confirmed=True,
            _last_override_refresh=gracelab_client.time.time(),
        )
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=True)
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False), \
             patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._send_heartbeat()

        mock_thread.assert_not_called()

    def test_unconfirmed_maintenance_does_not_refresh(self):
        """Refresh is a lease *renewal*, not an entry retry — it must not
        fire before the initial entry has actually been confirmed."""
        client = _make_client(
            _state=MAINTENANCE_ACTIVE, _maintenance_requested=True,
            _maintenance_confirmed=False,
            _last_override_refresh=0,
        )
        client.api.heartbeat.return_value = _heartbeat_body(maintenance_requested=True)
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False), \
             patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._send_heartbeat()

        mock_thread.assert_not_called()

    def test_idle_state_never_refreshes(self):
        client = _make_client(
            _state=IDLE, _maintenance_confirmed=True, _last_override_refresh=0,
        )
        client.api.heartbeat.return_value = _heartbeat_body()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False), \
             patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._send_heartbeat()

        mock_thread.assert_not_called()

    def test_exiting_maintenance_stops_further_refresh(self):
        """Once _exit_maintenance has moved state off MAINTENANCE_ACTIVE
        (e.g. to RESETTING), the heartbeat's refresh branch can't fire."""
        client = _make_client(
            _state=gracelab_client.GraceLabClient.RESETTING,
            _maintenance_requested=False, _maintenance_confirmed=True,
            _last_override_refresh=0,
        )
        client.api.heartbeat.return_value = _heartbeat_body()
        client._schedule_heartbeat = MagicMock()

        with patch.object(gracelab_client.os.path, "exists", return_value=False), \
             patch.object(gracelab_client.threading, "Thread") as mock_thread:
            client._send_heartbeat()

        mock_thread.assert_not_called()

    def test_refresh_success_keeps_confirmed_true(self):
        client = _make_client(_maintenance_confirmed=True)
        client._enable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=True)

        client._refresh_admin_override()

        self.assertTrue(client._maintenance_confirmed)

    def test_refresh_updates_last_refresh_timestamp_even_on_failure(self):
        """Pacing must advance regardless of outcome, or a failing refresh
        would be retried every single heartbeat instead of on the bounded
        cadence."""
        client = _make_client(_maintenance_confirmed=True, _last_override_refresh=0)
        client._enable_admin_override = MagicMock(return_value=False)
        client._admin_override_active = MagicMock(return_value=False)

        before = gracelab_client.time.time()
        client._refresh_admin_override()

        self.assertGreaterEqual(client._last_override_refresh, before)

    def test_refresh_failure_clears_confirmed_and_reports(self):
        client = _make_client(_maintenance_confirmed=True)
        client._enable_admin_override = MagicMock(return_value=True)
        client._admin_override_active = MagicMock(return_value=False)  # verify fails
        client.api.event = MagicMock()

        client._refresh_admin_override()

        self.assertFalse(client._maintenance_confirmed)
        client.api.event.assert_called_once()
        self.assertEqual(client.api.event.call_args.args[1], "maintenance_enter_failed")

    def test_refresh_helper_failure_clears_confirmed(self):
        client = _make_client(_maintenance_confirmed=True)
        client._enable_admin_override = MagicMock(return_value=False)
        client._admin_override_active = MagicMock(return_value=False)

        client._refresh_admin_override()

        self.assertFalse(client._maintenance_confirmed)


if __name__ == "__main__":
    unittest.main()
