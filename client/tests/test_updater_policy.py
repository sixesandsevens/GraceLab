#!/usr/bin/env python3
"""
Tests for GraceLab Patch B: updater active-session safety.

The core bug this patch fixes: a per-station admin push used to set
"forced": true, which the updater treated as license to install over an
active session. should_install() is the single gate that now governs this —
see client/updater/updater.py.

Run with:
    python3 -m unittest discover -s client/tests
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "updater")))

import updater  # noqa: E402


class ShouldInstallTests(unittest.TestCase):
    """1. Updater active-session safety."""

    def test_station_targeted_update_does_not_bypass_active_session(self):
        proceed, reason = updater.should_install(
            policy="idle_only", station_targeted=True, session_active=True,
        )
        self.assertFalse(proceed)
        self.assertIn("Session active", reason)

    def test_manual_policy_station_targeted_still_defers_when_active(self):
        # This is the exact scenario that used to install mid-session: a
        # manual push (station_targeted=True) arriving while a session runs.
        proceed, reason = updater.should_install(
            policy="manual", station_targeted=True, session_active=True,
        )
        self.assertFalse(proceed)
        self.assertIn("Session active", reason)

    def test_normal_idle_only_update_defers_when_session_active(self):
        proceed, _reason = updater.should_install(
            policy="idle_only", station_targeted=False, session_active=True,
        )
        self.assertFalse(proceed)

    def test_idle_only_queued_update_installs_when_idle(self):
        """2. Idle queued update is eligible to install."""
        proceed, reason = updater.should_install(
            policy="idle_only", station_targeted=True, session_active=False,
        )
        self.assertTrue(proceed)
        self.assertIsNone(reason)

    def test_manual_policy_skips_when_nothing_queued(self):
        proceed, reason = updater.should_install(
            policy="manual", station_targeted=False, session_active=False,
        )
        self.assertFalse(proceed)
        self.assertIn("manual", reason)

    def test_manual_policy_installs_when_queued_and_idle(self):
        proceed, _reason = updater.should_install(
            policy="manual", station_targeted=True, session_active=False,
        )
        self.assertTrue(proceed)

    def test_disabled_policy_always_skips(self):
        proceed, reason = updater.should_install(
            policy="disabled", station_targeted=True, session_active=False,
        )
        self.assertFalse(proceed)
        self.assertIn("disabled", reason)

    def test_force_policy_bypasses_active_session(self):
        # "force" is a local, opt-in per-station config override — the only
        # thing that may still install during an active session.
        proceed, _reason = updater.should_install(
            policy="force", station_targeted=False, session_active=True,
        )
        self.assertTrue(proceed)


class IsSessionActiveTests(unittest.TestCase):
    """Patch A wrote GUEST_TIMER_FILE for both normal and open-mode
    sessions, so this check now catches open-mode sessions too."""

    def test_open_mode_session_file_counts_as_active(self):
        with patch.object(updater.os.path, "exists", return_value=True) as exists:
            self.assertTrue(updater.is_session_active())
            exists.assert_called_once_with(updater.GUEST_TIMER_FILE)

    def test_no_session_file_is_not_active(self):
        with patch.object(updater.os.path, "exists", return_value=False):
            self.assertFalse(updater.is_session_active())


if __name__ == "__main__":
    unittest.main()
