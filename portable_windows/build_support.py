"""Explicit source allowlist and checksums shared by packaging and validation."""
import hashlib
import json
from pathlib import Path
import shutil


def runtime_hashes(repository):
    files = ('launcher.py', 'locking.py', 'runner.py', 'settings.py', 'requirements.txt',
             'Start.cmd', 'Stop.cmd', 'Backup.cmd', 'Restore.cmd', 'snapshots.py')
    return {name: hashlib.sha256((repository / 'portable_windows' / name).read_bytes()).hexdigest()
            for name in files}


def application_hashes(repository):
    """Bind validation to the application and collected assets actually tested."""
    files = {}
    for folder in ('config', 'portal', 'static', 'templates', 'staticfiles'):
        for path in sorted((repository / folder).rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            with path.open('rb') as source:
                files[path.relative_to(repository).as_posix()] = hashlib.file_digest(source, 'sha256').hexdigest()
    for name in ('manage.py', 'requirements.txt'):
        files[name] = hashlib.sha256((repository / name).read_bytes()).hexdigest()
    return files


def copy_application(repository, stage):
    app = stage / 'app'
    app.mkdir(parents=True)
    for folder in ('config', 'portal', 'static', 'templates', 'docs'):
        shutil.copytree(repository / folder, app / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(repository / 'staticfiles', app / 'staticfiles')
    scripts = app / 'scripts'
    scripts.mkdir()
    for name in ('backup.py', 'verify_backup.py'):
        shutil.copyfile(repository / 'scripts' / name, scripts / name)
    for name in ('manage.py', 'requirements.txt', 'README.md', 'AGENTS.md'):
        shutil.copyfile(repository / name, app / name)
    shutil.copytree(repository / 'portable_windows', stage / 'portable_windows',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('Start.cmd', 'Stop.cmd', 'Backup.cmd', 'Restore.cmd', 'README.html'):
        shutil.copyfile(repository / 'portable_windows' / name, stage / name)
    shutil.copyfile(repository / 'static/brand/institution-logo.png', stage / 'institution-logo.png')
    reference = stage / 'reference'
    reference.mkdir()
    shutil.copyfile(repository / '.local/committee-reference/violation-fees-source.jpg',
                    reference / 'violation-fees-source.jpg')


def manifest(stage, *, versions, provenance):
    files = {}
    for path in sorted(stage.rglob('*')):
        if path.is_symlink():
            raise ValueError('Package contains a symlink')
        if path.is_file() and path.name != 'package-manifest.json':
            if path.relative_to(stage).parts[0] == 'data':
                raise ValueError('Runtime data must never enter a release')
            with path.open('rb') as source:
                files[path.relative_to(stage).as_posix()] = hashlib.file_digest(source, 'sha256').hexdigest()
    data = {'format': 1, 'platform': 'windows-amd64', 'versions': versions,
            'provenance': provenance, 'files': files,
            'data': 'No database, user settings or keys included. Fictional data generated on first start.'}
    (stage / 'package-manifest.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data
