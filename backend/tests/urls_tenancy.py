"""Test-only urlconf: the real routes plus two read endpoints built on TenantScopedViewSet
(the real RiskCase / Supplier endpoints belong to other tasks)."""
from django.urls import path
from rest_framework import mixins, serializers
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.principals import SystemPrincipal
from apps.core.models import RiskCase, Supplier
from apps.core.tenancy import TenantScopedViewSet
from config.urls import urlpatterns as real_urlpatterns


class IsRealUser(BasePermission):
    """Test-only: any authenticated non-SYSTEM user (the real endpoints use RolePermission)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and not isinstance(user, SystemPrincipal))


class _IdSerializer(serializers.Serializer):
    id = serializers.CharField()
    tenant_id = serializers.CharField()


class RiskCaseViewSet(
    TenantScopedViewSet, mixins.RetrieveModelMixin, mixins.ListModelMixin
):
    queryset = RiskCase.objects.all().order_by("id")
    serializer_class = _IdSerializer
    permission_classes = [IsRealUser]


class SupplierViewSet(
    TenantScopedViewSet, mixins.RetrieveModelMixin, mixins.ListModelMixin
):
    queryset = Supplier.objects.all().order_by("id")
    serializer_class = _IdSerializer
    permission_classes = [IsRealUser]


class AnyAuthRiskCaseViewSet(RiskCaseViewSet):
    """Accepts any authenticated caller incl. SYSTEM (to exercise the mixin with an API key)."""

    permission_classes = [IsAuthenticated]


class NarrowedRiskCaseViewSet(RiskCaseViewSet):
    """get_queryset() narrows further (as real views do with status filters)."""

    def get_queryset(self):
        return super().get_queryset().filter(risk_level="LOW")


class NarrowedSupplierByErpViewSet(SupplierViewSet):
    """ERP-keyed lookup (per-tenant key) behind a narrowing queryset."""

    lookup_field = "erp_id"
    lookup_url_kwarg = "pk"

    def get_queryset(self):
        return super().get_queryset().filter(name="never-matches")


class NoKeyView(APIView):
    """No permission_class and no permission_key: the project default must refuse it."""

    def get(self, request):
        return Response({"leaked": True})


urlpatterns = [
    path(
        "test/narrowed-suppliers/<str:pk>",
        NarrowedSupplierByErpViewSet.as_view({"get": "retrieve"}),
    ),
    path("test/narrowed/<str:pk>", NarrowedRiskCaseViewSet.as_view({"get": "retrieve"})),
    path("test/nokey", NoKeyView.as_view()),
    path("test/any/risk-cases/<str:pk>", AnyAuthRiskCaseViewSet.as_view({"get": "retrieve"})),
    path("test/risk-cases", RiskCaseViewSet.as_view({"get": "list"})),
    path("test/risk-cases/<str:pk>", RiskCaseViewSet.as_view({"get": "retrieve"})),
    path("test/suppliers", SupplierViewSet.as_view({"get": "list"})),
    path("test/suppliers/<str:pk>", SupplierViewSet.as_view({"get": "retrieve"})),
    *real_urlpatterns,
]
