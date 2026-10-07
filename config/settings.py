"""Local-first development; production configuration is supplied through the environment."""
import os
import secrets
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production.")
    keyfile = BASE_DIR / ".local" / "django-secret"
    keyfile.parent.mkdir(parents=True, exist_ok=True)
    try:
        with keyfile.open("x") as file:
            file.write(secrets.token_urlsafe(64))
        keyfile.chmod(0o600)
    except FileExistsError:
        pass
    SECRET_KEY = keyfile.read_text().strip()

ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
if not DEBUG and "DJANGO_ALLOWED_HOSTS" not in os.environ:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS is required in production.")
INSTALLED_APPS = [
    "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions",
    "django.contrib.messages", "django.contrib.staticfiles", "portal",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "portal.maintenance.SnapshotCoordinationMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "portal.middleware.ResponsePolicyMiddleware",
]
ROOT_URLCONF = "config.urls"
WASH_COMMITTEE_DEMO = False
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "portal.committee.context",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
if database_url := os.environ.get("DATABASE_URL"):
    parsed = urlparse(database_url)
    if parsed.scheme not in ("postgres", "postgresql"):
        raise ImproperlyConfigured("DATABASE_URL must be PostgreSQL.")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""), "PASSWORD": unquote(parsed.password or ""),
        "HOST": unquote(parsed.hostname or ""), "PORT": parsed.port or 5432,
        "OPTIONS": {"sslmode": os.environ.get("PGSSLMODE", "require")},
    }}
else:
    if not DEBUG:
        raise ImproperlyConfigured("DATABASE_URL is required in production.")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", "NAME": "wash_development",
        "USER": os.environ.get("PGUSER", os.environ.get("USER", "agent")),
        "HOST": str(BASE_DIR / ".local" / "pgsocket"), "PORT": "55432",
    }}
AUTH_USER_MODEL = "portal.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Aden"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = Path(os.environ.get("WASH_MEDIA_ROOT", str(BASE_DIR / "media")))
WASH_WRITE_LOCK = Path(os.environ.get("WASH_WRITE_LOCK", str(BASE_DIR / ".local/application-write.lock")))
WASH_BACKUP_WAIT_SECONDS = 15
# Never expose MEDIA_ROOT through a public /media/ route. Photos use owner-checked APIs.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 3
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/workspace/"
LOGOUT_REDIRECT_URL = "/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
if os.environ.get("WASH_TRUST_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False
# Preload requires the institution's explicit domain enrollment. Retain all other
# deployment checks; do not opt a future institutional hostname into preload here.
SILENCED_SYSTEM_CHECKS = ["security.W021"]
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
# No external mail, SMS, analytics, CDN, payment or identity service is required.
