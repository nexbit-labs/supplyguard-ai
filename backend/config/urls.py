from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.core.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", health, name="health"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    # Versioned REST API (docs/CONTRACTS.md section 4). Each app adds its own urls module
    # and is included here once it has endpoints.
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.audit.urls")),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.integrations.urls")),

    path("api/v1/", include("apps.workflow.urls")),
    path("api/v1/", include("apps.intelligence.urls")),
]

# JSON error body (CONTRACTS s1) for /api/ paths; other paths keep Django's pages.
handler400 = "apps.core.handlers.bad_request"
handler403 = "apps.core.handlers.permission_denied"
handler404 ="apps.core.handlers.not_found"
handler500 = "apps.core.handlers.server_error"
