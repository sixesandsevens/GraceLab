#!/usr/bin/env python3
"""
Tests for GraceLab client session/timer lifecycle behavior.

Covers open-mode timer reliability and session teardown/display-switch
ordering. Uses stdlib unittest + unittest.mock only (no third-party test
deps are installed for this project).

Run with:
    python3 -m unittest discover -s client/tests
"""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import gracelab_client  # noqa: E402
import gracelab_timer  # noqa: E402


def _make_client(**attrs):
    """
    Build a GraceLabClient instance without running __init__ (which builds a
    live tkinter UI and talks to config/network). Tests set only the
    attributes and mocks they need on top of these lifecycle-relevant
    defaults.
    """
    client = gracelab_client.GraceLabClient.__new__(gracelab_client.GraceLabClient)
    client.cfg = MagicMock()
    client.cfg.get.return_value = ""
    client.api = MagicMock()
    client.root = MagicMock()
    client._session_id = "sess-1"
    client._expires_at = None
    client._warning_seconds = 300
    client._is_open_session = False
    client._update_locked = False
    client._update_status = None
    client._desired_client_version = None
    client._update_lock_screen_status = None
    for key, value in attrs.items():
        setattr(client, key, value)
    return client


class WriteGuestTimerFileTests(unittest.TestCase):
    """A. Normal session timer / B. Open-mode timer."""

    def setUp(self):
        patcher = patch.object(gracelab_client, "GUEST_TIMER_FILE",
                               "/tmp/gracelab-test-timer.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._cleanup_files)

    def _cleanup_files(self):
        for suffix in ("", ".tmp"):
            path = gracelab_client.GUEST_TIMER_FILE + suffix
            if os.path.exists(path):
                os.unlink(path)

    def test_normal_session_writes_timer_file(self):
        client = _make_client(_expires_at=9999999999.0, _is_open_session=False)
        client._write_guest_timer_file()

        with open(gracelab_client.GUEST_TIMER_FILE) as f:
            data = json.load(f)

        self.assertIn("expires_at", data)
        self.assertEqual(data["warning_seconds"], 300)
        self.assertFalse(data["open_mode"])
        self.assertFalse(os.path.exists(gracelab_client.GUEST_TIMER_FILE + ".tmp"))

    def test_open_mode_session_also_writes_timer_file(self):
        client = _make_client(_expires_at=9999999999.0, _is_open_session=True)
        client._write_guest_timer_file()

        self.assertTrue(os.path.exists(gracelab_client.GUEST_TIMER_FILE))
        with open(gracelab_client.GUEST_TIMER_FILE) as f:
            data = json.load(f)
        self.assertIn("expires_at", data)
        self.assertTrue(data["open_mode"])

    def test_no_expires_at_skips_write(self):
        client = _make_client(_expires_at=None, _is_open_session=True)
        client._write_guest_timer_file()
        self.assertFalse(os.path.exists(gracelab_client.GUEST_TIMER_FILE))


class TimerOverlayOpenModeTests(unittest.TestCase):
    """B. gracelab_timer.py no longer exits because the session is open mode."""

    def setUp(self):
        patcher = patch.object(gracelab_timer, "TIMER_FILE",
                               "/tmp/gracelab-test-overlay-timer.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._cleanup_file)

    def _cleanup_file(self):
        if os.path.exists(gracelab_timer.TIMER_FILE):
            os.unlink(gracelab_timer.TIMER_FILE)

    def test_main_does_not_exit_for_open_mode_session(self):
        with open(gracelab_timer.TIMER_FILE, "w") as f:
            json.dump({
                "expires_at": "2999-01-01T00:00:00",
                "warning_seconds": 300,
                "open_mode": True,
            }, f)

        with patch.object(gracelab_timer.tk, "Tk") as mock_tk, \
             patch.object(gracelab_timer, "TimerOverlay") as mock_overlay:
            mock_root = MagicMock()
            mock_tk.return_value = mock_root
            gracelab_timer.main()

        mock_tk.assert_called_once()
        mock_overlay.assert_called_once_with(mock_root)
        mock_root.mainloop.assert_called_once()


class SwitchToGracelabTests(unittest.TestCase):
    """C. Display switch success / D. Display switch failure."""

    def test_switch_reports_success_on_zero_returncode(self):
        client = _make_client()
        completed = subprocess.CompletedProcess(
            args=["dm-tool", "switch-to-user", "gracelab"],
            returncode=0, stdout="", stderr="",
        )
        with patch.object(gracelab_client.subprocess, "run", return_value=completed) as run, \
             self.assertLogs(gracelab_client.log, level="INFO") as logs:
            ok = client._switch_to_gracelab()

        self.assertTrue(ok)
        run.assert_called_once()
        self.assertTrue(any("Switched display to gracelab" in m for m in logs.output))

    def test_switch_reports_failure_on_nonzero_returncode(self):
        client = _make_client()
        completed = subprocess.CompletedProcess(
            args=["dm-tool", "switch-to-user", "gracelab"],
            returncode=1, stdout="", stderr="seat busy",
        )
        with patch.object(gracelab_client.subprocess, "run", return_value=completed), \
             self.assertLogs(gracelab_client.log, level="WARNING") as logs:
            ok = client._switch_to_gracelab()

        self.assertFalse(ok)
        self.assertTrue(any("exited 1" in m for m in logs.output))
        # Must never claim success when the subprocess actually failed.
        self.assertFalse(any("Switched display to gracelab" in m for m in logs.output))

    def test_switch_handles_timeout_without_raising(self):
        client = _make_client()
        with patch.object(gracelab_client.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(cmd="dm-tool", timeout=5)), \
             self.assertLogs(gracelab_client.log, level="WARNING") as logs:
            ok = client._switch_to_gracelab()

        self.assertFalse(ok)
        self.assertTrue(any("timed out" in m for m in logs.output))

    def test_switch_with_retries_stops_at_first_success(self):
        client = _make_client()
        client._switch_to_gracelab = MagicMock(side_effect=[False, True])
        with patch.object(gracelab_client.time, "sleep") as sleep:
            ok = client._switch_to_gracelab_with_retries(attempts=3, delay=1.5)

        self.assertTrue(ok)
        self.assertEqual(client._switch_to_gracelab.call_count, 2)
        sleep.assert_called_once_with(1.5)

    def test_switch_with_retries_exhausts_bounded_attempts(self):
        client = _make_client()
        client._switch_to_gracelab = MagicMock(return_value=False)
        with patch.object(gracelab_client.time, "sleep") as sleep:
            ok = client._switch_to_gracelab_with_retries(attempts=3, delay=1.5)

        self.assertFalse(ok)
        self.assertEqual(client._switch_to_gracelab.call_count, 3)
        self.assertEqual(sleep.call_count, 2)  # no sleep after the last attempt


class EndAndResetOrderingTests(unittest.TestCase):
    """E. Teardown ordering / F. existing behavior does not regress."""

    def test_switch_attempted_before_termination_script(self):
        client = _make_client()
        calls = []

        client._switch_to_gracelab = MagicMock(
            side_effect=lambda **kw: calls.append("switch") or True)
        client._switch_to_gracelab_with_retries = MagicMock(
            side_effect=AssertionError(
                "retry loop must not run when the pre-termination switch succeeded"))
        client._run_script = MagicMock(
            side_effect=lambda script, label, **kw: calls.append(f"script:{label}") or True)
        client._clear_session_state = MagicMock()
        client._clear_guest_timer_file = MagicMock()
        client._run_reset = MagicMock()

        client._end_and_reset("expired")

        self.assertEqual(calls, ["switch", "script:end"])
        client._clear_guest_timer_file.assert_called_once()
        client.api.end.assert_called_once_with("sess-1", "expired")
        client._run_reset.assert_called_once_with("sess-1")

    def test_guest_termination_still_proceeds_when_pre_switch_fails(self):
        client = _make_client()
        calls = []

        client._switch_to_gracelab = MagicMock(
            side_effect=lambda **kw: calls.append("switch") or False)
        client._switch_to_gracelab_with_retries = MagicMock(
            side_effect=lambda **kw: calls.append("retry") or True)
        client._run_script = MagicMock(
            side_effect=lambda script, label, **kw: calls.append(f"script:{label}") or True)
        client._clear_session_state = MagicMock()
        client._clear_guest_timer_file = MagicMock()
        client._run_reset = MagicMock()

        client._end_and_reset("expired")

        # Guest termination must still happen even though the initial display
        # switch failed (time-limit enforcement wins), and display recovery is
        # only retried afterward.
        self.assertEqual(calls, ["switch", "script:end", "retry"])
        client._run_reset.assert_called_once_with("sess-1")

    def test_needs_attention_shown_and_reset_skipped_when_termination_script_fails(self):
        client = _make_client()
        client._switch_to_gracelab = MagicMock(return_value=True)
        client._switch_to_gracelab_with_retries = MagicMock(return_value=True)
        client._run_script = MagicMock(return_value=False)
        client._clear_session_state = MagicMock()
        client._clear_guest_timer_file = MagicMock()
        client._run_reset = MagicMock()
        client._show_needs_attention = MagicMock()

        client._end_and_reset("expired")

        client._run_reset.assert_not_called()
        client.root.after.assert_called_once()
        _delay, callback = client.root.after.call_args[0]
        callback()
        client._show_needs_attention.assert_called_once()


if __name__ == "__main__":
    unittest.main()
