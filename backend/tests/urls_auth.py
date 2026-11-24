"""Test-only urlconf: the real routes plus an endpoint that echoes the authenticated caller."""
from django.urls import path
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.urls import urlpatterns as real_urlpatterns


class WhoAmI(APIView):
    permission_classes = [IsAuthenticated]  # the project default is RolePermission

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        return Response(
            {
                "tenant": tenant.id if tenant else None,
                "role": request.user.role,
                "user": str(request.user),
                "auth_is_api_key": type(request.auth).__name__ == "ApiKey",
            }
        )


urlpatterns = [path("test/whoami", WhoAmI.as_view()), *real_urlpatterns]
