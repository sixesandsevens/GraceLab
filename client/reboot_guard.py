"""Durable at-most-once handling of dashboard reboot commands."""
import json
import os
from pathlib import Path
import tempfile


class RebootGuard:
    def __init__(self, path, boot_id=None):
        self.path = Path(path)
        self.boot_id = boot_id or Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def _read(self):
        try:
            record = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None
        if (not isinstance(record, dict) or not record.get('command_id')
                or not record.get('boot_id')
                or record.get('status') not in ('armed', 'complete', 'failed')):
            raise ValueError('Invalid reboot receipt; staff recovery required')
        return record

    def _write(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                os.unlink(temporary)

    def begin(self, command_id):
        """Return a saved terminal report, or persist intent before execution.

        A process restart on the same boot is ambiguous: do not execute again.
        A changed Linux boot ID proves the station has rebooted since the intent.
        """
        record = self._read()
        if record and record['command_id'] == command_id:
            if record['status'] == 'armed':
                if record['boot_id'] != self.boot_id:
                    record['status'] = 'complete'
                    record['error'] = None
                else:
                    record['status'] = 'failed'
                    record['error'] = 'Previous reboot attempt interrupted; issue a new command to retry.'
                self._write(record)
            return record
        self._write({'command_id': command_id, 'boot_id': self.boot_id,
                     'status': 'armed', 'error': None})
        return None

    def finish(self, command_id, status, error=None):
        if status not in ('complete', 'failed'):
            raise ValueError('Invalid reboot result')
        self._write({'command_id': command_id, 'boot_id': self.boot_id,
                     'status': status, 'error': error})
