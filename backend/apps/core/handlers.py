"""JSON 400/403/404/500 for API paths (docs/CONTRACTS.md s1); other paths keep Django's defaults."""
import uuid

from django.http import JsonResponse
from django.views import defaults

from apps.audit.context import get_correlation_id


def _is_api(request) -> bool:
    return request.path.startswith("/api/")


def _cid(request) -> str:
    return getattr(request, "correlation_id", None) or get_correlation_id() or str(uuid.uuid4())


def _body(request, code, message, status):
    cid = _cid(request)
    response = JsonResponse(
        {"error": {"code": code, "message": message, "details": {}, "correlation_id": cid}},
        status=status,
    )
    response["X-Correlation-ID"] = cid
    return response


def bad_request(request, exception=None):
    if _is_api(request):
        return _body(request, "VALIDATION_ERROR", "Bad request.", 400)
    return defaults.bad_request(request, exception)


def permission_denied(request, exception=None):
    if _is_api(request):
        return _body(request, "FORBIDDEN", "Permission denied.", 403)
    return defaults.permission_denied(request, exception)


def not_found(request, exception=None):
    if _is_api(request):
        return _body(request, "NOT_FOUND", "Not found.", 404)
    return defaults.page_not_found(request, exception)


def server_error(request):
    if _is_api(request):
        # Generic message only: no exception text or traceback reaches the caller. The
        # traceback is already logged once by django.request (with the correlation id).
        return _body(request, "INTERNAL_ERROR", "Internal server error.", 500)
    return defaults.server_error(request)
