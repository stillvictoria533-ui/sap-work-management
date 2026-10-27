import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR.parent / '.env')
DEBUG = os.getenv('DJANGO_DEBUG', 'false').lower() == 'true'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if len(SECRET_KEY) < 40:
    raise ImproperlyConfigured('请设置至少 40 字符的 DJANGO_SECRET_KEY。')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
CSRF_TRUSTED_ORIGINS = [s for s in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if s]
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.staticfiles', 'rest_framework', 'desk', 'assistant']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware', 'desk.middleware.HeadersMiddleware']
X_FRAME_OPTIONS = 'DENY'
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
AUTH_USER_MODEL = 'desk.User'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_TZ = True
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.getenv('SQLITE_PATH', str(BASE_DIR / 'dev.sqlite3')), 'OPTIONS': {'timeout': 20}}}
if os.getenv('POSTGRES_HOST'):
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'HOST': os.environ['POSTGRES_HOST'], 'PORT': os.getenv('POSTGRES_PORT', '5432'), 'NAME': os.getenv('POSTGRES_DB', 'sapdesk'), 'USER': os.getenv('POSTGRES_USER', 'sapdesk'), 'PASSWORD': os.environ['POSTGRES_PASSWORD'], 'CONN_MAX_AGE': 60}}
elif not DEBUG and os.getenv('ALLOW_SQLITE', 'false') != 'true':
    raise ImproperlyConfigured('生产环境必须配置 PostgreSQL。仅本地验证可设置 ALLOW_SQLITE=true。')
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
PASSWORD_HASHERS = ['django.contrib.auth.hashers.Argon2PasswordHasher', 'django.contrib.auth.hashers.PBKDF2PasswordHasher']
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'], 'DEFAULT_PERMISSION_CLASSES': ['desk.permissions.ActiveAccount'], 'DEFAULT_PAGINATION_CLASS': 'desk.pagination.Pagination', 'PAGE_SIZE': 20, 'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'], 'EXCEPTION_HANDLER': 'desk.errors.exception_handler'}
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
TRUST_PROXY = os.getenv('TRUST_PROXY', 'false') == 'true'
if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = Path(os.getenv('MEDIA_ROOT', str(BASE_DIR / 'private-media')))
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 5
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def nonnegative_integer(name, default, minimum=0):
    raw = os.getenv(name, str(default))
    if not raw.isascii() or not raw.isdigit() or len(raw) > 19:
        raise ImproperlyConfigured(f'{name} 必须是非负整数。')
    value = int(raw)
    if value < minimum or value > 9223372036854775807:
        raise ImproperlyConfigured(f'{name} 超出支持范围。')
    return value


ATTACHMENT_TICKET_LIMIT_BYTES = nonnegative_integer('ATTACHMENT_TICKET_LIMIT_BYTES', 256 * 1024**2)
ATTACHMENT_OWNER_LIMIT_BYTES = nonnegative_integer('ATTACHMENT_OWNER_LIMIT_BYTES', 1024**3)
ATTACHMENT_GLOBAL_LIMIT_BYTES = nonnegative_integer('ATTACHMENT_GLOBAL_LIMIT_BYTES', 2 * 1024**3)
ATTACHMENT_MIN_FREE_BYTES = nonnegative_integer('ATTACHMENT_MIN_FREE_BYTES', 3 * 1024**3)
AI_HEARTBEAT_STALE_SECONDS = nonnegative_integer('AI_HEARTBEAT_STALE_SECONDS', 300, minimum=1)
AI_BACKLOG_ALERT_SECONDS = nonnegative_integer('AI_BACKLOG_ALERT_SECONDS', 600, minimum=1)
AI_FAILURE_ALERT_COUNT = nonnegative_integer('AI_FAILURE_ALERT_COUNT', 3, minimum=1)
OPS_BACKUP_HEALTH_PATH = os.getenv('OPS_BACKUP_HEALTH_PATH', '/app/ops-health/backup.json')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
DINGTALK_ENABLED = os.getenv('DINGTALK_ENABLED', 'false').lower() == 'true'
DINGTALK_GROUP_TOKEN = os.getenv('DINGTALK_GROUP_TOKEN', '')
DINGTALK_GROUP_SECRET = os.getenv('DINGTALK_GROUP_SECRET', '')
# Model credentials are server-only. SAP tools are deliberately not registered.
AI_PROVIDER = os.getenv('AI_PROVIDER', 'disabled').strip()
AI_CODEX_BIN = os.getenv('AI_CODEX_BIN', 'codex').strip()
AI_CODEX_MODEL = os.getenv('AI_CODEX_MODEL', '').strip()
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.getenv('OPENAI_MODEL', '').strip()
AI_MAX_OUTPUT_TOKENS = 4000
AI_MAX_TURN_SECONDS = 150
AI_TURNS_PER_DAY = 100
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'handlers': {'console': {'class': 'logging.StreamHandler'}}, 'root': {'handlers': ['console'], 'level': 'INFO'}}

AI_TICKET_AUTOMATION_ENABLED = os.getenv('AI_TICKET_AUTOMATION_ENABLED', 'false').lower() == 'true'
