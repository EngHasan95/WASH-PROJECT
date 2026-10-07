"""Export the verified Linux images and an allowlisted, secret-free source bundle."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / '.local/committee-package/WASH-Committee'
OUTPUT = ROOT / 'test-results/wash-committee-demo.zip'
TAGS = ['wash-committee:2026.10', 'wash-committee-postgres:17']
BUNDLE_FILES = ('Run.ps1', 'Start.cmd', 'Stop.cmd', 'Backup.cmd', 'run.sh', 'compose.yaml', 'README.html',
                'institution-logo.png', 'images.tar', 'images.sha256', 'images.ids', 'manifest.json')
environment = os.environ.copy()
for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
    environment.pop(name, None)
docker = ['docker', '--host=unix:///var/run/docker.sock']

def run(*args):
    return subprocess.check_output([*docker, *args], env=environment, text=True).strip()

def main():
    STAGE.mkdir(parents=True, exist_ok=True)
    for name in ('Run.ps1', 'Start.cmd', 'Stop.cmd', 'Backup.cmd', 'run.sh', 'compose.yaml', 'README.html'):
        shutil.copyfile(ROOT / 'committee' / name, STAGE / name)
    shutil.copyfile(ROOT / 'static/brand/institution-logo.png', STAGE / 'institution-logo.png')
    ids = {tag: run('image', 'inspect', '--format', '{{.Id}}', tag) for tag in TAGS}
    archive = STAGE / 'images.tar'
    subprocess.run([*docker, 'image', 'save', '--output', str(archive), *TAGS], env=environment, check=True)
    with archive.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    (STAGE / 'images.sha256').write_text(digest + '  images.tar\n')
    (STAGE / 'images.ids').write_text(''.join(f'{tag} {identity}\n' for tag, identity in ids.items()))
    (STAGE / 'manifest.json').write_text(json.dumps({'version': '2026.10', 'architecture': 'linux/amd64',
        'images_sha256': digest, 'images': ids, 'data': 'fictional fixtures provisioned on first local start; no live database or credentials included'}, indent=2))
    source = STAGE / 'source'
    if source.exists(): shutil.rmtree(source)
    source.mkdir()
    for folder in ('config', 'portal', 'static', 'templates', 'scripts', 'committee', 'deploy', 'docs'):
        shutil.copytree(ROOT / folder, source / folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('manage.py', 'requirements.txt', 'README.md', 'AGENTS.md', '.gitignore', '.dockerignore'):
        shutil.copyfile(ROOT / name, source / name)
    reference = source / '.local/committee-reference'
    reference.mkdir(parents=True)
    shutil.copyfile(ROOT / '.local/committee-reference/violation-fees-source.jpg', reference / 'violation-fees-source.jpg')
    OUTPUT.parent.mkdir(exist_ok=True)
    temporary = OUTPUT.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        files = [STAGE / name for name in BUNDLE_FILES] + [file for file in source.rglob('*') if file.is_file()]
        for file in sorted(files): package.write(file, file.relative_to(STAGE.parent))
    with zipfile.ZipFile(temporary) as package:
        assert package.testzip() is None
        assert not any(Path(name).name in ('.committee.env', 'django-secret', 'database.dump') for name in package.namelist())
    os.replace(temporary, OUTPUT)
    with OUTPUT.open('rb') as stream: checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    OUTPUT.with_suffix('.sha256').write_text(checksum + '  ' + OUTPUT.name + '\n')
    print(json.dumps({'package': str(OUTPUT), 'bytes': OUTPUT.stat().st_size, 'sha256': checksum, 'images': ids}))

if __name__ == '__main__': main()
