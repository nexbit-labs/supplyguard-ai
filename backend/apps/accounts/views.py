from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenViewBase

from apps.audit import record
from apps.core.tenancy import TenantScopedMixin
from apps.tenants.models import Tenant
from apps.tenants.settings_schema import (
    drop_default_exposure,
    effective_settings,
    validate_tenant_settings,
)

from .permissions import RolePermission
from .serializers import (
    MeSerializer,
    TokenObtainSerializer,
    TokenRefreshSerializer,
    UserSerializer,
)
from .throttles import LoginUsernameThrottle


class _Public:
    permission_classes = [AllowAny]


@extend_schema(summary="Log in: returns access and refresh tokens", auth=[])
class TokenObtainView(_Public, TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle, LoginUsernameThrottle]
    throttle_scope = "auth"  # per client IP; LoginUsernameThrottle adds a per-username limit
    serializer_class = TokenObtainSerializer


@extend_schema(summary="Exchange a refresh token for a new access token", auth=[])
class TokenRefreshView(_Public, TokenViewBase):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_refresh"  # its own budget: refreshing must not consume logins
    serializer_class = TokenRefreshSerializer


@extend_schema(summary="The signed-in user and tenant")
class MeView(generics.RetrieveAPIView):
    serializer_class = MeSerializer
    permission_classes = [RolePermission]
    permission_key = "me.read"
    pagination_class = None

    def get_object(self):
        return self.request.user


@extend_schema_view(
    get=extend_schema(
        summary="The tenant's effective settings (exposure_full_scale shows 500000 when not set)",
        responses=OpenApiTypes.OBJECT,
    ),
    patch=extend_schema(
        summary="Partially update the tenant's settings; returns the effective settings",
        request=OpenApiTypes.OBJECT,
        responses=OpenApiTypes.OBJECT,
    ),
)
class SettingsView(APIView):
    """ADMIN only. Body and response are the tenant's `settings` JSON; the tenant is always the
    caller's own (`request.tenant`), so `tenant`/`tenant_id` in the body is an unknown key (400)
    and in the query string is ignored."""

    permission_classes = [RolePermission]
    permission_key = {"GET": "settings.read", "PATCH": "settings.write"}

    def get(self, request):
        return Response(effective_settings(request.tenant.settings))

    def patch(self, request):
        with transaction.atomic():
            # Row lock: two concurrent PATCHes apply one after the other, never lose an update.
            tenant = Tenant.objects.select_for_update().get(pk=request.tenant.pk)
            old = tenant.settings or {}
            new = drop_default_exposure(
                validate_tenant_settings(request.data, partial=True, current=old), old
            )
            changed = {}
            for key, value in new.items():
                if key not in old or old[key] != value:
                    changed[key] = {"old": old.get(key), "new": value}
            if changed:  # a no-op PATCH (e.g. GET body sent back) writes neither row nor audit
                tenant.settings = new
                tenant.save(update_fields=["settings"])
                record(request.user, "settings.updated", tenant, data={"changed": changed})
        return Response(effective_settings(new))


@extend_schema_view(
    get=extend_schema(summary="List the tenant's users"),
    post=extend_schema(summary="Create a user in the caller's tenant"),
)
class UserListCreateView(TenantScopedMixin, generics.ListCreateAPIView):
    """ADMIN only, own tenant only. No update or delete routes."""

    queryset = get_user_model().objects.all().order_by("id")
    serializer_class = UserSerializer
    permission_classes = [RolePermission]
    permission_key = {"GET": "users.read", "POST": "users.write"}

    def perform_create(self, serializer):
        tenant = self.get_request_tenant()
        try:
            with transaction.atomic():
                user = serializer.save(tenant=tenant)
                record(
                    self.request.user,
                    "user.created",
                    user,
                    data={"username": user.username, "role": user.role},
                )
        except IntegrityError:  # a concurrent create of the same username
            raise ValidationError({"username": ["This username is not available."]}) from None
