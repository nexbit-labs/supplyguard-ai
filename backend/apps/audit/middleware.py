"""Correlation id middleware (docs/CONTRACTS.md s1): one id per request, echoed on responses."""
import uuid

from .context import reset_correlation_id, set_correlation_id

HEADER = "X-Correlation-ID"
META_KEY = "HTTP_X_CORRELATION_ID"


def normalise_uuid(value) -> str | None:
    """Lowercase hyphenated form of any valid, non-nil UUID (any version), else None."""
    try:
        parsed = uuid.UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None
    return None if parsed.int == 0 else str(parsed)


class CorrelationIdMiddleware:
    """Must be FIRST in MIDDLEWARE so that errors raised by later middleware still get the id.

    A malformed (or nil) incoming value is never echoed: a fresh v4 is generated instead.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cid = normalise_uuid(request.META.get(META_KEY)) or str(uuid.uuid4())
        request.correlation_id = cid
        token = set_correlation_id(cid)
        try:
            response = self.get_response(request)
            response[HEADER] = cid
            return response
        finally:
            reset_correlation_id(token)
