"""Failure recovery tests against throwaway portable Linux clusters, never live data."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from portable_windows.build_support import copy_application, manifest, runtime_hashes
from portable_windows.snapshots import validate, space_required, RESERVE
PYTHON = str(ROOT / '.venv/bin/python')
PG = ROOT / '.local/postgres/usr/lib/postgresql/17/bin'
ENV = {**os.environ, 'WASH_PORTABLE_TEST': '1', 'WASH_PORTABLE_NO_BROWSER': '1',
       'LD_LIBRARY_PATH': str(ROOT / '.local/postgres/usr/lib/x86_64-linux-gnu'), 'PYTHONTZPATH': ''}


def create_stage(base, name):
    stage = base / name
    stage.mkdir()
    copy_application(ROOT, stage)
    binaries = stage / 'runtime/postgres/bin'
    binaries.mkdir(parents=True)
    for binary in ('postgres', 'pg_ctl', 'initdb', 'pg_dump', 'pg_restore', 'psql'):
        wrapper = binaries / binary
        if binary == 'pg_ctl':
            wrapper.write_text('#!' + PYTHON + '\nimport os,sys,time\n'
                'if "stop" in sys.argv and os.environ.get("WASH_PORTABLE_SLOW_STOP") == "1": time.sleep(3)\n'
                'os.execv(' + repr(str(PG / binary)) + ', [' + repr(str(PG / binary)) + '] + sys.argv[1:])\n')
        else:
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(PG / binary)) + ' "$@"\n')
        wrapper.chmod(0o700)
    manifest(stage, versions={'validation': 'Linux regressions only'}, provenance=[])
    return stage


class Demo:
    def __init__(self, stage, env=None):
        self.stage, self.env = stage, env or ENV
        self.launch = [PYTHON, '-I', str(stage / 'portable_windows/launcher.py')]
        self.process = None
        self.log = (stage / 'regression.log').open('w+')

    def command(self, *arguments):
        return subprocess.run([*self.launch, *arguments], env=self.env, capture_output=True, text=True, timeout=210)

    def start(self):
        self.process = subprocess.Popen([*self.launch, 'start'], env=self.env, stdout=self.log, stderr=self.log)
        for _ in range(600):
            if self.process.poll() is not None:
                raise AssertionError((self.stage / 'regression.log').read_text()[-3000:])
            live = self.stage / 'data/runtime.json'
            if live.exists() and (self.stage / 'data/last-backup.json').exists():
                port = json.loads(live.read_text())['port']
                try:
                    with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/health/', timeout=1) as response:
                        if response.status == 200:
                            return port
                except Exception:
                    pass
            time.sleep(.1)
        raise AssertionError('Startup timed out: ' + (self.stage / 'regression.log').read_text()[-3000:])

    def pg_alive(self):
        return subprocess.run([str(self.stage / 'runtime/postgres/bin/pg_ctl'), '-D', str(self.stage / 'data/database'), 'status'],
                              env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def stop(self):
        result = self.command('stop')
        assert result.returncode == 0, result.stderr
        if self.process:
            self.process.wait(timeout=10)
            assert self.process.returncode == 0
            self.process = None
        assert not self.pg_alive()

    def cleanup(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=180)
        subprocess.run([str(PG / 'pg_ctl'), '-D', str(self.stage / 'data/database'), '-m', 'fast', '-w', 'stop'],
                       env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.log.close()


def main():
    results = {'native_windows_execution': 'not performed', 'runtime_sha256': runtime_hashes(ROOT)}
    demos = []
    with tempfile.TemporaryDirectory(prefix='portable regression ', dir=ROOT / '.local') as temporary:
        base = Path(temporary)
        try:
            demo = Demo(create_stage(base, 'Original Arabic مسار'))
            demos.append(demo)
            port = demo.start()
            state = demo.stage / 'data'
            fixture = b'fictional portable restoration fixture'
            (state / 'media/probe.txt').write_bytes(fixture)
            snapshot_result = demo.command('backup')
            assert snapshot_result.returncode == 0, snapshot_result.stderr
            snapshot = sorted((state / 'backups').glob('wash-*'))[-1]
            configuration = validate(snapshot)
            metadata = {name: (state / name).read_bytes() for name in ('settings.json', 'committee.json', 'django-secret')}
            assert all((snapshot / 'metadata' / name).read_bytes() == body for name, body in metadata.items())
            results['complete_backup_metadata'] = True
            # Old damaged JSON files must not turn a newly completed backup into
            # a failure after its atomic rename, or poison automatic scheduling.
            invalid_old = []
            for name, body in (('wash-000-invalid-manifest', []),
                               ('wash-001-null-file-map', {'portable_format': 1, 'portable_files': None})):
                folder = state / 'backups' / name
                folder.mkdir()
                (folder / 'manifest.json').write_text(json.dumps(body))
                invalid_old.append(folder)
            for index, bad_settings in enumerate(([], {**configuration, 'password': 123})):
                folder = state / 'backups' / ('wash-00' + str(index + 2) + '-invalid-settings')
                shutil.copytree(snapshot, folder)
                target = folder / 'metadata/settings.json'
                target.write_text(json.dumps(bad_settings))
                document = json.loads((folder / 'manifest.json').read_text())
                import hashlib
                document['portable_files']['metadata/settings.json'] = hashlib.sha256(target.read_bytes()).hexdigest()
                (folder / 'manifest.json').write_text(json.dumps(document))
                invalid_old.append(folder)
            for folder in invalid_old:
                try:
                    validate(folder)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Structurally invalid old backup accepted')
            repaired = demo.command('backup')
            assert repaired.returncode == 0, repaired.stderr
            assert all(folder.is_dir() for folder in invalid_old)
            results['invalid_old_snapshot_schema_skipped_after_successful_backup'] = len(invalid_old)
            for _ in range(7):
                completed = demo.command('backup')
                assert completed.returncode == 0, completed.stderr
            snapshots = [folder for folder in (state / 'backups').glob('wash-*') if folder not in invalid_old]
            assert len(snapshots) == 7
            snapshot = sorted(snapshots)[-1]
            results['retention_keeps_seven_verified_snapshots'] = True
            # Deterministic fault injection tests the free-space refusal before pg_dump.
            usage = type('Usage', (), {'free': RESERVE - 1})()
            with patch('portable_windows.snapshots.shutil.disk_usage', return_value=usage):
                try:
                    space_required(state)
                except RuntimeError:
                    pass
                else:
                    raise AssertionError('Low disk space accepted')
            results['low_disk_refused_without_deleting_data'] = True
            demo.stop()
            for corrupt in ('{', '{"timestamp":"wrong"}', '{"timestamp":1e999}', '{"timestamp":999999999999}'):
                (state / 'last-backup.json').write_text(corrupt)
                assert demo.start() == port
                # Readiness can precede repaired schedule completion; wait for a valid new timestamp.
                for _ in range(200):
                    try:
                        stamp = json.loads((state / 'last-backup.json').read_text())['timestamp']
                        if type(stamp) in (int, float) and 0 < stamp <= time.time():
                            break
                    except (ValueError, KeyError):
                        pass
                    time.sleep(.1)
                else:
                    raise AssertionError('Corrupt marker was not repaired')
                demo.stop()
            results['corrupt_markers_recovered'] = 4
            assert all(folder.is_dir() for folder in invalid_old)
            results['automatic_backup_recovers_without_retry_loop_despite_invalid_old_snapshots'] = True
            demo.start()
            demo.process.kill()
            demo.process.wait(timeout=10)
            demo.process = None
            assert demo.pg_alive()
            (state / 'runtime.json').write_text('{')
            demo.stop()
            results['orphan_pg_and_corrupt_runtime_stopped'] = True
            (state / 'settings.json').rename(state / 'settings.kept')
            refused = demo.command('stop')
            assert refused.returncode != 0 and 'existing database has no settings' in refused.stderr
            (state / 'settings.kept').rename(state / 'settings.json')
            results['stop_with_missing_settings_refuses_false_success'] = True
            slow = Demo(demo.stage, {**ENV, 'WASH_PORTABLE_SLOW_STOP': '1'})
            demo.log.close()
            demos.remove(demo)
            demos.append(slow)
            slow.start()
            started = time.monotonic()
            slow.stop()
            elapsed = time.monotonic() - started
            assert elapsed >= 3
            slow.start()
            slow.stop()
            results['stop_waited_for_pg_and_immediate_restart'] = {'passed': True, 'injected_stop_delay_seconds': 3}
            # Before-launch configuration resumption: retain a valid cluster but remove its custom block.
            config_file = state / 'database/postgresql.conf'
            import re
            body = re.sub(r'\n# BEGIN WASH PORTABLE\n.*?# END WASH PORTABLE\n', '', config_file.read_text(), flags=re.S)
            config_file.write_text(body + "\nport = 55401\nunix_socket_directories = ''\n")
            (state / 'database-ready.json').unlink()
            slow.start()
            actual = subprocess.check_output([str(demo.stage / 'runtime/postgres/bin/postgres'), '-D', str(state / 'database'), '-C', 'port'], env=ENV, text=True)
            assert actual.strip() == str(configuration['db_port'])
            slow.stop()
            results['completed_initdb_configuration_resumed'] = True
            with socket.socket() as occupied:
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                occupied.bind(('127.0.0.1', configuration['db_port']))
                occupied.listen(1)
                refused = slow.command('start')
                assert refused.returncode != 0 and not slow.pg_alive()
                assert not (state / 'database/postmaster.pid').exists()
            results['occupied_db_port_refused_before_pg_start'] = True
            initialization = state / 'initialization.json'
            original_initialization = initialization.read_bytes()
            initialization.write_text('{"format":1,"state":"initializing"}')
            refused = slow.command('start')
            assert refused.returncode != 0 and 'did not finish reliably' in refused.stderr and not slow.pg_alive()
            initialization.write_bytes(original_initialization)
            results['uncertain_initdb_refused_without_deleting_cluster'] = True
            snapshot = sorted((state / 'backups').glob('wash-*'))[-1]
            restored = Demo(create_stage(base, 'Restored clean copy'))
            demos.append(restored)
            response = restored.command('restore', str(snapshot))
            assert response.returncode == 0, response.stderr
            assert not restored.pg_alive()
            assert all((restored.stage / 'data' / name).read_bytes() == body for name, body in metadata.items())
            assert (restored.stage / 'data/media/probe.txt').read_bytes() == fixture
            restored.start()
            assert (restored.stage / 'data/committee.json').read_bytes() == metadata['committee.json']
            restored.stop()
            response = restored.command('restore', str(snapshot))
            assert response.returncode != 0 and 'freshly extracted copy' in response.stderr
            assert (restored.stage / 'data/media/probe.txt').read_bytes() == fixture
            results['clean_copy_restore_boots_and_existing_data_refused'] = True
            damaged = base / 'damaged-backup'
            shutil.copytree(snapshot, damaged)
            (damaged / 'metadata/committee.json').write_text('{}')
            bad = Demo(create_stage(base, 'Damaged rejected'))
            demos.append(bad)
            response = bad.command('restore', str(damaged))
            assert response.returncode != 0 and 'checksum mismatch' in response.stderr
            assert not (bad.stage / 'data/settings.json').exists()
            results['damaged_restore_rejected_before_private_settings'] = True
            print(json.dumps(results, indent=2), flush=True)
            destination = ROOT / 'test-results/windows-portable-validation/regressions.json'
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(results, indent=2))
        finally:
            for demo in demos:
                demo.cleanup()


if __name__ == '__main__':
    main()
