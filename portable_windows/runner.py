"""Run the original backup/restore code with the Windows file-lock adapter."""
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'app'
sys.path[:0] = [str(ROOT), str(APP)]
os.environ['DJANGO_SETTINGS_MODULE'] = 'portable_windows.settings'
from portable_windows import locking
sys.modules['portal.maintenance'] = locking

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('backup', 'verify_backup'):
        raise SystemExit('Use runner.py backup DESTINATION or verify_backup BACKUP')
    operation = sys.argv.pop(1)
    if operation == 'backup':
        from portable_windows.snapshots import backup
        backup(Path(sys.argv[1]))
    else:
        runpy.run_path(str(APP / 'scripts' / (operation + '.py')), run_name='__main__')
