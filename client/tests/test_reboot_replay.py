"""Exercise real durable receipts across simulated client and machine restarts."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from test_client_lifecycle import _make_client
from gracelab_client import APIError, GraceLabClient
from reboot_guard import RebootGuard


class RebootReplayTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'reboot-command.json'

    def client(self, boot='boot-a'):
        client = _make_client(_state=GraceLabClient.IDLE)
        client._reboot_guard = RebootGuard(self.path, boot_id=boot)
        client._run_script = MagicMock(return_value=True)
        return client

    def test_shutdown_before_completion_report_does_not_reboot_again(self):
        first = self.client()
        first._run_script.side_effect = SystemExit('machine shut down before helper returned')
        with self.assertRaises(SystemExit):
            first._handle_reboot_command('command-a')
        second = self.client('boot-b')
        second._handle_reboot_command('command-a')
        second._run_script.assert_not_called()
        second.api.command_status.assert_called_once_with('command-a', 'complete', None)
        self.assertFalse(second._maintenance_requested)

    def test_same_boot_process_crash_never_reexecutes_ambiguous_command(self):
        first = self.client()
        first._run_script.side_effect = SystemExit()
        with self.assertRaises(SystemExit):
            first._handle_reboot_command('command-a')
        second = self.client()
        second._handle_reboot_command('command-a')
        second._run_script.assert_not_called()
        self.assertEqual(second.api.command_status.call_args.args[1], 'failed')

    def test_lost_completion_retries_only_report(self):
        client = self.client()
        client.api.command_status.side_effect = [None, APIError('offline'), None]
        client._last_handled_command_id = 'command-a'
        client._handle_reboot_command('command-a')
        self.assertIsNone(client._last_handled_command_id)
        client._handle_reboot_command('command-a')
        client._run_script.assert_called_once()
        self.assertEqual(client.api.command_status.call_args.args, ('command-a', 'complete', None))

    def test_receipt_is_durable_before_helper_is_called(self):
        client = self.client()
        def helper(*args, **kwargs):
            self.assertTrue(self.path.exists())
            replay = RebootGuard(self.path, boot_id='boot-b').begin('command-a')
            self.assertEqual(replay['status'], 'complete')
            return True
        client._run_script.side_effect = helper
        client._handle_reboot_command('command-a')

    def test_storage_failure_prevents_reboot(self):
        client = self.client()
        with patch.object(client._reboot_guard, '_write', side_effect=OSError('disk full')):
            client._handle_reboot_command('command-a')
        client._run_script.assert_not_called()
        self.assertEqual(client.api.command_status.call_args.args[1], 'failed')

    def test_corrupt_receipt_prevents_reboot(self):
        self.path.write_text('{broken')
        client = self.client()
        client._handle_reboot_command('command-a')
        client._run_script.assert_not_called()

    def test_failed_helper_is_not_retried_after_boot(self):
        first = self.client()
        first._run_script.return_value = False
        first._handle_reboot_command('command-a')
        self.assertFalse(first._reboot_pending)
        second = self.client('boot-b')
        second._handle_reboot_command('command-a')
        second._run_script.assert_not_called()
        self.assertEqual(second.api.command_status.call_args.args[1], 'failed')

    def test_new_explicit_command_can_reboot_again(self):
        first = self.client()
        first._handle_reboot_command('command-a')
        second = self.client('boot-b')
        second._handle_reboot_command('command-b')
        second._run_script.assert_called_once()

    def test_reboot_blocks_all_local_admission_entrypoints(self):
        for method, args in (('_begin_open_session', ()), ('_submit_code', ()),
                             ('_proceed_open_session', ()), ('_proceed_code_validate', ('123-456',))):
            with self.subTest(method=method):
                client = self.client()
                client._reboot_pending = True
                client._show_reboot_pending = MagicMock()
                getattr(client, method)(*args)
                client._show_reboot_pending.assert_called_once()
                client.api.open_start.assert_not_called()
                client.api.validate.assert_not_called()

    def test_pending_reboot_rejection_does_not_enter_maintenance(self):
        for method, args, api_method in (('_do_open_session', (), 'open_start'),
                                         ('_validate_and_start', ('123-456',), 'validate')):
            with self.subTest(method=method):
                client = self.client()
                getattr(client.api, api_method).return_value = {'ok': False, 'error': 'station_command_pending'}
                client._show_command_pending = MagicMock()
                getattr(client, method)(*args)
                client.root.after.call_args.args[1]()
                client._show_command_pending.assert_called_once()
                self.assertFalse(client._maintenance_requested)
