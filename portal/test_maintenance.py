import subprocess
import sys
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from .maintenance import application_lock, SnapshotCoordinationMiddleware


class SnapshotCoordinationTests(SimpleTestCase):
    def test_backup_excludes_web_requests_across_processes_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'write.lock')
            with override_settings(WASH_WRITE_LOCK=path, WASH_BACKUP_WAIT_SECONDS=0):
                worker = subprocess.Popen([sys.executable, '-c',
                    'import fcntl,sys; f=open(sys.argv[1],"w");fcntl.flock(f,fcntl.LOCK_EX);print("ready",flush=True);sys.stdin.read()', path],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(worker.stdout.readline().strip(), 'ready')
                    middleware = SnapshotCoordinationMiddleware(lambda request: 'view reached')
                    response = middleware(None)
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response['Cache-Control'], 'no-store, private')
                    with self.assertRaises(TimeoutError):
                        with application_lock(exclusive=True, timeout=0):
                            pass
                finally:
                    worker.communicate('', timeout=5)
                self.assertEqual(middleware(None), 'view reached')
                with application_lock(exclusive=True, timeout=0):
                    pass
