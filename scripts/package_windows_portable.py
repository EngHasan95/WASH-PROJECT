"""Build a secret-free, native Windows x64 ZIP from verified vendor archives."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from portable_windows.build_support import copy_application, manifest, runtime_hashes, application_hashes

CACHE = ROOT / '.local/windows-portable-downloads'
WHEELS = ROOT / '.local/windows-portable-wheels'
STAGE = ROOT / '.local/windows-portable-package/WASH-Windows-Portable'
OUTPUT = ROOT / 'test-results/wash-windows-portable.zip'
ARTIFACTS = [
    {'name': 'python-3.13.16-embed-amd64.zip', 'url': 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip',
     'sha256': '97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297',
     'verification': 'Official Python release SPDX SHA256, fetched over verified HTTPS'},
    {'name': 'postgresql-17.11-1-windows-x64-binaries.zip',
     'url': 'https://get.enterprisedb.com/postgresql/postgresql-17.11-1-windows-x64-binaries.zip',
     'sha256': '6eabdf00d2893713b75db4336a23c3fdf505f056e217ec6e2e95d901750cfea3',
     'verification': 'Pinned digest of archive received directly from EDB over verified HTTPS'},
    {'name': 'Microsoft.VCLibs.x64.14.00.Desktop.appx',
     'url': 'https://download.microsoft.com/download/4/7/c/47c6134b-d61f-4024-83bd-b9c9ea951c25/Microsoft.VCLibs.x64.14.00.Desktop.appx',
     'sha256': 'b56a9101f706f9d95f815f5b7fa6efbac972e86573d378b96a07cff5540c5961',
     'verification': 'Pinned Microsoft HTTPS artifact and per-file AppxBlockMap SHA256; no AppX installation'},
]


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def fetch(item):
    path = CACHE / item['name']
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.download')
        with urllib.request.urlopen(item['url'], timeout=60) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target)
        os.replace(temporary, path)
    if digest(path) != item['sha256']:
        raise ValueError('Vendor archive checksum mismatch: ' + item['name'])
    return path


def extract_file(archive, name, target):
    if archive.getinfo(name).is_dir():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(name) as source, target.open('wb') as output:
        shutil.copyfileobj(source, output)


def verified_wheels():
    from packaging.utils import parse_wheel_filename
    from packaging.requirements import Requirement
    desired = {}
    for line in (ROOT / 'portable_windows/requirements.txt').read_text().splitlines():
        if line.strip() and not line.startswith('#'):
            requirement = Requirement(line)
            desired[requirement.name.lower().replace('_', '-')] = str(next(iter(requirement.specifier)).version)
    provenance = []
    wheels = list(WHEELS.glob('*.whl'))
    seen = set()
    for wheel in wheels:
        name, version, _, tags = parse_wheel_filename(wheel.name)
        name = str(name).replace('_', '-')
        if desired.get(name) != str(version):
            raise ValueError('Unexpected package or version: ' + wheel.name)
        if not all(tag.platform in ('any', 'win_amd64') for tag in tags):
            raise ValueError('Non-Windows wheel: ' + wheel.name)
        if name in seen:
            raise ValueError('Duplicate dependency: ' + name)
        seen.add(name)
        with urllib.request.urlopen('https://pypi.org/pypi/' + name + '/' + str(version) + '/json', timeout=30) as response:
            metadata = json.load(response)
        entry = next(item for item in metadata['urls'] if item['filename'] == wheel.name)
        if digest(wheel) != entry['digests']['sha256']:
            raise ValueError('PyPI checksum mismatch: ' + wheel.name)
        provenance.append({'name': wheel.name, 'url': entry['url'], 'sha256': entry['digests']['sha256'],
                           'verification': 'PyPI release metadata SHA256 over verified HTTPS'})
    if seen != set(desired):
        raise ValueError('Download all Windows wheels first using the pinned portable requirements.')
    return wheels, provenance


def verify_appx(archive):
    namespace = {'a': 'http://schemas.microsoft.com/appx/2010/blockmap'}
    blocks = ET.fromstring(archive.read('AppxBlockMap.xml'))
    for file in blocks.findall('a:File', namespace):
        data = archive.read(file.attrib['Name'].replace('\\', '/'))
        if len(data) != int(file.attrib['Size']):
            raise ValueError('Microsoft AppX file size mismatch')
        entries = file.findall('a:Block', namespace)
        if len(entries) != (len(data) + 65535) // 65536:
            raise ValueError('Microsoft AppX block count mismatch')
        for index, block in enumerate(entries):
            expected = base64.b64decode(block.attrib['Hash'], validate=True)
            if hashlib.sha256(data[index * 65536:(index + 1) * 65536]).digest() != expected:
                raise ValueError('Microsoft AppX block checksum mismatch')


def main():
    artifacts = [fetch(item) for item in ARTIFACTS]
    wheels, wheel_sources = verified_wheels()
    if STAGE.exists():
        if (STAGE / 'data').exists():
            raise RuntimeError('Do not rebuild over a started portable copy. Retain its data and choose a clean staging folder.')
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    copy_application(ROOT, STAGE)
    python_root = STAGE / 'runtime/python'
    with zipfile.ZipFile(artifacts[0]) as archive:
        for name in archive.namelist():
            if '/' in name or '\\' in name or name in ('.', '..'):
                raise ValueError('Unexpected Python archive layout')
            extract_file(archive, name, python_root / name)
    (python_root / 'python313._pth').write_text(
        'python313.zip\n.\n../site-packages\n../..\n../../app\n', encoding='ascii')
    postgres = STAGE / 'runtime/postgres'
    executables = {'postgres.exe', 'initdb.exe', 'pg_ctl.exe', 'pg_dump.exe', 'pg_restore.exe', 'psql.exe'}
    with zipfile.ZipFile(artifacts[1]) as archive:
        for name in archive.namelist():
            path = PurePosixPath(name)
            if '..' in path.parts or not path.parts or path.parts[0] != 'pgsql':
                raise ValueError('Unexpected PostgreSQL archive path')
            relative = PurePosixPath(*path.parts[1:])
            if not relative.parts:
                continue
            selected = relative.parts[0] in ('share', 'lib')
            selected |= relative.parts[0] == 'bin' and (relative.suffix.lower() == '.dll' or relative.name in executables)
            selected |= relative.name in ('server_license.txt', 'commandlinetools_3rd_party_licenses.txt')
            # Django uses PL/pgSQL, not the optional Perl/Python/Tcl server languages.
            # Do not ship optional language DLLs that require separate interpreters.
            if relative.parts[0] == 'lib' and any(language in relative.name.lower()
                for language in ('plperl', 'plpython', 'pltcl')):
                selected = False
            if relative.parts[:2] == ('share', 'extension') and any(language in relative.name.lower()
                for language in ('plperl', 'plpython', 'pltcl')):
                selected = False
            if selected and relative.suffix.lower() not in ('.lib', '.a', '.pdb'):
                extract_file(archive, name, postgres / str(relative))
    licenses = STAGE / 'licenses'
    licenses.mkdir()
    with zipfile.ZipFile(artifacts[2]) as archive:
        verify_appx(archive)
        for name in archive.namelist():
            if name.lower().startswith(('msvcp140', 'vcruntime140', 'concrt140')) and name.endswith('.dll'):
                extract_file(archive, name, postgres / 'bin' / name)
        for name in ('AppxManifest.xml', 'AppxBlockMap.xml', 'AppxSignature.p7x'):
            extract_file(archive, name, licenses / ('Microsoft-' + name))
    # Python's newer app-local VC runtime also serves the PG subprocesses.
    for name in ('vcruntime140.dll', 'vcruntime140_1.dll'):
        shutil.copyfile(python_root / name, postgres / 'bin' / name)
    (licenses / 'Microsoft-runtime.txt').write_text(
        'Microsoft Visual C++ runtime DLLs, Copyright Microsoft Corporation.\n'
        'Source: ' + ARTIFACTS[2]['url'] + '\n'
        'Deployed as app-local dependencies; the AppX package is NOT installed.\n'
        'Official redistribution guidance: https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files\n', encoding='utf-8')
    documentation = CACHE / 'microsoft-redistribution.html'
    if documentation.exists():
        shutil.copyfile(documentation, licenses / 'Microsoft-redistribution.html')
    libraries = STAGE / 'runtime/site-packages'
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for name in archive.namelist():
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('Unsafe wheel path')
                # These pinned wheels use normal site-packages layouts.
                if any(part.endswith('.data') for part in path.parts):
                    if '.data/data/' in name:
                        continue  # Django manpage, not runtime code.
                    raise ValueError('A wheel requires an unsupported install scheme: ' + name)
                extract_file(archive, name, libraries / name)
    checks = ROOT / 'test-results/windows-portable-validation/results.json'
    if not checks.exists():
        raise RuntimeError('Run windows_portable_checks.py successfully before packaging.')
    report = json.loads(checks.read_text())
    if report.get('runtime_sha256') != runtime_hashes(ROOT):
        raise RuntimeError('Portable source changed after its checks. Run windows_portable_checks.py again.')
    if report.get('application_sha256') != application_hashes(ROOT):
        raise RuntimeError('Application or collected assets changed after validation; rerun windows_portable_checks.py.')
    (STAGE / 'VALIDATION.txt').write_text(
        'Windows 10/11 x64 portable evaluation package.\n'
        'Same application source and pinned dependencies; PostgreSQL retained.\n'
        'Verified on Linux: portable supervisor/Waitress and six browser groups,\n'
        'offline intake and sync, role permissions, original complaint/violation workflows,\n'
        'document/report/PDF output, automatic/manual backup, throwaway restore,\n'
        'snapshot exclusion, duplicate start, restart persistence, lost settings and tamper rejection.\n'
        'Complete protected metadata backup; clean-copy restore without replacing existing data.\n'
        'Failure recovery: interrupted configuration, corrupt schedule, orphan PG and full stop.\n'
        'Seven-snapshot retention, low-space refusal and damaged restore rejection.\n'
        'Native Windows binaries and Windows ACL/LockFileEx execution: NOT TESTED.\n'
        'No OS installer/service/registry/global PATH/firewall changes.\n'
        'See current app/docs review and repair reports for application checks and remaining limits.\n', encoding='utf-8')
    (STAGE / 'validation-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    versions = {'python': '3.13.16', 'postgresql': '17.11', 'waitress': '3.0.2', 'django': '5.2.17', 'tzdata': '2026.5'}
    manifest(STAGE, versions=versions, provenance=ARTIFACTS + wheel_sources)
    OUTPUT.parent.mkdir(exist_ok=True)
    temporary = OUTPUT.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for file in sorted(STAGE.rglob('*')):
            if file.is_file():
                if file.name in ('settings.json', 'django-secret', 'database.dump', '.committee.env'):
                    raise ValueError('Private runtime file in release')
                archive.write(file, file.relative_to(STAGE.parent))
    with zipfile.ZipFile(temporary) as archive:
        if archive.testzip() is not None:
            raise ValueError('Release ZIP integrity failure')
    os.replace(temporary, OUTPUT)
    checksum = digest(OUTPUT)
    OUTPUT.with_suffix('.sha256').write_text(checksum + '  ' + OUTPUT.name + '\n')
    print(json.dumps({'package': str(OUTPUT), 'bytes': OUTPUT.stat().st_size, 'sha256': checksum, 'versions': versions}))


if __name__ == '__main__':
    main()
