"""Runtime-only overrides; preserve committee guards, models and workflows."""
from config.committee import *  # noqa: F403

MIDDLEWARE = [
    'portable_windows.locking.SnapshotCoordinationMiddleware'
    if name == 'portal.maintenance.SnapshotCoordinationMiddleware' else name
    for name in MIDDLEWARE  # noqa: F405
]
