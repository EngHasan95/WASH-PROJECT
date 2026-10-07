"""Validate portable lifecycle on Linux with an isolated native PostgreSQL cluster.

This exercises the portable supervisor/Waitress/adapters and original workflows;
it is not evidence of executing Windows binaries or ACL APIs on Windows.
"""
import hashlib
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from portable_windows.build_support import copy_application, manifest, runtime_hashes, application_hashes


def main():
    if os.name == 'nt':
        raise SystemExit('This is the Linux validation harness; run the real release Start.cmd on Windows.')
    results_folder = ROOT / 'test-results/windows-portable-validation'
    results_folder.mkdir(parents=True, exist_ok=True)
    (results_folder / 'results.json').unlink(missing_ok=True)
    temp = tempfile.TemporaryDirectory(prefix='portable check ', dir=ROOT / '.local')
    stage = Path(temp.name) / 'WASH Demo'
    stage.mkdir()
    copy_application(ROOT, stage)
    binaries = stage / 'runtime/postgres/bin'
    binaries.mkdir(parents=True)
    original = ROOT / '.local/postgres/usr/lib/postgresql/17/bin'
    for name in ('postgres', 'pg_ctl', 'initdb', 'pg_dump', 'pg_restore', 'psql'):
        # Keep Debian's original compiled share layout; only this test has wrappers.
        wrapper = binaries / name
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(original / name)) + ' "$@"\n')
        wrapper.chmod(0o700)
    manifest(stage, versions={'validation': 'Linux supervisor only'}, provenance=[])
    env = os.environ.copy()
    env.update(WASH_PORTABLE_TEST='1', WASH_PORTABLE_NO_BROWSER='1',
        LD_LIBRARY_PATH=str(ROOT / '.local/postgres/usr/lib/x86_64-linux-gnu'),
        PYTHONTZPATH='')  # Exercise the bundled timezone database Windows needs.
    python = str(ROOT / '.venv/bin/python')
    launch = [python, '-I', str(stage / 'portable_windows/launcher.py')]
    supervisor = None
    output = (stage / 'validation.log').open('w+')

    def stop():
        nonlocal supervisor
        if supervisor and supervisor.poll() is None:
            subprocess.run([*launch, 'stop'], env=env, check=True, stdout=subprocess.DEVNULL)
            supervisor.wait(timeout=90)
            assert supervisor.returncode == 0
        supervisor = None

    def start():
        nonlocal supervisor
        supervisor = subprocess.Popen([*launch, 'start'], env=env, stdout=output, stderr=output)
        for _ in range(300):
            if supervisor.poll() is not None:
                output.flush()
                pglog = stage / 'data/postgres.log'
                detail = pglog.read_text()[-2000:] if pglog.exists() else ''
                raise RuntimeError('Portable startup failed: ' + (stage / 'validation.log').read_text()[-2500:] + detail)
            live = stage / 'data/runtime.json'
            if live.exists():
                port = json.loads(live.read_text())['port']
                try:
                    with urllib.request.urlopen('http://127.0.0.1:' + str(port) + '/health/', timeout=1) as r:
                        if r.status == 200:
                            # The due automatic snapshot must finish before workflow checks.
                            marker = stage / 'data/last-backup.json'
                            if marker.exists():
                                return port
                except Exception:
                    pass
            time.sleep(.1)
        raise RuntimeError('Portable supervisor readiness timed out')

    try:
        port = start()
        settings_before = (stage / 'data/settings.json').read_bytes()
        subprocess.run([*launch, 'start'], env=env, check=True, stdout=subprocess.DEVNULL)
        assert settings_before == (stage / 'data/settings.json').read_bytes()
        print('PASS: first start, private cluster, automatic snapshot and duplicate Start', flush=True)
        spec = importlib.util.spec_from_file_location('committee_checks', ROOT / 'scripts/committee_browser_checks.py')
        checks = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checks)
        checks.BASE = 'http://127.0.0.1:' + str(port)
        checks.OUTPUT = ROOT / 'test-results/windows-portable-validation'
        checks.OUTPUT.mkdir(parents=True, exist_ok=True)
        checks.fixture = lambda: json.loads((stage / 'data/committee.json').read_text())
        original_playwright = checks.sync_playwright
        @contextmanager
        def diagnose_browser():
            with original_playwright() as playwright:
                browsers = []
                requests = []
                original_launch = playwright.chromium.launch
                def launch(*args, **kwargs):
                    browser = original_launch(*args, **kwargs)
                    browsers.append(browser)
                    original_context = browser.new_context
                    def context(*context_args, **context_kwargs):
                        created = original_context(*context_args, **context_kwargs)
                        def response(item):
                            from urllib.parse import urlsplit
                            requests.append({'path': urlsplit(item.url).path, 'status': item.status,
                                             'response_start_ms': item.request.timing['responseStart']})
                        created.on('response', response)
                        created.on('requestfailed', lambda item: requests.append(
                            {'path': urllib.parse.urlsplit(item.url).path, 'failure': item.failure}))
                        return created
                    browser.new_context = context
                    return browser
                playwright.chromium.launch = launch
                try:
                    yield playwright
                except Exception:
                    for browser in browsers:
                        for context in browser.contexts:
                            for page in context.pages:
                                page.screenshot(path=str(results_folder / 'failure.png'), full_page=True)
                                (results_folder / 'failure.txt').write_text(page.url + '\n' + page.locator('body').inner_text())
                    raise
                finally:
                    (results_folder / 'browser-requests.json').write_text(json.dumps(requests, indent=2))
        checks.sync_playwright = diagnose_browser

        def control(action, _):
            if action == 'stop':
                stop()
            elif action == 'start':
                assert start() == port
            else:
                raise AssertionError('Unexpected process action')
        checks.control = control
        checks.main()
        subprocess.run([*launch, 'backup'], env=env, check=True, stdout=subprocess.DEVNULL)
        snapshots = sorted((stage / 'data/backups').glob('wash-*'))
        configuration = json.loads((stage / 'data/settings.json').read_text())
        from urllib.parse import quote
        backup_env = {**env, 'DJANGO_SETTINGS_MODULE': 'portable_windows.settings',
            'WASH_COMMITTEE_DEMO': '1', 'WASH_COMMITTEE_STATE': str(stage / 'data'), 'PGSSLMODE': 'disable',
            'DATABASE_URL': 'postgresql://wash:' + quote(configuration['password']) + '@127.0.0.1:'
                            + str(configuration['db_port']) + '/wash_committee_windows',
            'PATH': str(binaries) + os.pathsep + env['PATH']}
        restore = subprocess.run([python, '-I', str(stage / 'portable_windows/runner.py'),
            'verify_backup', str(snapshots[-1])], env=backup_env, check=True, capture_output=True, text=True)
        restored = json.loads(restore.stdout)
        assert restored['restored'] and restored['document_count'] >= 1 and restored['media_files'] >= 1
        print('PASS: portable backup and real restore into a throwaway database', flush=True)
        # Backup consistency remains enforced across the original web middleware.
        worker = subprocess.Popen([python, '-c',
            'import fcntl,sys;f=open(sys.argv[1],"w");fcntl.flock(f,fcntl.LOCK_EX);print("ready",flush=True);sys.stdin.read()',
            str(stage / 'data/application-write.lock')], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            assert worker.stdout.readline().strip() == 'ready'
            try:
                urllib.request.urlopen(checks.BASE + '/health/', timeout=20)
            except urllib.error.HTTPError as e:
                assert e.code == 503 and e.headers['Cache-Control'] == 'no-store, private'
            else:
                raise AssertionError('Exclusive snapshot failed to pause web requests')
        finally:
            worker.communicate('', timeout=5)
        print('PASS: cross-process backup exclusion and safe HTTP 503', flush=True)
        stop()
        # Never generate a new password against a retained database.
        (stage / 'data/settings.json').rename(stage / 'data/settings-original.json')
        refused = subprocess.run([*launch, 'start'], env=env, capture_output=True, text=True)
        assert refused.returncode != 0 and 'existing database has no settings' in refused.stderr
        assert not (stage / 'data/settings.json').exists()
        (stage / 'data/settings-original.json').rename(stage / 'data/settings.json')
        start()
        assert settings_before == (stage / 'data/settings.json').read_bytes()
        print('PASS: stop/restart persistence and missing settings refusal', flush=True)
        stop()
        guide = stage / 'README.html'
        guide.write_bytes(guide.read_bytes() + b'corrupt')
        refused = subprocess.run([*launch, 'start'], env=env, capture_output=True, text=True)
        assert refused.returncode != 0 and 'checksum failed' in refused.stderr
        print('PASS: package tamper rejected before database startup', flush=True)
        regression_path = results_folder / 'regressions.json'
        if not regression_path.exists():
            raise RuntimeError('Run windows_portable_regression_checks.py before the portable workflow checks.')
        regressions = json.loads(regression_path.read_text())
        if regressions.get('runtime_sha256') != runtime_hashes(ROOT):
            raise RuntimeError('Portable recovery checks are stale; rerun windows_portable_regression_checks.py.')
        tested_application = application_hashes(stage / 'app')
        if tested_application != application_hashes(ROOT):
            raise RuntimeError('Application changed during validation; rerun the portable workflow checks.')
        result = {'portable_supervisor': 'validated on Linux, not native Windows',
            'failure_recovery': regressions,
            'runtime_sha256': runtime_hashes(ROOT), 'system_timezone_database': 'disabled; bundled tzdata used',
            'application_sha256': tested_application,
            'browser_groups': 6, 'backup_restore': restored,
            'checks': ['initial startup', 'automatic backup', 'duplicate start', 'full original workflows',
                       'exclusive snapshot', 'repeatable restart', 'lost configuration refusal', 'tamper rejection'],
            'native_windows_execution': 'not performed'}
        (ROOT / 'test-results/windows-portable-validation/results.json').write_text(json.dumps(result, indent=2))
    finally:
        try:
            stop()
        finally:
            output.flush()
            shutil.copyfile(stage / 'validation.log', results_folder / 'supervisor.log')
            # Clean only this harness's private cluster, even after failed startup.
            subprocess.run([str(binaries / 'pg_ctl'), '-D', str(stage / 'data/database'),
                            '-m', 'fast', '-w', 'stop'], env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            output.close()
            temp.cleanup()


if __name__ == '__main__':
    main()
