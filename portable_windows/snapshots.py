"""Portable-only complete private snapshots. Never replace an existing data folder."""
import hashlib
import json
import os
import socket
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
from datetime import datetime, timezone

METADATA = ('settings.json', 'committee.json', 'django-secret')
KEEP = 7
RESERVE = 256 * 1024 * 1024


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def validate(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest, dict):
        raise ValueError('Backup manifest must be a JSON object.')
    if type(manifest.get('portable_format')) is not int or manifest['portable_format'] != 1:
        raise ValueError('This is not a complete portable backup. Keep older backups for technical recovery.')
    expected_names = {'database.dump', 'media.tar.gz', *('metadata/' + name for name in METADATA)}
    checksums = manifest.get('portable_files')
    if not isinstance(checksums, dict):
        raise ValueError('Backup portable_files must be a JSON object of file checksums.')
    if set(checksums) != expected_names:
        raise ValueError('Backup metadata is incomplete.')
    for checksum in checksums.values():
        if not isinstance(checksum, str) or len(checksum) != 64 or any(character not in '0123456789abcdef' for character in checksum):
            raise ValueError('Backup file checksums must be lowercase SHA256 strings.')
    if manifest.get('database_sha256') != checksums['database.dump'] or manifest.get('media_sha256') != checksums['media.tar.gz']:
        raise ValueError('Backup manifest checksum fields do not agree.')
    for name, checksum in checksums.items():
        path = directory / name
        if path.is_symlink() or not path.resolve().is_relative_to(directory) or digest(path) != checksum:
            raise ValueError('Backup checksum mismatch: ' + name)
    settings = json.loads((directory / 'metadata/settings.json').read_text(encoding='utf-8'))
    if not isinstance(settings, dict):
        raise ValueError('Backup private settings must be a JSON object.')
    if type(settings.get('format')) is not int or settings['format'] != 1:
        raise ValueError('Backup private settings format is invalid.')
    for key in ('password', 'instance'):
        value = settings.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(character not in '0123456789abcdef' for character in value):
            raise ValueError('Backup private settings ' + key + ' must be a valid local token.')
    for key in ('http_port', 'db_port'):
        if type(settings.get(key)) is not int or not 1024 <= settings[key] <= 65535:
            raise ValueError('Backup port is invalid.')
    committee = json.loads((directory / 'metadata/committee.json').read_text(encoding='utf-8'))
    if not isinstance(committee, dict) or not committee:
        raise ValueError('Backup committee metadata is invalid.')
    if not (directory / 'metadata/django-secret').read_text().strip():
        raise ValueError('Backup Django key is missing.')
    with tarfile.open(directory / 'media.tar.gz') as archive:
        for item in archive.getmembers():
            path = Path(item.name)
            if not item.isfile() or path.is_absolute() or '..' in path.parts or '\\' in item.name:
                raise ValueError('Backup media contains an unsafe path or entry.')
    return settings


def space_required(state, db_bytes=0):
    # Dumps and gzip need temporary storage; retain a conservative free-space reserve.
    media_bytes = sum(p.stat().st_size for p in (state / 'media').rglob('*') if p.is_file())
    required = RESERVE + 2 * (db_bytes + media_bytes)
    if shutil.disk_usage(state).free < required:
        raise RuntimeError('Not enough free disk space for a safe backup. Free space or copy old backups to protected storage; data was preserved.')


def prune(destination):
    # Only verified snapshots made by this adapter may be removed. Always keep seven.
    complete = []
    for folder in destination.glob('wash-*'):
        if folder.is_dir() and not folder.is_symlink():
            try:
                validate(folder)
            except (ValueError, OSError, KeyError, json.JSONDecodeError, tarfile.TarError):
                continue
            complete.append(folder)
    for folder in sorted(complete, key=lambda p: p.name, reverse=True)[KEEP:]:
        shutil.rmtree(folder)


def backup(destination):
    import django
    django.setup()
    from django.conf import settings
    from portable_windows.locking import application_lock, file_lock
    import psycopg
    state = Path(settings.WASH_COMMITTEE_STATE).resolve()
    destination = Path(destination).resolve()
    if destination != state / 'backups':
        raise ValueError('Portable backups must remain inside the protected data/backups folder. Copy a finished snapshot to protected external storage separately.')
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = settings.DATABASES['default']
    env = os.environ.copy()
    env.update(PGHOST=str(database['HOST']), PGPORT=str(database['PORT']), PGUSER=database['USER'],
               PGPASSWORD=database['PASSWORD'], PGDATABASE=database['NAME'], PGSSLMODE='disable')
    with file_lock(state / 'backup-operation.lock', exclusive=True, timeout=60), application_lock(exclusive=True, timeout=60):
        with psycopg.connect(host=database['HOST'], port=database['PORT'], user=database['USER'],
             password=database['PASSWORD'], dbname=database['NAME'], sslmode='disable') as connection:
            size = connection.execute('SELECT pg_database_size(current_database())').fetchone()[0]
        space_required(state, size)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        with tempfile.TemporaryDirectory(prefix='.wash-backup-', dir=destination) as temporary:
            working = Path(temporary)
            working.chmod(0o700)
            subprocess.run([shutil.which('pg_dump'), '--format=custom', '--no-owner', '--no-acl',
                            '--file', str(working / 'database.dump')], env=env, check=True, stdout=subprocess.DEVNULL)
            with tarfile.open(working / 'media.tar.gz', 'w:gz') as archive:
                for path in sorted((state / 'media').rglob('*')):
                    if path.is_symlink():
                        raise ValueError('Media symlinks cannot enter a backup.')
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(state / 'media').as_posix(), recursive=False)
            metadata = working / 'metadata'
            metadata.mkdir(mode=0o700)
            for name in METADATA:
                source = state / name
                if source.is_symlink():
                    raise ValueError('Metadata symlinks cannot enter a backup.')
                shutil.copyfile(source, metadata / name)
            names = ('database.dump', 'media.tar.gz', *('metadata/' + name for name in METADATA))
            checksums = {name: digest(working / name) for name in names}
            manifest = {'created_utc': stamp, 'format': 'PostgreSQL custom dump + media tar.gz',
                        'portable_format': 1, 'portable_files': checksums,
                        'consistency': 'exclusive application lock; single-host requests paused',
                        'database_sha256': checksums['database.dump'], 'media_sha256': checksums['media.tar.gz']}
            (working / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            for path in working.rglob('*'):
                if path.is_file():
                    path.chmod(0o600)
            validate(working)
            final = destination / ('wash-' + stamp)
            os.replace(working, final)
        prune(destination)
    print('Complete private backup created in data/backups:', final.name)
    return final


def restore(directory, launcher):
    directory = Path(directory).expanduser().resolve()
    data = validate(directory)  # Validate every file before creating private state.
    state = launcher.STATE
    allowed = {'launcher.lock'}
    if any(path.name not in allowed for path in state.iterdir()):
        raise RuntimeError('Restore requires a freshly extracted copy with no data. Existing files were not replaced or deleted.')
    launcher.verify_package()
    with tarfile.open(directory / 'media.tar.gz') as archive:
        media_size = sum(item.size for item in archive.getmembers())
    required = RESERVE + 4 * (directory / 'database.dump').stat().st_size + 2 * media_size
    if shutil.disk_usage(state).free < required:
        raise RuntimeError('Not enough disk space to restore safely. Existing data and backup were preserved.')
    # Never change the HTTP origin: doing so disconnects browser offline drafts.
    for key in ('http_port', 'db_port'):
        with socket.socket() as probe:
            if os.name == 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', data[key]))
    launcher.write_json(state / 'restore-in-progress.json', {'source': directory.name, 'started': time.time()})
    for name in METADATA:
        shutil.copyfile(directory / 'metadata' / name, state / name)
        (state / name).chmod(0o600)
    try:
        launcher.start_database(data)
        environment = launcher.environment(data)
        environment.update(PGHOST='127.0.0.1', PGPORT=str(data['db_port']), PGUSER='wash',
                           PGPASSWORD=data['password'], PGDATABASE=launcher.DBNAME)
        launcher.call([launcher.pg_binary('pg_restore'), '--no-owner', '--no-acl', '--exit-on-error',
                       '--dbname', launcher.DBNAME, directory / 'database.dump'], env=environment, quiet=True)
        media = state / 'media'
        media.mkdir(mode=0o700)
        with tarfile.open(directory / 'media.tar.gz') as archive:
            archive.extractall(media, filter='data')
        with launcher.connect(data) as connection:
            if not connection.execute('SELECT COUNT(*) FROM django_migrations').fetchone()[0]:
                raise ValueError('Restored database has no migration history.')
        (state / 'restore-in-progress.json').unlink()
    finally:
        launcher.stop_database(data)
    print('Restored into this clean copy. Run Start.cmd. The original data and backup were not changed.')
