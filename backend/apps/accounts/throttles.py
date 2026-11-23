from rest_framework.throttling import SimpleRateThrottle

from . import failguard


def username_ident(raw):
    """Normalised throttle key of a submitted username: str/int/float as text, lowercased.
    None (and objects/arrays, which are not usernames) -> None: nothing to key on."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return None
    ident = str(raw).strip().lower()[:150]
    return ident or None


class LoginUsernameThrottle(SimpleRateThrottle):
    """Per-username limit on FAILED logins (`AUTH_USERNAME_THROTTLE_RATE`, per minute).

    The login serializer calls `record_failure()` on every failed attempt; this class only
    checks the counter before authenticating, so successful logins never use it up. While a
    username is over the limit every login for it is refused with 429, even with the correct
    password. Trade-off: anyone who knows a username can lock that account out of logging in
    for the rest of the minute by sending bad attempts."""

    scope = "auth_username"

    def allow_request(self, request, view):
        data = request.data
        ident = username_ident(data.get("username") if hasattr(data, "get") else None)
        if ident is None:
            return True
        return failguard.username_failures(ident) < self.num_requests

    def wait(self):
        return self.duration

    @classmethod
    def record_failure(cls, raw_username) -> None:
        ident = username_ident(raw_username)
        if ident is not None:
            failguard.record_username_failure(ident, cls().duration)
