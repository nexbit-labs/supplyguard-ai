"""Per-client-IP counters that keep failed-credential attempts from flooding the audit table
or being tried without limit. Counters live in the Django cache (per process, like the login
throttle: each gunicorn worker counts separately; use a shared cache such as Redis in
production). The client IP is DRF's `get_ident`, which honours `NUM_PROXIES`, so a spoofed
X-Forwarded-For does not mint a new bucket."""
import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

logger = logging.getLogger(__name__)
WINDOW = 60  # seconds


def client_ip(request) -> str:
    return BaseThrottle().get_ident(request)


def _bump(key: str, window: int = WINDOW) -> int:
    cache.add(key, 0, window)
    try:
        return cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, window)
        return 1


def should_audit_failure(request, what: str) -> bool:
    """True while this IP is under AUTH_FAIL_AUDIT_PER_MIN audit rows of kind `what` this
    minute (each kind has its own budget, so bad API keys cannot hide failed logins). The first
    skipped write logs ONE warning with the IP and the skip count so far (no secrets)."""
    limit = settings.AUTH_FAIL_AUDIT_PER_MIN
    ip = client_ip(request)
    count = _bump(f"failguard:audit:{what}:{ip}")
    if count <= limit:
        return True
    if count == limit + 1:
        logger.warning(
            "audit rows '%s' from %s are rate limited (%s per minute): skipped %s so far, "
            "further rows this minute are skipped silently",
            what,
            ip,
            limit,
            count - limit,
        )
    return False


def api_key_failures(request) -> int:
    return cache.get(f"failguard:apikey:{client_ip(request)}", 0)


def record_api_key_failure(request) -> int:
    return _bump(f"failguard:apikey:{client_ip(request)}")


def _login_key(ident: str) -> str:
    return "failguard:login:" + hashlib.sha256(ident.encode()).hexdigest()


def username_failures(ident: str) -> int:
    return cache.get(_login_key(ident), 0)


def record_username_failure(ident: str, window: int = WINDOW) -> int:
    return _bump(_login_key(ident), window)
