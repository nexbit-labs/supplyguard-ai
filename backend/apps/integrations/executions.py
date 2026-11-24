"""Execution results from Boomi (POST /internal/executions/<id>/result; tasks/UDAY.md U-06).

Rules: tenant-scoped lookup (foreign = missing = same 404, foreign also audited);
response_payload and error are redacted and size-capped before they are stored; the update and
the case move are done by apps.workflow.services.record_execution_result (N-10): stale attempt
and not-running execution are 409, the identical result again is a no-op 200.
"""
import json
import logging
import re

from rest_framework.exceptions import NotFound

from apps.audit import record
from apps.audit.service import redact
from apps.core.models import Execution

from .risk_cases import CROSS_TENANT_ACTION, CaseResult

logger = logging.getLogger(__name__)

PAYLOAD_MAX_BYTES = 20 * 1024
PREVIEW_CHARS = 16 * 1024
ERROR_MAX_CHARS = 2000
UNVERIFIED_ERROR = "ERP change not verified"
_SECRET_IN_TEXT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)\b(\s*[=:]\s*)\S+"
)


def sanitize_error(error):
    if error is None:
        return None
    text = redact(str(error))
    text = _SECRET_IN_TEXT.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    return text[:ERROR_MAX_CHARS]


def sanitize_payload(payload):
    """Redact sensitive keys, then cap the stored size (truncate with a marker, never fail)."""
    if payload is None:
        return None
    clean = redact(payload)
    try:
        text = json.dumps(clean, default=str)
    except (TypeError, ValueError):
        return {"_truncated": True, "reason": "payload could not be serialised"}
    if len(text.encode("utf-8")) <= PAYLOAD_MAX_BYTES:
        return clean
    return {
        "_truncated": True,
        "original_size_bytes": len(text.encode("utf-8")),
        "preview": text[:PREVIEW_CHARS],
    }


def _find_execution(tenant, execution_id) -> Execution:
    execution = Execution.objects.filter(tenant=tenant, id=execution_id).first()
    if execution is not None:
        return execution
    if Execution.objects.filter(id=execution_id).exclude(tenant=tenant).exists():
        record(
            "SYSTEM",
            CROSS_TENANT_ACTION,
            ("Execution", str(execution_id)[:100]),
            tenant=tenant,
            data={"endpoint": "internal.execution-result"},
        )
    raise NotFound()


def _body(execution) -> dict:
    execution = Execution.objects.select_related("approval__risk_case").get(pk=execution.pk)
    return {
        "execution_id": execution.id,
        "status": execution.status,
        "error_class": execution.error_class,
        "finished_at": execution.finished_at,
        "case_status": execution.approval.risk_case.status,
        "retryable": execution.retryable,
    }


def apply_execution_result(tenant, execution_id, data, actor="SYSTEM") -> CaseResult:
    """Validate + sanitise, then hand the result to the workflow service, which owns the
    Execution update and the case move (RECOVERED / EXECUTION_FAILED) in one transaction."""
    from apps.workflow.services import record_execution_result

    outcome, error_class, error = data["status"], data.get("error_class"), data.get("error")
    if outcome == "SUCCEEDED" and data.get("verified") is not True:
        # The ERP re-read did not confirm the change: N-10 treats an unverified success as
        # FAILED / BUSINESS (not retryable). A 400 here would leave the execution RUNNING.
        outcome, error_class, error = "FAILED", "BUSINESS", UNVERIFIED_ERROR
    failed = outcome == "FAILED"
    # outside any transaction: the cross-tenant audit row must survive the 404
    execution = _find_execution(tenant, execution_id)
    record_execution_result(
        execution,
        outcome=outcome,
        attempt=data["attempt"],
        error_class=error_class if failed else None,
        error=sanitize_error(error) if failed else None,
        response_status=data.get("response_status"),
        response_payload=sanitize_payload(data.get("response_payload")),
        actor=actor,
        correlation_id=execution.approval.risk_case.correlation_id,
    )
    return CaseResult(200, _body(execution))
