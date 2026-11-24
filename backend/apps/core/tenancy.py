"""Tenant scoping for views (docs/CONTRACTS.md s1: the tenant never comes from body or query).

`TenantScopedMixin` makes a DRF view tenant-safe:
- `get_queryset()` is always filtered by `request.tenant` (set from the JWT user or the API key
  by the authentication classes). `tenant`/`tenant_id` in the body or the query string are
  never read. Without a tenant the view fails closed with a 403 (never an unfiltered queryset).
- `get_object()` looks only inside the scoped queryset. Another tenant's row answers with the
  same 404 as a missing id (no existence leak), and the attempt is audited as
  `security.cross_tenant_denied` under the REQUESTER's tenant (TC-10), so it is visible in
  their own `GET /audit` and never in the other tenant's. The audit data holds only the request
  path and method, never anything about the foreign row.
- `perform_create` stores the row in the requester's tenant.
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import viewsets
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.accounts.principals import SystemPrincipal
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)

NO_TENANT_MESSAGE = "No active tenant for this credential."
CROSS_TENANT_ACTION = "security.cross_tenant_denied"
_LOOKUP_ERRORS = (ValueError, TypeError, DjangoValidationError)


class TenantScopedMixin:
    tenant_field = "tenant"

    def get_request_tenant(self):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None or tenant.status != Tenant.Status.ACTIVE:
            raise PermissionDenied(NO_TENANT_MESSAGE)
        return tenant

    def get_queryset(self):
        tenant = self.get_request_tenant()  # fail closed before any query is built
        return super().get_queryset().filter(**{self.tenant_field: tenant})

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        value = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        try:
            obj = queryset.filter(**{self.lookup_field: value}).first()
        except _LOOKUP_ERRORS:
            obj = None
        if obj is None:
            self._audit_if_foreign(queryset.model, value)
            raise NotFound()  # same exception and body for "missing" and "someone else's"
        self.check_object_permissions(self.request, obj)
        return obj

    def _audit_if_foreign(self, model, value):
        tenant = self.get_request_tenant()
        try:
            if model._base_manager.filter(
                **{self.tenant_field: tenant, self.lookup_field: value}
            ).exists():
                return  # the requester's OWN row (a per-tenant key such as erp_id): not foreign
            # Only rows of OTHER tenants count: an own row hidden by a narrowing get_queryset()
            # (status filter etc.) is an ordinary 404, not a cross-tenant attempt.
            elsewhere = (
                model._base_manager.filter(**{self.lookup_field: value})
                .exclude(**{self.tenant_field: tenant})
                .exists()
            )
        except _LOOKUP_ERRORS:
            return
        if not elsewhere:
            return
        try:
            from apps.audit import record

            user = self.request.user
            record(
                "SYSTEM" if isinstance(user, SystemPrincipal) else user,
                CROSS_TENANT_ACTION,
                (model.__name__, str(value)[:100]),
                tenant=tenant,
                data={"path": self.request.path, "method": self.request.method},
            )
        except Exception:  # an audit failure must never change the 404 answer
            logger.exception("audit write failed for %s", CROSS_TENANT_ACTION)

    def perform_create(self, serializer):
        serializer.save(**{self.tenant_field: self.get_request_tenant()})


class TenantScopedViewSet(TenantScopedMixin, viewsets.GenericViewSet):
    """Ready-made base: add the DRF mixins you need (ListModelMixin, ...)."""
