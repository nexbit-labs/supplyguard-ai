"""Tiny urlconf for middleware / handler tests (use with override_settings(ROOT_URLCONF=...))."""
import logging

from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.http import JsonResponse
from django.urls import path
from django.views.decorators.http import require_GET
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.audit import record

logger = logging.getLogger("tests.correlation")


def ok(request):
    return JsonResponse({"cid": request.correlation_id})


def recording(request):
    event = record("SYSTEM", "test.recorded", entity=("Thing", "T-1"))
    return JsonResponse({"audit_id": event.pk})


def logging_view(request):
    logger.warning("hello from view")
    return JsonResponse({})


def forbidden(request):
    raise PermissionDenied("secret-reason")


def bad(request):
    raise SuspiciousOperation("secret-reason")


def boom(request):
    raise RuntimeError("secret-exception-text")


@require_GET
def get_only(request):
    return JsonResponse({})


class Protected(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return JsonResponse({})


urlpatterns = [
    path("api/ok", ok),
    path("api/record", recording),
    path("api/log", logging_view),
    path("api/boom", boom),
    path("api/get-only", get_only),
    path("api/protected", Protected.as_view()),
    path("boom-html", boom),
    path("api/forbidden", forbidden),
    path("api/bad", bad),
    path("forbidden-html", forbidden),
    path("bad-html", bad),
]

handler400 = "apps.core.handlers.bad_request"
handler403 = "apps.core.handlers.permission_denied"
handler404 ="apps.core.handlers.not_found"
handler500 = "apps.core.handlers.server_error"
