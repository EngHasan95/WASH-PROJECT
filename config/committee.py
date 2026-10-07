"""Computer-only committee demo. This configuration never enables public hosting."""
import os
from pathlib import Path
import secrets
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

if os.environ.get('WASH_COMMITTEE_DEMO') != '1':
    raise ImproperlyConfigured('Committee configuration requires an explicit isolated demo.')
database_name = unquote(urlparse(os.environ.get('DATABASE_URL', '')).path.lstrip('/'))
if not database_name.startswith('wash_committee'):
    raise ImproperlyConfigured('Committee demo must use a dedicated wash_committee database.')
state = Path(os.environ.get('WASH_COMMITTEE_STATE', str(Path(__file__).resolve().parent.parent / '.local/committee')))
state.mkdir(mode=0o700, parents=True, exist_ok=True)
keyfile = state / 'django-secret'
try:
    descriptor = os.open(keyfile, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        file.write(secrets.token_urlsafe(64))
except FileExistsError:
    pass
os.environ['DJANGO_DEBUG'] = '0'
os.environ['DJANGO_SECRET_KEY'] = keyfile.read_text().strip()
os.environ['DJANGO_ALLOWED_HOSTS'] = 'localhost,127.0.0.1,[::1]'
os.environ['WASH_MEDIA_ROOT'] = str(state / 'media')
os.environ['WASH_WRITE_LOCK'] = str(state / 'application-write.lock')
os.environ.pop('WASH_TRUST_PROXY', None)
from .settings import *  # noqa: F403,E402

WASH_COMMITTEE_DEMO = True
WASH_COMMITTEE_STATE = state
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
# Plain HTTP is confined to loopback. Modern browsers treat localhost as a
# secure context, allowing the same offline service worker without fake TLS.
