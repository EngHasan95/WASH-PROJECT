"""Run periodic local snapshots; production scheduling uses the supplied systemd timer."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--interval-hours', type=float, default=24)
    parser.add_argument('--once', action='store_true', help='Check due time and exit after one check')
    args = parser.parse_args()
    if args.interval_hours < 1:
        parser.error('Interval must be at least one hour')
    destination = args.destination.resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = os.open(destination / '.scheduler.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Backup scheduler already running at this destination')
        os.close(lock)
        return
    marker = destination / '.last-success.json'
    try:
        while True:
            last = json.loads(marker.read_text()).get('timestamp', 0) if marker.exists() else 0
            if time.time() - last >= args.interval_hours * 3600:
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/backup.py'), str(destination)], cwd=ROOT)
                if result.returncode == 0:
                    temporary = destination / '.last-success.tmp'
                    temporary.write_text(json.dumps({'timestamp': time.time()}))
                    temporary.chmod(0o600)
                    os.replace(temporary, marker)
                elif args.once:
                    raise SystemExit(result.returncode)
            if args.once:
                return
            time.sleep(30)
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
