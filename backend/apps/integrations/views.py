import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import RequestDataTooBig
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.accounts.permissions import NO_TENANT_MESSAGE
from apps.core.models import DisruptionEvent
from apps.core.models.sequences import IdSequence, next_id

from . import boomi
from .callback_serializers import (
    ExecutionResultRequestSerializer,
    ExecutionResultResponseSerializer,
    RiskCaseCreatedSerializer,
    RiskCaseRequestSerializer,
)
from .executions import apply_execution_result
from .risk_cases import create_risk_case_from_snapshot
from .serializers import (
    EventAcceptedSerializer,
    IntegrationStatusSerializer,
    SimulateRequestSerializer,
    SimulateResponseSerializer,
)
from .services import intake_event
from .throttles import CredentialScopedThrottle

HEALTH_CACHE_SECONDS = 10


def _tenant(request):
    tenant = getattr(request, "tenant", None)  # set from the credential only
    if tenant is None:
        raise PermissionDenied(NO_TENANT_MESSAGE)
    return tenant


def _actor(request):
    return request.user if isinstance(request.user, User) else "SYSTEM"


def _request_correlation_id(request) -> str:
    cid = getattr(request, "correlation_id", None)
    return str(cid) if cid else str(uuid.uuid4())


class EventIntakeView(APIView):
    """Disruption events from Boomi (API key) or an ADMIN. The tenant is the credential's."""

    permission_key = "events.create"
    throttle_classes = [CredentialScopedThrottle]
    throttle_scope = "events"

    @extend_schema(
        summary="Receive a canonical disruption event (idempotent on event_id)",
        request=None,
        responses={200: EventAcceptedSerializer, 202: EventAcceptedSerializer},
    )
    def post(self, request):
        if not isinstance(request.data, dict):
            raise ValidationError({"non_field_errors": ["Expected a JSON object."]})
        result = intake_event(
            tenant=_tenant(request),
            actor=_actor(request),
            payload=request.data,
            request_correlation_id=_request_correlation_id(request),
        )
        return Response(result.body, status=result.http_status)


def fresh_simulated_event_id(tenant) -> str:
    """Next EVT-9xxxxx id (reserved for simulation) this tenant does not have yet. The sequence
    row is recreated at 900000 if it is missing."""
    IdSequence.objects.get_or_create(prefix="EVT", defaults={"last_value": 900000})
    for _ in range(1000):
        candidate = next_id("EVT", 6)
        if not DisruptionEvent.objects.filter(tenant=tenant, event_id=candidate).exists():
            return candidate
    raise RuntimeError("could not find a free simulation event id")


def load_scenario(name: str) -> dict:
    """Read `<SCENARIO_DIR>/<name>.json`; the name was validated against ^[a-z0-9_]+$ and the
    resolved path must still sit directly inside SCENARIO_DIR."""
    base = settings.SCENARIO_DIR.resolve()
    path = (base / f"{name}.json").resolve()
    if path.parent != base or not path.is_file():
        raise NotFound("Unknown scenario.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise NotFound("Unknown scenario.") from None
    if not isinstance(data, dict) or not isinstance(data.get("event"), dict):
        raise NotFound("Unknown scenario.")
    return data


class SimulateDisruptionView(APIView):
    """ADMIN demo trigger: replays a scenario file through the same intake as the endpoint."""

    permission_key = "demo.simulate"
    throttle_classes = [CredentialScopedThrottle]
    throttle_scope = "simulate"

    @extend_schema(
        summary="Replay a demo scenario as a disruption event for the caller's tenant",
        request=SimulateRequestSerializer,
        responses={200: SimulateResponseSerializer},
    )
    def post(self, request):
        tenant = _tenant(request)
        params = SimulateRequestSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        scenario = load_scenario(params.validated_data["scenario"])

        event = dict(scenario["event"])
        event["tenant_id"] = tenant.id  # always the caller's tenant
        override = params.validated_data.get("event_time")
        if override:
            event["event_time"] = override.isoformat()
        if params.validated_data.get("fresh_event_id", True):
            event["event_id"] = fresh_simulated_event_id(tenant)
            event.pop("correlation_id", None)  # every simulated run is its own flow

        try:
            send_times = max(1, min(int(scenario.get("send_times", 1)), 5))
        except (TypeError, ValueError):
            send_times = 1

        request_cid = _request_correlation_id(request)
        sent = []
        for _ in range(send_times):
            result = intake_event(
                tenant=tenant,
                actor=_actor(request),
                payload=dict(event),
                request_correlation_id=request_cid,
            )
            sent.append({"http_status": result.http_status, **result.body})
        return Response(
            {
                "scenario": params.validated_data["scenario"],
                "event_id": event.get("event_id"),
                "sent": sent,
            }
        )


class IntegrationStatusView(APIView):
    permission_key = "integrations.status"

    @extend_schema(
        summary="Boomi connection state and event counts of the last 24 hours (tenant-scoped)",
        responses={200: IntegrationStatusSerializer},
    )
    def get(self, request):
        tenant = _tenant(request)
        return Response({"boomi": self._boomi(), "events_24h": self._counts(tenant)})

    @staticmethod
    def _boomi():
        auth_mode = settings.BOOMI_AUTH_MODE
        out = {
            "status": "NOT_CONFIGURED",
            "configured": False,
            "auth_mode": auth_mode,
            "host": boomi.host_of(settings.BOOMI_BASE_URL),
            "reachable": None,
            "latency_ms": None,
            "checked_at": None,
        }
        try:
            client = boomi.get_boomi_client()
        except boomi.BoomiNotConfigured:
            return out
        out["configured"] = True
        cache_key = f"integrations:boomi_health:{client.host}"
        probe = cache.get(cache_key)
        if probe is None:
            probe = {**client.health(), "checked_at": timezone.now().isoformat()}
            cache.set(cache_key, probe, HEALTH_CACHE_SECONDS)
        out.update(
            reachable=probe["reachable"],
            latency_ms=probe["latency_ms"],
            checked_at=probe["checked_at"],
            status="REACHABLE" if probe["reachable"] else "UNREACHABLE",
        )
        return out

    @staticmethod
    def _counts(tenant):
        since = timezone.now() - timedelta(hours=24)
        events = DisruptionEvent.objects.filter(tenant=tenant, created_at__gte=since)
        by_status = DisruptionEvent.PublishStatus
        return {
            "RECEIVED": events.filter(publish_status=by_status.RECEIVED).count(),
            "PUBLISHED": events.filter(publish_status=by_status.PUBLISHED).count(),
            "PUBLISH_FAILED": events.filter(publish_status=by_status.PUBLISH_FAILED).count(),
            "needs_data_correction": events.filter(needs_data_correction=True).count(),
        }


def _json_object(request):
    """The JSON body as a dict. Django's request-size limit (DATA_UPLOAD_MAX_MEMORY_SIZE) is
    enforced here because DRF's JSON parser streams the body and would bypass it."""
    try:
        request._request.body  # noqa: B018 - reads and size-checks the body
    except RequestDataTooBig:
        raise ValidationError({"non_field_errors": ["Request body is too large."]}) from None
    if not isinstance(request.data, dict):
        raise ValidationError({"non_field_errors": ["Expected a JSON object."]})
    return request.data


class RiskCaseCallbackView(APIView):
    """Boomi sends the ERP snapshot for an event; Django computes impact and creates the case."""

    permission_key = "internal.risk-cases"

    @extend_schema(
        summary="Create the RiskCase for an event from a Boomi ERP snapshot (idempotent)",
        request=RiskCaseRequestSerializer,
        responses={200: RiskCaseCreatedSerializer, 201: RiskCaseCreatedSerializer},
    )
    def post(self, request):
        data = _json_object(request)
        serializer = RiskCaseRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        result = create_risk_case_from_snapshot(
            _tenant(request),
            serializer.validated_data["event_id"],
            data["snapshot"],  # as received (money strings unchanged)
            actor=request.user,
        )
        return Response(result.body, status=result.http_status)


class ExecutionResultView(APIView):
    """Boomi reports the outcome of an approved ERP change."""

    permission_key = "internal.execution-result"

    @extend_schema(
        summary="Store the ERP execution result sent by Boomi (idempotent, 409 on conflict)",
        request=ExecutionResultRequestSerializer,
        responses={200: ExecutionResultResponseSerializer},
    )
    def post(self, request, execution_id):
        data = _json_object(request)
        serializer = ExecutionResultRequestSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        result = apply_execution_result(
            _tenant(request), execution_id, serializer.validated_data, actor=request.user
        )
        return Response(result.body, status=result.http_status)
