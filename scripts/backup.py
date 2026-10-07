"""Create a private PostgreSQL and media snapshot at a caller-chosen destination.

Example: .venv/bin/python scripts/backup.py /path/to/separate/backup-volume
The destination should be persistent and separate from the application host.
"""
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
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
from portal.maintenance import application_lock  # noqa: E402


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="نسخة خاصة من قاعدة بيانات المياه والصور")
    parser.add_argument("destination", type=Path, help="مجلد نسخ احتياطي دائم تحدده المؤسسة")
    args = parser.parse_args()
    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.postgresql":
        parser.error("PostgreSQL is required")
    binary = shutil.which("pg_dump") or str(ROOT / ".local/postgres/usr/lib/postgresql/17/bin/pg_dump")
    if not Path(binary).is_file():
        parser.error("pg_dump is unavailable")
    destination = args.destination.expanduser().resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with application_lock(exclusive=True, timeout=60), tempfile.TemporaryDirectory(prefix=".wash-backup-", dir=destination) as temporary:
        working = Path(temporary)
        working.chmod(0o700)
        dump = working / "database.dump"
        media_archive = working / "media.tar.gz"
        environment = os.environ.copy()
        environment.update({"PGHOST": str(database["HOST"]), "PGPORT": str(database["PORT"]),
                            "PGUSER": str(database["USER"]), "PGDATABASE": str(database["NAME"])})
        if database.get("PASSWORD"):
            environment["PGPASSWORD"] = str(database["PASSWORD"])
        if database.get("OPTIONS", {}).get("sslmode"):
            environment["PGSSLMODE"] = database["OPTIONS"]["sslmode"]
        subprocess.run([binary, "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)],
                       env=environment, check=True, stdout=subprocess.DEVNULL)
        dump.chmod(0o600)
        media_root = Path(settings.MEDIA_ROOT)
        with tarfile.open(media_archive, "w:gz") as archive:
            if media_root.exists():
                for file in sorted(media_root.rglob("*")):
                    if file.is_symlink():
                        raise RuntimeError("Media directory contains a symbolic link; inspect before backing up")
                    if file.is_file():
                        archive.add(file, arcname=str(file.relative_to(media_root)), recursive=False)
        media_archive.chmod(0o600)
        manifest = {"created_utc": stamp, "format": "PostgreSQL custom dump + media tar.gz",
                    "consistency": "exclusive application lock; single-host web requests paused",
                    "database_sha256": sha256(dump), "media_sha256": sha256(media_archive)}
        (working / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        (working / "manifest.json").chmod(0o600)
        final = destination / f"wash-{stamp}"
        if final.exists():
            raise FileExistsError("A backup already exists for this second; retry after a moment")
        os.replace(working, final)
        # TemporaryDirectory cleanup sees the original name removed after the atomic move.
    print(f"Backup created: {final}")


if __name__ == "__main__":
    main()
