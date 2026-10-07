"""Cross-process coordination of web requests and DB/media backups on one host."""
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse


@contextmanager
def application_lock(*, exclusive=False, timeout=15):
    path = Path(settings.WASH_WRITE_LOCK)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        deadline = time.monotonic() + timeout
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        while True:
            try:
                fcntl.flock(descriptor, mode | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Application is busy; retry after backup completes")
                time.sleep(.05)
        yield
    finally:
        os.close(descriptor)


class SnapshotCoordinationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            with application_lock(timeout=settings.WASH_BACKUP_WAIT_SECONDS):
                return self.get_response(request)
        except TimeoutError:
            response = JsonResponse({"error": "backup_in_progress", "message": "نسخ احتياطي جارٍ؛ أعد المحاولة بعد قليل."}, status=503)
            response["Retry-After"] = "30"
            response["Cache-Control"] = "no-store, private"
            return response
