"""Trigger interfaces + stub implementations (WORKFLOW_DESIGN.md s4.4).

U-10 implements ExecutionTrigger (Boomi execution process); N-09 implements AnalysisTrigger.
Both are chosen by dotted path in settings (WORKFLOW_EXECUTION_TRIGGER /
WORKFLOW_ANALYSIS_TRIGGER) and resolved on every call, so tests can override the setting.
"""
import logging
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str  # also the ERP Idempotency-Key
    tenant_id: str
    risk_case_id: str
    approval_id: str
    recommendation_id: str
    option_type: str
    proposed_action: dict
    correlation_id: str
    attempt: int  # execution.retry_count + 1


class ExecutionTrigger(Protocol):
    def trigger(self, request: ExecutionRequest) -> None:
        """Hand the approved action to Boomi. Must return quickly; the result comes back via
        POST /internal/executions/{id}/result. Raise on dispatch failure."""


@dataclass(frozen=True)
class AnalysisRequest:
    tenant_id: str
    risk_case_id: str
    round: int
    reason: str
    feedback: str
    correlation_id: str


class AnalysisTrigger(Protocol):
    def request_analysis(self, request: AnalysisRequest) -> None: ...


def get_execution_trigger() -> ExecutionTrigger:
    return import_string(settings.WORKFLOW_EXECUTION_TRIGGER)()


def get_analysis_trigger() -> AnalysisTrigger:
    return import_string(settings.WORKFLOW_ANALYSIS_TRIGGER)()


def _audit(action, entity_type, entity_id, tenant_id, correlation_id, data):
    from apps.audit import record
    from apps.tenants.models import Tenant

    record(
        "SYSTEM",
        action,
        tenant=Tenant.objects.get(pk=tenant_id),
        entity_type=entity_type,
        entity_id=entity_id,
        correlation_id=correlation_id,
        data=data,
    )


class StubExecutionTrigger:
    """Logs and audits; does NOT fake a result (the case stays EXECUTING until one is posted)."""

    def trigger(self, request: ExecutionRequest) -> None:
        logger.info(
            "execution dispatch stubbed: %s attempt %s", request.execution_id, request.attempt
        )
        _audit(
            "execution.dispatch_stubbed",
            "Execution",
            request.execution_id,
            request.tenant_id,
            request.correlation_id,
            {
                "execution_id": request.execution_id,
                "risk_case_id": request.risk_case_id,
                "approval_id": request.approval_id,
                "attempt": request.attempt,
            },
        )


class StubAnalysisTrigger:
    def request_analysis(self, request: AnalysisRequest) -> None:
        logger.info("analysis request stubbed: %s round %s", request.risk_case_id, request.round)
        _audit(
            "analysis.request_stubbed",
            "RiskCase",
            request.risk_case_id,
            request.tenant_id,
            request.correlation_id,
            {"risk_case_id": request.risk_case_id, "round": request.round},
        )
