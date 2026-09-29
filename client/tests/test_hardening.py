"""Safety regressions for required lifecycle hooks and guest termination."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from test_client_lifecycle import _make_client


class RequiredLifecycleTests(unittest.TestCase):
    def make_client(self):
        client = _make_client(_sync_interval=12)
        client.cfg.get.return_value = ""
        client._save_session_state = MagicMock()
        client._write_guest_timer_file = MagicMock()
        client._clear_session_state = MagicMock()
        client._clear_guest_timer_file = MagicMock()
        client._show_needs_attention = MagicMock()
        client._start_session_active = MagicMock()
        client._return_to_available_state = MagicMock()
        return client

    def test_code_start_missing_hook_does_not_launch_desktop(self):
        client = self.make_client()
        client.api.validate.return_value = {"ok": True, "session_id": 1}
        client.api.start.return_value = {"ok": True, "expires_at": "2030-01-01T00:00:00"}
        client._validate_and_start("123-456")
        client.api.end.assert_called_once_with(1, "failed")
        callbacks = [call.args[1] for call in client.root.after.call_args_list]
        self.assertNotIn(client._start_session_active, callbacks)
        self.assertEqual(client.api.event.call_args.args[1], "start_script_failed")

    def test_fault_during_validation_shows_station_error(self):
        client = self.make_client()
        client.api.validate.return_value = {"ok": False, "error": "station_needs_attention"}
        client._validate_and_start("123-456")
        client.root.after.call_args.args[1]()
        client._show_needs_attention.assert_called_once()
        client.api.start.assert_not_called()

    def test_fault_between_validate_and_start_shows_station_error(self):
        client = self.make_client()
        client.api.validate.return_value = {"ok": True, "session_id": 1}
        client.api.start.return_value = {"ok": False, "error": "station_needs_attention"}
        client._validate_and_start("123-456")
        client.root.after.call_args.args[1]()
        client._show_needs_attention.assert_called_once()
        client._write_guest_timer_file.assert_not_called()

    def test_open_start_missing_hook_does_not_launch_desktop(self):
        client = self.make_client()
        client.api.open_start.return_value = {
            "ok": True, "session_id": 1, "expires_at": "2030-01-01T00:00:00",
        }
        client._do_open_session()
        client.api.end.assert_called_once_with(1, "failed")
        callbacks = [call.args[1] for call in client.root.after.call_args_list]
        self.assertNotIn(client._start_session_active, callbacks)

    def test_missing_end_hook_blocks_reset(self):
        client = self.make_client()
        client._switch_to_gracelab = MagicMock(return_value=True)
        client._run_reset = MagicMock()
        self.assertFalse(client._end_and_reset("expired"))
        client._run_reset.assert_not_called()
        self.assertEqual(client.api.event.call_args.args[1], "end_script_failed")

    def test_missing_reset_hook_never_reports_success_or_reopens(self):
        client = self.make_client()
        self.assertFalse(client._run_reset(1))
        events = [call.args[1] for call in client.api.event.call_args_list]
        self.assertIn("reset_script_failed", events)
        self.assertNotIn("profile_reset_success", events)
        callbacks = [call.args[1] for call in client.root.after.call_args_list]
        self.assertNotIn(client._return_to_available_state, callbacks)

    def test_malformed_hook_configuration_is_reported_as_failure(self):
        for command in ("   ", "sudo", "sudo '"):
            with self.subTest(command=command):
                client = self.make_client()
                self.assertFalse(client._run_script(command, "reset", required=True,
                                                    failure_event="reset_script_failed"))
                self.assertEqual(client.api.event.call_args.args[1], "reset_script_failed")

    def test_real_hook_exit_status_is_respected(self):
        with tempfile.TemporaryDirectory() as folder:
            hook = Path(folder) / "hook.sh"
            for status in (0, 1):
                with self.subTest(status=status):
                    hook.write_text(f"#!/bin/sh\nexit {status}\n")
                    hook.chmod(0o700)
                    client = self.make_client()
                    self.assertEqual(client._run_script(str(hook), "reset", required=True), status == 0)


class GuestTerminationTests(unittest.TestCase):
    def run_helper(self, process_status, account_status=0):
        common = Path(__file__).resolve().parents[1] / "scripts" / "common.sh"
        # All OS operations are shell stubs: never touch local accounts or processes.
        program = r'''
set -euo pipefail
source "$1"
id() { return "$ACCOUNT_STATUS"; }
pkill() { :; }
loginctl() { :; }
sleep() { :; }
pgrep() { return "$PROCESS_STATUS"; }
gl_log() { printf '%s\n' "$*" >&2; }
gl_kill_guest
echo cleanup-allowed
'''
        return subprocess.run(["bash", "-c", program, "test", str(common)],
                              env={**os.environ, "PROCESS_STATUS": str(process_status),
                                   "ACCOUNT_STATUS": str(account_status)},
                              text=True, capture_output=True, timeout=5)

    def test_absent_processes_allow_cleanup(self):
        result = self.run_helper(1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cleanup-allowed", result.stdout)

    def test_surviving_processes_block_cleanup(self):
        result = self.run_helper(0)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("cleanup-allowed", result.stdout)

    def test_failed_process_inspection_blocks_cleanup(self):
        result = self.run_helper(2)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("cleanup-allowed", result.stdout)

    def test_missing_guest_account_blocks_cleanup(self):
        result = self.run_helper(1, account_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("cleanup-allowed", result.stdout)
