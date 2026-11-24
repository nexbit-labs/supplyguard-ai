"""N-11 what-if endpoints (docs/CONTRACTS.md s4). Read-only apart from one audit row per call."""
import logging
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response

from apps.accounts.permissions import RolePermission
from apps.audit import record
from apps.core.models import RiskCase, Supplier
from apps.core.tenancy import CROSS_TENANT_ACTION, TenantScopedMixin

from . import impact, services, whatif
from .exceptions import AnalysisInvalid, PayloadTooLarge
from .parsers import StrictJSONParser
from .serializers import (
    AnalysisRequestSerializer,
    AnalysisResponseSerializer,
    CaseWhatIfRequestSerializer,
    WhatIfRequestSerializer,
)

logger = logging.getLogger(__name__)

_AUDITED_FAILURES = frozenset({400, 403, 404, 409, 413, 422})


class DownstreamError(APIException):
    status_code = 502
    default_detail = "The ERP could not be read."


class DownstreamTimeout(APIException):
    status_code = 504
    default_detail = "The ERP did not answer in time."


def _client():
    return whatif.make_http_client(
        settings.ERP_BASE_URL, settings.ERP_API_KEY, settings.ERP_TIMEOUT_SECONDS
    )


def _now():
    """The clock for a what-if without `start`; tests patch this (never the real date)."""
    return timezone.now()


def _deny_foreign_supplier(request, tenant, supplier_id):
    """404, and record TC-10 `security.cross_tenant_denied` when the id is another tenant's."""
    try:
        foreign = (
            Supplier.objects.filter(erp_id=supplier_id).exclude(tenant=tenant).exists()
            and not Supplier.objects.filter(erp_id=supplier_id, tenant=tenant).exists()
        )
    except Exception:  # a lookup problem must not change the 404 answer
        logger.exception("foreign supplier lookup failed")
        foreign = False
    if foreign:
        record(
            request.user,
            CROSS_TENANT_ACTION,
            ("Supplier", str(supplier_id)[:100]),
            tenant=tenant,
            data={"path": request.path, "method": request.method},
        )
    raise NotFound()


def _simulate(request, tenant, *, supplier_id, event_type, hours, severity, start, extra, notes):
    cid = getattr(request, "correlation_id", None)
    event = whatif.build_event(
        tenant_id=tenant.id,
        supplier_id=supplier_id,
        event_type=event_type,
        duration_hours=hours,
        severity=severity,
        start=start,
    )
    client = _client()
    try:
        snapshot = whatif.fetch_snapshot(
            client,
            supplier_id,
            tenant.id,
            cid,
            per_call_timeout=settings.ERP_TIMEOUT_SECONDS,
            total_timeout=settings.ERP_TOTAL_TIMEOUT_SECONDS,
        )
    except whatif.ErpNotFound:
        _deny_foreign_supplier(request, tenant, supplier_id)
    except whatif.ErpTimeout:
        raise DownstreamTimeout() from None
    except whatif.ErpUnavailable:
        raise DownstreamError() from None
    finally:
        close = getattr(client, "close", None)
        if close:
            close()
    try:
        whatif.check_snapshot_tenant(snapshot, tenant.id)
        return whatif.run_what_if(
            tenant={
                "tenant_id": tenant.id,
                "currency": tenant.currency,
                "settings": tenant.settings,
            },
            event=event,
            snapshot=snapshot,
            scenario_extra=extra,
            assumptions_extra=notes,
        )
    except whatif.ForeignSupplier:
        _deny_foreign_supplier(request, tenant, supplier_id)
    except (impact.SnapshotError, KeyError, TypeError, ValueError, ArithmeticError):
        logger.exception("what-if: inconsistent ERP data")
        raise DownstreamError("The ERP returned inconsistent data.") from None


def _audit(request, tenant, entity, body):
    record(
        request.user,
        "what_if.run",
        entity,
        tenant=tenant,
        data={
            "supplier_id": body["scenario"]["supplier_id"],
            "event_type": body["scenario"]["event_type"],
            "duration_days": body["scenario"]["duration_days"],
            "risk_case_id": body["scenario"].get("risk_case_id"),
            "risk_score": body["risk"]["score"],
            "risk_level": body["risk"]["level"],
            "affected_po_count": len({p["po_id"] for p in body["affected_pos"]}),
            "exposure": body["exposure"],
        },
    )


@extend_schema(
    request=WhatIfRequestSerializer, responses={200: dict}, summary="What-if supplier outage"
)
class WhatIfView(generics.GenericAPIView):
    permission_classes = [RolePermission]
    permission_key = "what-if"
    serializer_class = WhatIfRequestSerializer
    pagination_class = None

    def post(self, request):
        tenant = request.tenant  # JWT / API key; never the body
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        body = _simulate(
            request,
            tenant,
            supplier_id=v["supplier_id"],
            event_type=v["event_type"],
            hours=Decimal(v["duration_days"]) * 24,
            severity=None,
            start=v.get("start") or _now(),
            extra={"risk_case_id": None, "source": "REQUEST"},
            notes=[],
        )
        _audit(request, tenant, ("Supplier", v["supplier_id"]), body)
        return Response(body)


@extend_schema(
    request=CaseWhatIfRequestSerializer,
    responses={200: dict},
    summary="What-if on an existing risk case",
)
class CaseWhatIfView(TenantScopedMixin, generics.GenericAPIView):
    permission_classes = [RolePermission]
    permission_key = "what-if"
    serializer_class = CaseWhatIfRequestSerializer
    pagination_class = None
    queryset = RiskCase.objects.select_related("event", "supplier")
    lookup_field = "id"
    lookup_url_kwarg = "case_id"

    def post(self, request, case_id):
        case = self.get_object()  # tenant-scoped: foreign or missing -> 404
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        override = data.validated_data.get("duration_days")
        event = case.event
        notes = [f"Seeded from risk case {case.id}: supplier, event type and severity copied."]
        if override is not None:
            hours = Decimal(override) * 24
            notes.append(f"Duration overridden to {override} days.")
        elif event.expected_duration_hours:
            hours = Decimal(event.expected_duration_hours)
            notes.append("Duration taken from the case's event.")
        else:
            raise ValidationError(
                {"duration_days": ["The case has no expected duration; supply duration_days."]}
            )
        body = _simulate(
            request,
            request.tenant,
            supplier_id=event.supplier_erp_id,
            event_type=event.event_type,
            hours=hours,
            severity=event.severity,
            start=data.validated_data.get("start") or event.event_time,
            extra={"risk_case_id": case.id, "source": "RISK_CASE"},
            notes=notes,
        )
        _audit(request, request.tenant, case, body)
        return Response(body)


@extend_schema(
    request=AnalysisRequestSerializer,
    responses={201: AnalysisResponseSerializer},
    summary="Store and validate one agent output (SYSTEM only)",
)
class AnalysisView(TenantScopedMixin, generics.GenericAPIView):
    """POST /internal/risk-cases/{id}/analysis (N-07). SYSTEM API key only; tenant from the key.

    Every outcome of an authenticated call leaves one audit row: `analysis.recorded` (201),
    `analysis.invalid` (422, the AgentRun is stored) or `analysis.rejected` (400/403/404/409/413/
    422-not-applied).
    """

    permission_classes = [RolePermission]
    permission_key = "internal.analysis"
    serializer_class = AnalysisRequestSerializer
    pagination_class = None
    queryset = RiskCase.objects.select_related("event")
    lookup_field = "id"
    lookup_url_kwarg = "case_id"
    max_body_bytes = 1024 * 1024
    max_errors_echoed = 50
    parser_classes = [StrictJSONParser]
    _audited = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)  # authentication + role check first
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > self.max_body_bytes:
            raise PayloadTooLarge()

    def _audit_failure(self, exc, response):
        if self._audited or response.status_code not in _AUDITED_FAILURES:
            return
        tenant = getattr(self.request, "tenant", None)
        user = self.request.user
        if tenant is None or not getattr(user, "is_authenticated", False):
            return
        data = {"status_code": response.status_code, "method": self.request.method}
        details = getattr(exc, "details", None)
        if isinstance(details, dict) and "reason" in details:
            data["reason"] = details["reason"]
        try:
            record(
                user,
                "analysis.rejected",
                ("RiskCase", str(self.kwargs.get("case_id", ""))[:100]),
                tenant=tenant,
                data=data,
            )
            self._audited = True
        except Exception:  # an audit problem must not change the answer
            logger.exception("analysis.rejected audit failed")

    def handle_exception(self, exc):
        response = super().handle_exception(exc)
        self._audit_failure(exc, response)
        return response

    def post(self, request, case_id):
        case = self.get_object()  # tenant-scoped: foreign or missing -> 404
        data = self.get_serializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        result = services.record_analysis(
            tenant=request.tenant,
            case=case,
            kind=v["kind"],
            payload=v["payload"],
            actor=request.user,
            correlation_id=getattr(request, "correlation_id", None),
            round=v["round"],
        )
        record(
            request.user,
            "analysis.recorded" if result.valid else "analysis.invalid",
            case,
            tenant=request.tenant,
            data={
                "kind": v["kind"],
                "round": v["round"],
                "agent_run_id": result.agent_run.pk,
                "valid": result.valid,
                "actionable_count": result.actionable_count,
                "case_status": result.status,
                "error_count": len(result.validation_errors),
            },
        )
        self._audited = True
        if not result.valid:
            raise AnalysisInvalid(result, limit=self.max_errors_echoed)
        body = {
            "agent_run_id": result.agent_run.pk,
            "valid": True,
            "actionable_count": result.actionable_count,
            "risk_case_id": case.pk,
            "case_status": result.status,
            "analysis_round": case.analysis_round,
            "recommendation_ids": [r.pk for r in result.recommendations],
        }
        return Response(AnalysisResponseSerializer(body).data, status=201)
