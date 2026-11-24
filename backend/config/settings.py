"""Django settings for SupplyGuard AI.

All environment-specific values come from environment variables (see ../.env.example),
read with django-environ. Nothing secret is hard-coded.
"""
from datetime import timedelta
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1", "[::1]"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173"]),
)
# Local runs outside docker: read the repo-root .env (docker compose injects env itself).
for _env_file in (BASE_DIR.parent / ".env", BASE_DIR / ".env"):
    if _env_file.exists():
        environ.Env.read_env(_env_file)

DEBUG = env("DEBUG")

# A real secret is mandatory unless DEBUG is on (local dev only). An empty value counts as
# unset, so `SECRET_KEY=` in compose/.env can never silently become a known key outside DEBUG.
SECRET_KEY = env("SECRET_KEY", default="") or ("dev-insecure-secret-key" if DEBUG else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG is off.")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# --- Applications -----------------------------------------------------------------------
# One shell per bounded area so teammates can add models/views without editing settings.
# Owners: tenants/accounts/core/audit/integrations = Uday, intelligence/workflow = Nikhil,
# portal = Roop.
LOCAL_APPS = [
    "apps.tenants",
    "apps.accounts",
    "apps.core",
    "apps.audit",
    "apps.integrations",
    "apps.intelligence",
    "apps.workflow",
    "apps.portal",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "corsheaders",
    *LOCAL_APPS,
]

MIDDLEWARE = [
    # First, so every response (including errors from later middleware) carries the id.
    "apps.audit.middleware.CorrelationIdMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Database (PostgreSQL 16 by default) ----------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://supplyguard:supplyguard@localhost:5432/supplyguard",
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom user from day one: swapping AUTH_USER_MODEL after the first migrate is painful.
# U-03 adds the tenant FK and role fields to this model.
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- CORS (React dev server) ----------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")
CORS_EXPOSE_HEADERS = ["X-Correlation-ID"]

# --- DRF / JWT / OpenAPI --------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.accounts.authentication.TenantJWTAuthentication",
        "apps.accounts.authentication.ApiKeyAuthentication",
    ],
    # Only the login and refresh views are throttled (scopes below). Each gunicorn worker counts
    # separately (LocMemCache); see backend/README.md.
    "DEFAULT_THROTTLE_RATES": {
        "auth": env("AUTH_THROTTLE_RATE", default="10/min"),  # /auth/token per client IP
        "auth_username": env("AUTH_USERNAME_THROTTLE_RATE", default="10/min"),  # per username
        "auth_refresh": env("AUTH_REFRESH_THROTTLE_RATE", default="60/min"),  # /auth/refresh
    },
    # Number of trusted reverse proxies in front of Django (0 = none, 1 = behind the U-12
    # proxy). The client IP for throttles and failed-credential counters is read from
    # X-Forwarded-For only up to this depth, so a spoofed header cannot mint new buckets.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=0),
    # Secure by default: a view without `permission_key` is refused (403). Views that are
    # meant to be public (login, health, schema) say so explicitly.
    "DEFAULT_PERMISSION_CLASSES": ["apps.accounts.permissions.RolePermission"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    # docs/CONTRACTS.md section 1: one error body shape for every API error.
    "EXCEPTION_HANDLER": "apps.core.exceptions.contract_exception_handler",
}

# Failed-credential guards (apps/accounts/failguard.py), per client IP per minute.
AUTH_FAIL_AUDIT_PER_MIN = env.int("AUTH_FAIL_AUDIT_PER_MIN", default=10)  # max audit rows
AUTH_FAIL_LIMIT_PER_MIN = env.int("AUTH_FAIL_LIMIT_PER_MIN", default=30)  # then 429

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=1)),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SupplyGuard AI API",
    "DESCRIPTION": "Supplier risk intelligence and recovery. Source of truth: docs/CONTRACTS.md.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --- Logging (never log secrets; see CLAUDE.md section 6) -------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"correlation_id": {"()": "apps.audit.logfilter.CorrelationIdFilter"}},
    "formatters": {
        "default": {
            "format": "%(asctime)s %(levelname)s [%(correlation_id)s] %(name)s: %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["correlation_id"],
            "formatter": "default",
        }
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

# --- Execution retries (docs/CONTRACTS.md section 10, Execution.retryable) -------------------
EXECUTION_MAX_RETRIES = env.int("EXECUTION_MAX_RETRIES", default=3)

# --- Workflow triggers (N-10): dotted paths; U-10 / N-09 replace the logging stubs ----------------
WORKFLOW_EXECUTION_TRIGGER = env(
    "WORKFLOW_EXECUTION_TRIGGER", default="apps.workflow.hooks.StubExecutionTrigger"
)
WORKFLOW_ANALYSIS_TRIGGER = env(
    "WORKFLOW_ANALYSIS_TRIGGER", default="apps.workflow.hooks.StubAnalysisTrigger"
)

for _name in ("WORKFLOW_EXECUTION_TRIGGER", "WORKFLOW_ANALYSIS_TRIGGER"):
    if not globals()[_name].startswith("apps."):
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured(f"{_name} must be a dotted path inside 'apps.'")

# --- Seed data (manage.py load_seed, docs/CONTRACTS.md section 10) ---------------------------
# Folder with tenants.json, suppliers.json, ...; default is <repo>/data/seed.
SEED_DATA_DIR = Path(env("SEED_DATA_DIR", default=str(BASE_DIR.parent / "data" / "seed")))

# --- ERP read access for the what-if simulator (N-11, apps/intelligence/whatif.py) ------------
# Base URL of the (mock) ERP; X-Api-Key is sent only when ERP_API_KEY is set. Timeout is per call.
ERP_BASE_URL = env("ERP_BASE_URL", default="http://localhost:8081")
ERP_API_KEY = env("ERP_API_KEY", default="")
ERP_TIMEOUT_SECONDS = env.float("ERP_TIMEOUT_SECONDS", default=2.0)
# Total budget for all (sequential) ERP calls of one what-if; when used up the answer is 504.
ERP_TOTAL_TIMEOUT_SECONDS = env.float("ERP_TOTAL_TIMEOUT_SECONDS", default=2.5)
