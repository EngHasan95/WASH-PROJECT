"""Folder-local demo supervisor: no services, registry, firewall or global PATH."""
import csv
import hashlib
import io
import json
import os
import math
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'app'
STATE = ROOT / 'data'
CONFIG = STATE / 'settings.json'
LIVE = STATE / 'runtime.json'
DB = STATE / 'database'
DBNAME = 'wash_committee_windows'
sys.path[:0] = [str(ROOT), str(APP)]
from portable_windows.locking import file_lock


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(data, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


def protect_state():
    if os.name != 'nt':
        if os.environ.get('WASH_PORTABLE_TEST') != '1':
            raise RuntimeError('This release requires Windows 10/11 x64.')
        STATE.mkdir(mode=0o700, exist_ok=True)
        STATE.chmod(0o700)
        return
    import ctypes
    from ctypes import wintypes
    if sys.maxsize <= 2**32 or sys.getwindowsversion().major < 10:
        raise RuntimeError('This release requires Windows 10/11 x64.')
    if str(ROOT).startswith('\\\\'):
        raise RuntimeError('Extract to a local folder, not a network share.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    filesystem = ctypes.create_unicode_buffer(32)
    kernel.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationW.restype = wintypes.BOOL
    if not kernel.GetVolumeInformationW(ROOT.anchor, None, 0, None, None, None, filesystem, 32):
        raise ctypes.WinError(ctypes.get_last_error())
    if filesystem.value != 'NTFS':
        raise RuntimeError('Use a local NTFS folder so private demo data can be protected.')
    # ACL applies only to this package's data folder, never system/user settings.
    STATE.mkdir(exist_ok=True)
    if STATE.is_symlink() or STATE.stat().st_file_attributes & 0x400:
        raise RuntimeError('The data folder must not be a junction or symlink.')
    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'],
                                       text=True, encoding='utf-8', errors='replace')
    sid = list(csv.reader(io.StringIO(identity)))[0][1]
    if not sid.startswith('S-1-') or not all(piece.isdigit() for piece in sid.split('-')[1:]):
        raise RuntimeError('Could not identify the current Windows account.')
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    security.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    security.SetFileSecurityW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        'D:P(A;OICI;FA;;;' + sid + ')(A;OICI;FA;;;SY)', 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not security.SetFileSecurityW(str(STATE), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


def verify_package():
    manifest = json.loads((ROOT / 'package-manifest.json').read_text(encoding='utf-8'))
    if manifest['format'] != 1 or manifest['platform'] != 'windows-amd64':
        raise RuntimeError('Invalid portable package manifest.')
    for name, expected in manifest['files'].items():
        path = ROOT / name
        if not path.resolve().is_relative_to(ROOT.resolve()) or path.is_symlink():
            raise RuntimeError('Invalid package path.')
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != expected:
            raise RuntimeError('Package checksum failed for ' + name + '. Extract the complete ZIP again.')


def free_port(first):
    for port in range(first, first + 100):
        try:
            with socket.socket() as probe:
                if os.name == 'nt':
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError('No local demo port is available.')


def configuration():
    if CONFIG.exists():
        data = json.loads(CONFIG.read_text(encoding='utf-8'))
        if data.get('format') != 1 or not data.get('password') or not data.get('instance'):
            raise RuntimeError('Invalid private local settings. Do not recreate them against an existing database.')
        for key in ('http_port', 'db_port'):
            if not isinstance(data.get(key), int) or not 1024 <= data[key] <= 65535:
                raise RuntimeError('Invalid local demo port.')
        return data
    if DB.exists() and any(DB.iterdir()):
        raise RuntimeError('An existing database has no settings.json. Restore its original settings; no data was reset.')
    data = {'format': 1, 'password': secrets.token_hex(32), 'instance': secrets.token_hex(32),
            'http_port': free_port(8766), 'db_port': free_port(55434)}
    write_json(CONFIG, data)
    return data


def pg_binary(name):
    return ROOT / 'runtime' / 'postgres' / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))


def environment(data):
    from urllib.parse import quote
    result = os.environ.copy()
    result.update(DJANGO_SETTINGS_MODULE='portable_windows.settings', WASH_COMMITTEE_DEMO='1',
        WASH_COMMITTEE_STATE=str(STATE), PGSSLMODE='disable',
        DATABASE_URL='postgresql://wash:' + quote(data['password'], safe='')
        + '@127.0.0.1:' + str(data['db_port']) + '/' + DBNAME,
        PATH=str(pg_binary('postgres').parent) + os.pathsep + result.get('PATH', ''))
    result.pop('WASH_TRUST_PROXY', None)
    return result


def call(arguments, *, env=None, quiet=False):
    # Never print commands: a child process may receive secrets in its environment.
    subprocess.run([str(arg) for arg in arguments], cwd=APP, env=env,
        stdout=subprocess.DEVNULL if quiet else None, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)


def connect(data, name=DBNAME):
    import psycopg
    return psycopg.connect(host='127.0.0.1', port=data['db_port'], user='wash',
                           password=data['password'], dbname=name, sslmode='disable', connect_timeout=5)


def validate_database(data, name=DBNAME):
    with connect(data, name) as connection:
        directory = connection.execute('SHOW data_directory').fetchone()[0]
        if os.path.normcase(str(Path(directory).resolve())) != os.path.normcase(str(DB.resolve())):
            raise RuntimeError('The local port belongs to a different database. No process was stopped.')


def pg_status():
    return subprocess.run([str(pg_binary('pg_ctl')), '-D', str(DB), 'status'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0).returncode


def stop_database(data):
    if not (DB / 'PG_VERSION').exists():
        return
    status = pg_status()
    if status == 3:
        return
    if status != 0:
        raise RuntimeError('Could not inspect the private database process; nothing was stopped.')
    # Recover even an old orphan started on the wrong port. Confirm both the
    # pidfile's directory and an authenticated SHOW data_directory before stop.
    lines = (DB / 'postmaster.pid').read_text().splitlines()
    if len(lines) < 4 or Path(lines[1]).resolve() != DB.resolve():
        raise RuntimeError('Database ownership cannot be confirmed; nothing was stopped.')
    owned = dict(data, db_port=int(lines[3]))
    validate_database(owned, 'postgres')
    call([pg_binary('pg_ctl'), '-D', DB, '-m', 'fast', '-w', '-t', '60', 'stop'], quiet=True)
    if pg_status() != 3:
        raise RuntimeError('The private database has not finished stopping.')


def configure_database(data):
    # Reapply this small managed block after a completed initdb, even when the
    # first launch was interrupted before configuration. Never change a live PG.
    import re
    path = DB / 'postgresql.conf'
    if not path.exists() or not (DB / 'global/pg_control').exists():
        raise RuntimeError('Database initialization was interrupted before completion. Keep data/ for diagnosis; nothing was deleted.')
    text = re.sub(r'\n# BEGIN WASH PORTABLE\n.*?# END WASH PORTABLE\n', '',
                  path.read_text(encoding='utf-8'), flags=re.S)
    text += ("\n# BEGIN WASH PORTABLE\nlisten_addresses = '127.0.0.1'\nport = " + str(data['db_port'])
             + "\nunix_socket_directories = ''\nmax_connections = 30\nshared_buffers = '32MB'\npassword_encryption = 'scram-sha-256'\n# END WASH PORTABLE\n")
    for target, body in ((path, text), (DB / 'pg_hba.conf', 'host all all 127.0.0.1/32 scram-sha-256\n')):
        temporary = target.with_suffix('.wash-tmp')
        with temporary.open('w', encoding='utf-8') as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, target)
    actual = subprocess.check_output([str(pg_binary('postgres')), '-D', str(DB), '-C', 'port'], text=True).strip()
    if actual != str(data['db_port']):
        raise RuntimeError('PostgreSQL port configuration differs from local settings; database was not started.')
    with socket.socket() as probe:
        if os.name == 'nt':
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(('127.0.0.1', data['db_port']))
    write_json(STATE / 'database-ready.json', {'format': 1, 'db_port': data['db_port']})


def start_database(data):
    if not (DB / 'PG_VERSION').exists():
        if DB.exists() and any(DB.iterdir()):
            raise RuntimeError('Database initialization was interrupted. Keep data/ for diagnosis; nothing was deleted.')
        write_json(STATE / 'initialization.json', {'format': 1, 'state': 'initializing'})
        password_file = STATE / 'initial-password'
        # A stale password file can remain after process termination. Replace
        # it only while holding the launcher lock and before any cluster exists.
        with password_file.open('w', encoding='ascii') as output:
            output.write(data['password'] + '\n')
        password_file.chmod(0o600)
        try:
            call([pg_binary('initdb'), '-D', DB, '-U', 'wash', '--auth=scram-sha-256',
                  '--encoding=UTF8', '--locale=C', '--pwfile', password_file], quiet=True)
            write_json(STATE / 'initialization.json', {'format': 1, 'state': 'complete'})
        finally:
            password_file.unlink(missing_ok=True)
    initialization = STATE / 'initialization.json'
    if initialization.exists() and json.loads(initialization.read_text()).get('state') != 'complete':
        raise RuntimeError('Database initialization did not finish reliably. Keep data/ for diagnosis; nothing was deleted or started.')
    if (DB / 'PG_VERSION').read_text().strip() != '17':
        raise RuntimeError('This package requires its original PostgreSQL 17 data directory.')
    status = pg_status()
    if status == 3:
        configure_database(data)
        call([pg_binary('pg_ctl'), '-D', DB, '-l', STATE / 'postgres.log', '-w', '-t', '60', 'start'], quiet=True)
    elif status != 0:
        raise RuntimeError('Could not inspect the private database process.')
    validate_database(data, 'postgres')
    from psycopg import sql
    with connect(data, 'postgres') as connection:
        connection.autocommit = True
        if not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', [DBNAME]).fetchone():
            connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DBNAME)))
    validate_database(data)


def last_backup_time(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))['timestamp']
        if type(value) not in (int, float) or value < 0 or value > time.time() + 300 or not math.isfinite(value):
            raise ValueError('Invalid backup timestamp')
        return value
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        print('Backup schedule marker is invalid. A new safe snapshot will be attempted; database data was preserved.')
        return 0


def control(action):
    try:
        data = json.loads(LIVE.read_text(encoding='utf-8'))
        request = urllib.request.Request('http://127.0.0.1:' + str(data['port']) + '/__wash_local_control__/',
            data=json.dumps({'action': action}).encode(),
            headers={'X-Wash-Control': data['token'], 'Content-Type': 'application/json'}, method='POST')
        # A loopback request must not use an institution's external HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=5) as response:
            result = json.load(response)
        if result.get('instance') != data['instance']:
            raise RuntimeError('Unexpected application at the local port.')
        return data
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        raise RuntimeError('The portable demo is not running. Run Start.cmd first.') from None


def backup(data):
    validate_database(data)
    call([sys.executable, '-I', ROOT / 'portable_windows/runner.py', 'backup', STATE / 'backups'], env=environment(data))


def start(data):
    server = None
    thread = None
    snapshot = None
    snapshot_log = None
    stop = threading.Event()
    try:
        # Refuse a reused web port before touching the database or its configuration.
        with socket.socket() as probe:
            if os.name == 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', data['http_port']))
        print('Preparing the private local database...')
        if (STATE / 'restore-in-progress.json').exists():
            raise RuntimeError('A restore did not finish. Keep this data folder for diagnosis; restore into a new clean copy instead.')
        start_database(data)
        env = environment(data)
        os.environ.update(env)
        media = STATE / 'media/policies'
        media.mkdir(mode=0o700, parents=True, exist_ok=True)
        reference = media / 'violation-fees-source.jpg'
        if not reference.exists():
            import shutil
            shutil.copyfile(ROOT / 'reference/violation-fees-source.jpg', reference)
        import django
        django.setup()
        from django.core.management import call_command
        call_command('migrate', interactive=False, verbosity=0)
        call_command('seed_committee')
        call_command('check')
        from config.wsgi import application
        from waitress.server import TcpWSGIServer
        class LocalServer(TcpWSGIServer):
            def set_reuse_addr(self):
                if os.name == 'nt':
                    self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    super().set_reuse_addr()
        token = secrets.token_hex(32)

        def local_application(environ, start_response):
            if environ.get('PATH_INFO') != '/__wash_local_control__/':
                return application(environ, start_response)
            allowed = environ.get('REQUEST_METHOD') == 'POST' and environ.get('REMOTE_ADDR') == '127.0.0.1'
            allowed = allowed and environ.get('HTTP_HOST') in ('127.0.0.1:' + str(data['http_port']), 'localhost:' + str(data['http_port']))
            allowed = allowed and secrets.compare_digest(environ.get('HTTP_X_WASH_CONTROL', ''), token)
            action = None
            try:
                size = int(environ.get('CONTENT_LENGTH') or 0)
                if allowed and 0 < size <= 128:
                    action = json.loads(environ['wsgi.input'].read(size)).get('action')
            except (ValueError, AttributeError):
                pass
            if action not in ('status', 'stop'):
                start_response('403 Forbidden', [('Content-Type', 'text/plain'), ('Cache-Control', 'no-store')])
                return [b'Forbidden']
            if action == 'stop':
                stop.set()
            body = json.dumps({'instance': data['instance']}).encode()
            start_response('200 OK', [('Content-Type', 'application/json'),
                ('Cache-Control', 'no-store'), ('Content-Length', str(len(body)))])
            return [body]

        server = LocalServer(local_application, host='127.0.0.1', port=data['http_port'], threads=4,
            clear_untrusted_proxy_headers=True, max_request_body_size=12 * 1024 * 1024)
        thread = threading.Thread(target=server.run, name='wash-local-http', daemon=True)
        thread.start()
        write_json(LIVE, {'instance': data['instance'], 'port': data['http_port'], 'token': token})
        control('status')
        if os.environ.get('WASH_PORTABLE_NO_BROWSER') != '1':
            webbrowser.open('http://127.0.0.1:' + str(data['http_port']) + '/committee/')
        print('The committee guide is ready in your browser. Keep this window open.')
        print('Use Stop.cmd or Ctrl+C to stop. Your test data is preserved.')
        signal.signal(signal.SIGINT, lambda *args: stop.set())
        signal.signal(signal.SIGTERM, lambda *args: stop.set())
        marker = STATE / 'last-backup.json'
        next_check = 0
        while not stop.wait(.25):
            if not thread.is_alive():
                raise RuntimeError('The local web server stopped unexpectedly.')
            if snapshot is not None and snapshot.poll() is not None:
                if snapshot.returncode == 0:
                    write_json(marker, {'timestamp': time.time()})
                    write_json(STATE / 'backup-health.json', {'status': 'ok', 'timestamp': time.time()})
                else:
                    write_json(STATE / 'backup-health.json', {'status': 'failed', 'timestamp': time.time()})
                    print('Automatic backup failed. Check data/backup.log; test data was preserved.')
                snapshot_log.close()
                snapshot_log = None
                snapshot = None
            if snapshot is None and time.time() >= next_check:
                last = last_backup_time(marker)
                if time.time() - last >= 86400:
                    snapshot_log = (STATE / 'backup.log').open('ab')
                    snapshot = subprocess.Popen([sys.executable, '-I', str(ROOT / 'portable_windows/runner.py'),
                        'backup', str(STATE / 'backups')], cwd=APP, env=env, stdout=snapshot_log,
                        stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                next_check = time.time() + 30
    finally:
        try:
            if server is not None:
                server.task_dispatcher.shutdown(timeout=30)
                server.close()
            if thread is not None:
                thread.join(timeout=5)
            if snapshot is not None:
                try:
                    snapshot.wait(timeout=90)
                except subprocess.TimeoutExpired:
                    snapshot.terminate()
                    try:
                        snapshot.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        snapshot.kill()
                        snapshot.wait(timeout=10)
                    print('An unfinished snapshot was cancelled. Earlier complete snapshots and database data remain intact.')
        finally:
            if snapshot_log is not None:
                snapshot_log.close()
            try:
                stop_database(data)
            finally:
                LIVE.unlink(missing_ok=True)
        print('Stopped. Your test data remains in data/.')


def main():
    action = sys.argv[1] if len(sys.argv) >= 2 else ''
    if action not in ('start', 'stop', 'backup', 'restore') or len(sys.argv) > (3 if action == 'restore' else 2):
        raise RuntimeError('Use Start.cmd, Stop.cmd, Backup.cmd or Restore.cmd.')
    protect_state()
    if action == 'stop':
        try:
            control('stop')
        except RuntimeError:
            pass  # Lost supervisor: ownership is checked again under its OS lock.
        with file_lock(STATE / 'launcher.lock', exclusive=True, timeout=240):
            if CONFIG.exists() or (DB.exists() and any(DB.iterdir())):
                stop_database(configuration())
            LIVE.unlink(missing_ok=True)
        print('Stopped completely. It is safe to restart this copy.')
        return
    verify_package()
    if action == 'restore':
        from portable_windows.snapshots import restore
        source = sys.argv[2] if len(sys.argv) == 3 else input('Full path to the complete private backup folder: ').strip().strip('"')
        with file_lock(STATE / 'launcher.lock', exclusive=True, timeout=0):
            restore(source, sys.modules[__name__])
        return
    if action == 'backup':
        if not CONFIG.exists():
            raise RuntimeError('Run Start.cmd first. No settings were created.')
        backup(configuration())
        return
    try:
        with file_lock(STATE / 'launcher.lock', exclusive=True, timeout=0):
            start(configuration())
    except TimeoutError:
        running = control('status')
        if os.environ.get('WASH_PORTABLE_NO_BROWSER') != '1':
            webbrowser.open('http://127.0.0.1:' + str(running['port']) + '/committee/')
        print('This copy is already running; its existing browser guide was reopened.')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as error:
        print('Unable to run the portable demo:', str(error), file=sys.stderr)
        raise SystemExit(1)
