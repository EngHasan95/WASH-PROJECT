"""Shared/exclusive OS file locks for the portable runtime and its backups."""
import os
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path, *, exclusive=False, timeout=15):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    if os.name != 'nt':
        import fcntl
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            while True:
                try:
                    fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Application is busy; retry after backup completes')
                    time.sleep(.05)
            yield
        finally:
            os.close(descriptor)
        return

    import ctypes
    from ctypes import wintypes

    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    api.GetFileAttributesW.restype = wintypes.DWORD
    api.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    api.LockFileEx.restype = wintypes.BOOL
    api.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, ctypes.POINTER(Overlapped)]
    api.UnlockFileEx.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    # OPEN_REPARSE_POINT prevents following a substituted lock-file symlink.
    handle = api.CreateFileW(str(path), 0xC0000000, 7, None, 4, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    overlap = Overlapped()
    acquired = False
    try:
        if api.GetFileAttributesW(str(path)) & 0x400:
            raise OSError('A lock file must not be a reparse point')
        flags = 1 | (2 if exclusive else 0)  # FAIL_IMMEDIATELY, EXCLUSIVE_LOCK
        while not api.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlap)):
            code = ctypes.get_last_error()
            if code != 33:  # ERROR_LOCK_VIOLATION
                raise ctypes.WinError(code)
            if time.monotonic() >= deadline:
                raise TimeoutError('Application is busy; retry after backup completes')
            time.sleep(.05)
        acquired = True
        yield
    finally:
        if acquired:
            api.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
        api.CloseHandle(handle)


@contextmanager
def application_lock(*, exclusive=False, timeout=15):
    from django.conf import settings
    with file_lock(settings.WASH_WRITE_LOCK, exclusive=exclusive, timeout=timeout):
        yield


class SnapshotCoordinationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.http import JsonResponse
        try:
            with application_lock(timeout=settings.WASH_BACKUP_WAIT_SECONDS):
                return self.get_response(request)
        except TimeoutError:
            response = JsonResponse({'error': 'backup_in_progress',
                'message': 'نسخ احتياطي جارٍ؛ أعد المحاولة بعد قليل.'}, status=503)
            response['Retry-After'] = '30'
            response['Cache-Control'] = 'no-store, private'
            return response
