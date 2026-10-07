"""Check the final ZIP, PE dependency closure and unchanged application content."""
import hashlib
import json
from pathlib import Path
import sys
import zipfile

import pefile

ROOT = Path(__file__).resolve().parent.parent
STAGE = ROOT / '.local/windows-portable-package/WASH-Windows-Portable'
SYSTEM = set('advapi32 bcrypt comctl32 comdlg32 crypt32 gdi32 iphlpapi kernel32 msimg32 msvcrt '
             'ole32 oleacc oleaut32 pdh propsys psapi rpcrt4 secur32 shell32 shlwapi user32 '
             'uxtheme version winmm wldap32 ws2_32'.split())
SYSTEM = {name + '.dll' for name in SYSTEM} | {'winspool.drv'}


def main():
    package = ROOT / 'test-results/wash-windows-portable.zip'
    checksum = package.with_suffix('.sha256').read_text().split()[0]
    with package.open('rb') as source:
        assert hashlib.file_digest(source, 'sha256').hexdigest() == checksum
    with zipfile.ZipFile(package) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert all(not name.startswith('WASH-Windows-Portable/data/') for name in names)
        assert not any(Path(name).name in ('django-secret', '.committee.env', 'database.dump') for name in names)
        manifest = json.loads(archive.read('WASH-Windows-Portable/package-manifest.json'))
        assert manifest['versions']['tzdata'] == '2026.5'
        for name, expected in manifest['files'].items():
            assert hashlib.sha256(archive.read('WASH-Windows-Portable/' + name)).hexdigest() == expected, name
    for folder in ('config', 'portal', 'static', 'templates'):
        for file in (ROOT / folder).rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts and file.suffix != '.pyc':
                assert file.read_bytes() == (STAGE / 'app' / file.relative_to(ROOT)).read_bytes(), file
    native = [file for file in (STAGE / 'runtime').rglob('*')
              if file.suffix.lower() in ('.dll', '.pyd', '.exe')]
    provided = {file.name.lower() for file in native}
    missing = set()
    for file in native:
        pe = pefile.PE(str(file), fast_load=True)
        assert pe.FILE_HEADER.Machine == 0x8664, file
        pe.parse_data_directories(directories=[1, 13])
        for group in ('DIRECTORY_ENTRY_IMPORT', 'DIRECTORY_ENTRY_DELAY_IMPORT'):
            for imported in getattr(pe, group, []):
                name = imported.dll.decode().lower()
                if name not in provided and name not in SYSTEM and not name.startswith('api-ms-win-'):
                    missing.add(name)
    assert not missing, sorted(missing)
    current = json.loads((ROOT / 'test-results/windows-portable-validation/results.json').read_text())
    assert current == json.loads((STAGE / 'validation-results.json').read_text())
    # CMD invokes only the included interpreter, without policy overrides/installers.
    for command in ('Start.cmd', 'Stop.cmd', 'Backup.cmd', 'Restore.cmd'):
        text = (STAGE / command).read_text().lower()
        assert 'runtime\\python\\python.exe' in text
        assert not any(word in text for word in ('powershell', 'docker', 'wsl', 'msiexec', 'reg add'))
    result = {'zip_sha256': checksum, 'files': len(manifest['files']), 'native_x64_files': len(native),
              'unbundled_non_system_imports': sorted(missing), 'application_source_unchanged': True,
              'private_runtime_data_included': False, 'native_windows_execution': 'not performed'}
    (ROOT / 'test-results/windows-portable-validation/artifacts.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
