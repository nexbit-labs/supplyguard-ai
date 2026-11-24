"""Per-credential rate limit for the event intake and the simulate trigger.

Scopes `events` (EVENTS_THROTTLE_RATE, default 300/min) and `simulate` (SIMULATE_THROTTLE_RATE,
default 10/min). The bucket is the credential (API key id or user id), not the IP: Boomi callers
share egress IPs. A throttled request is a 429 and is not processed or audited, so the audit
rows written for duplicates (`event.duplicate`) are capped by this limit. Counters live in the
default cache, i.e. per worker with the local-memory cache (see README).
"""
from rest_framework.settings import api_settings
from rest_framework.throttling import ScopedRateThrottle

from apps.accounts.principals import SystemPrincipal


class CredentialScopedThrottle(ScopedRateThrottle):
    # Own copy (not the class-level dict of ScopedRateThrottle) so it always has the intake
    # scopes, whatever else patches the base class.
    THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES

    def get_cache_key(self, request, view):
        user = request.user
        if isinstance(user, SystemPrincipal):
            ident = f"key:{user.api_key.pk}"
        elif getattr(user, "pk", None) is not None:
            ident = f"user:{user.pk}"
        else:
            ident = self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
