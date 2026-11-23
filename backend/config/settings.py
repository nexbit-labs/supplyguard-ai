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
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    # docs/CONTRACTS.md section 1: one error body shape for every API error.
    "EXCEPTION_HANDLER": "apps.core.exceptions.contract_exception_handler",
}

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
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

# --- Execution retries (docs/CONTRACTS.md section 10, Execution.retryable) -------------------
EXECUTION_MAX_RETRIES = env.int("EXECUTION_MAX_RETRIES", default=3)

# --- Seed data (manage.py load_seed, docs/CONTRACTS.md section 10) ---------------------------
# Folder with tenants.json, suppliers.json, ...; default is <repo>/data/seed.
SEED_DATA_DIR = Path(env("SEED_DATA_DIR", default=str(BASE_DIR.parent / "data" / "seed")))
