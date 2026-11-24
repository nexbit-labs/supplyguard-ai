"""DRF exception handler producing the error body from docs/CONTRACTS.md section 1:

    {"error": {"code": "...", "message": "...", "details": {}, "correlation_id": "uuid"}}

Every API error (validation, auth, permission, not found, conflict, ...) goes through here, so
the frontend and Boomi can rely on one shape. Unhandled exceptions (None from DRF) stay 500s.
"""
import uuid

from rest_framework import exceptions
from rest_framework.views import exception_handler

CODE_BY_STATUS = {
    400: "VALIDATION_ERROR",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "BUSINESS_RULE",
    429: "RATE_LIMITED",
    502: "DOWNSTREAM_ERROR",
    504: "DOWNSTREAM_TIMEOUT",
}


def _correlation_id(request) -> str:
    """Reuse the request's correlation id (U-05 middleware sets `request.correlation_id`),
    else a well-formed `X-Correlation-ID` header, else mint a new one."""
    candidate = getattr(request, "correlation_id", None)
    if not candidate:
        from apps.audit.context import get_correlation_id

        candidate = get_correlation_id()
    if not candidate and request is not None:
        candidate = request.META.get("HTTP_X_CORRELATION_ID")
    try:
        return str(uuid.UUID(str(candidate)))
    except (ValueError, TypeError):
        return str(uuid.uuid4())


def _code_for(status_code: int) -> str:
    if status_code in CODE_BY_STATUS:
        return CODE_BY_STATUS[status_code]
    # Other 4xx (405, 415, 429, ...) are caller errors; other 5xx are downstream failures.
    return "VALIDATION_ERROR" if status_code < 500 else "DOWNSTREAM_ERROR"


def contract_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None

    if isinstance(exc, exceptions.ValidationError):
        message = "Request validation failed."
        details = response.data if isinstance(response.data, dict) else {"errors": response.data}
    else:
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
        message = str(detail) if detail else str(exc)
        details = getattr(exc, "contract_details", None)  # workflow reason codes (N-10)
        details = dict(details) if isinstance(details, dict) else {}

    response.data = {
        "error": {
            "code": _code_for(response.status_code),
            "message": message,
            "details": details,
            "correlation_id": _correlation_id(context.get("request")),
        }
    }
    return response
