"""Check the native launcher's credential and startup boundaries without services."""
import os
import contextlib
import io
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import native


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.patch = patch.object(native, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_literal_credentials_and_native_paths(self):
        (self.root / 'connection.env').write_text('SEG_SERVICE_TOKEN=$literal#"token\nSEG_SCRATCH=/work\nSEG_MODEL_CACHE=/models\n')
        (self.root / '.env').write_text('SEG_GPU_DEVICE=2\n')
        env = native.environment()
        self.assertEqual(env['SEG_SERVICE_TOKEN'], '$literal#"token')
        self.assertEqual(env['SEG_SCRATCH'], str(self.root / 'work'))
        self.assertEqual(env['SEG_MODEL_CACHE'], str(self.root / 'models'))
        self.assertEqual(env['CUDA_VISIBLE_DEVICES'], '2')
        self.assertEqual((self.root / 'connection.env').stat().st_mode & 0o777, 0o600)

    def test_explicit_clinic_settings_override_runner_defaults(self):
        (self.root / '.env').write_text('CLINIC_SSH_TARGET=operator@192.168.1.2\nCLINIC_ROOT=/srv/clinic\n')
        with patch.dict(os.environ, CLINIC_SSH_TARGET='newuser@192.168.100.20', CLINIC_ROOT='/old'):
            self.assertEqual(native.clinic(), ('operator@192.168.1.2', '/srv/clinic'))

    def test_missing_receipt_prevents_migrations_and_workers(self):
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        (self.root / 'connection.env').write_text('SEG_SERVICE_TOKEN=example\n')
        with patch.object(sys, 'argv', ['native', 'start']), patch.object(native, 'run') as run:
            with self.assertRaises(FileNotFoundError):
                native.main()
            run.assert_not_called()

    def test_repeated_start_checks_readiness_without_migration_or_port_probe(self):
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        with patch.object(sys, 'argv', ['native', 'start']), \
             patch.object(native, 'environment', return_value={}), \
             patch('tools.check.handoff'), \
             patch.object(native, 'start_tunnel'), \
             patch.object(native, 'runtime_active', return_value=True), \
             patch.object(native, 'run') as run, \
             patch.object(native, 'install_units') as install:
            native.main()
        run.assert_called_once_with(sys.executable, '-m', 'tools.check', 'ready', '--port', '8096', env={})
        install.assert_not_called()

    def test_partial_runtime_is_not_reported_as_fully_running(self):
        result = subprocess.CompletedProcess([], 0, stdout='active\ninactive\nactive\nactive\n')
        with patch.object(native.subprocess, 'run', return_value=result):
            self.assertFalse(native.runtime_active())

    def test_occupied_port_has_specific_error(self):
        from tools import check
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            output = io.StringIO()
            with patch.object(sys, 'argv', ['check', 'port', '--port', str(listener.getsockname()[1])]), contextlib.redirect_stderr(output):
                with self.assertRaises(SystemExit) as error:
                    check.main()
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(output.getvalue().strip(), 'CHECK_FAILED: API_PORT_IN_USE')

    def test_units_use_current_python_and_are_stopped_together(self):
        with patch.object(Path, 'home', return_value=self.root), patch.object(native, 'systemctl') as ctl:
            native.install_units()
        directory = self.root / '.config/systemd/user'
        for role in native.ROLES:
            service = (directory / native.unit(role)).read_text()
            self.assertIn(sys.executable, service)
            self.assertIn('PartOf=' + native.TARGET, service)
            self.assertIn('KillMode=control-group' if role=='local-worker' else 'KillMode=mixed', service)
            self.assertNotIn('SEG_SERVICE_TOKEN', service)
        ctl.assert_called_once_with('daemon-reload')

    def test_invalid_download_preserves_existing_connection(self):
        path = self.root / 'connection.env'
        path.write_text('existing configuration')
        def download(*args):
            Path(args[-1]).write_text('SEG_SERVICE_TOKEN=incomplete\n')
        with patch.object(native, 'clinic', return_value=('user@host', '/srv/clinic')), patch.object(native, 'run', side_effect=download):
            with self.assertRaises(ValueError):
                native.fetch()
        self.assertEqual(path.read_text(), 'existing configuration')


if __name__ == '__main__':
    unittest.main()
