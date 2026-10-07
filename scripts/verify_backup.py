"""Restore only into a new throwaway DB and temporary media directory, then remove them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
import psycopg
from psycopg import sql


def verify(directory, expected=None):
    manifest = json.loads((directory / 'manifest.json').read_text())
    for name, key in [('database.dump', 'database_sha256'), ('media.tar.gz', 'media_sha256')]:
        digest = hashlib.sha256()
        with (directory / name).open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != manifest[key]:
            raise ValueError('Backup checksum mismatch: ' + name)
    database = settings.DATABASES['default']
    options = dict(host=database['HOST'], port=database['PORT'], user=database['USER'],
        password=database.get('PASSWORD', ''), dbname=database['NAME'],
        sslmode=database.get('OPTIONS', {}).get('sslmode', 'prefer'))
    target = 'wash_restore_check_' + uuid.uuid4().hex
    binary = shutil.which('pg_restore') or str(ROOT / '.local/postgres/usr/lib/postgresql/17/bin/pg_restore')
    environment = os.environ.copy()
    environment.update(PGHOST=str(options['host']), PGPORT=str(options['port']), PGUSER=str(options['user']),
        PGPASSWORD=options['password'], PGDATABASE=target, PGSSLMODE=options['sslmode'])
    with psycopg.connect(**options, autocommit=True) as control:
        control.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(target)))
        try:
            subprocess.run([binary, '--no-owner', '--no-acl', '--exit-on-error', '--dbname', target,
                str(directory / 'database.dump')], env=environment, check=True, stdout=subprocess.DEVNULL)
            with psycopg.connect(**{**options, 'dbname': target}) as restored:
                migrations = restored.execute('SELECT COUNT(*) FROM django_migrations').fetchone()[0]
                documents = restored.execute('SELECT COUNT(*) FROM portal_violationdocument').fetchone()[0]
                if not migrations:
                    raise AssertionError('Restored DB has no migration history')
                if expected:
                    body = restored.execute('SELECT body FROM portal_violationdocument WHERE client_id=%s',
                        [expected['client_id']]).fetchone()
                    if not body or body[0] != expected['body']:
                        raise AssertionError('Document fixture was not restored intact')
                    if expected.get('template_version_client_id'):
                        template = restored.execute('SELECT spec, approved_at FROM portal_reporttemplateversion WHERE client_id=%s',
                            [expected['template_version_client_id']]).fetchone()
                        report = restored.execute('SELECT content FROM portal_savedreport WHERE client_id=%s',
                            [expected['report_client_id']]).fetchone()
                        if not template or template[0] != expected['template_spec'] or not template[1]:
                            raise AssertionError('Approved template was not restored intact')
                        if not report or report[0] != expected['report_content']:
                            raise AssertionError('Saved report snapshot was not restored intact')
            with tempfile.TemporaryDirectory(prefix='wash-restore-media-') as temporary:
                with tarfile.open(directory / 'media.tar.gz') as archive:
                    members = archive.getmembers()
                    if any(not member.isfile() for member in members):
                        raise ValueError('Media backup contains unsupported entries')
                    archive.extractall(temporary, filter='data')
                    for member in members:
                        restored_file = Path(temporary) / member.name
                        if restored_file.stat().st_size != member.size:
                            raise AssertionError('Restored media size mismatch')
                    if expected:
                        if (Path(temporary) / expected['media_path']).read_bytes() != expected['media_bytes']:
                            raise AssertionError('Media fixture was not restored intact')
            return {'restored': True, 'migration_count': migrations, 'document_count': documents,
                'template_and_report_verified': bool(expected and expected.get('template_version_client_id')),
                'media_files': len(members), 'throwaway_database_removed': True}
        finally:
            control.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(target)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Verify restoration without replacing a live database')
    parser.add_argument('backup', type=Path)
    print(json.dumps(verify(parser.parse_args().backup.resolve())))
