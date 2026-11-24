"""RiskCase state machine and decision service (WORKFLOW_DESIGN.md).

`transition()` is the ONLY code that may change `RiskCase.status` (CLAUDE.md s6). Everything else
here (decisions, execution start / retry / result, close, re-analyse) calls it.

In data-pipeline terms: a case is a job with a status column; `transition()` is the single job
runner that locks the row, checks the move against the whitelist in `states.TRANSITIONS`, writes
the new status and its audit row in ONE transaction, or changes nothing.
"""
import logging
from dataclasses import dataclass
from typing import Literal

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.principals import SystemPrincipal
from apps.audit import record
from apps.audit.context import get_correlation_id
from apps.audit.models import AuditEvent
from apps.core.models import Approval, Execution, Recommendation, RiskCase
from apps.core.models.cases import status_change_allowed

from . import hooks
from .exceptions import (
    CaseNotFound,
    DecisionConflict,
    DecisionNotPermitted,
    DecisionRejected,
    InvalidTransition,
    PreconditionFailed,
    TransitionNotPermitted,
)
from .states import TRANSITIONS, S, actionable_recommendations

logger = logging.getLogger(__name__)

SYSTEM = "SYSTEM"
REASON_MAX = 500
_CASE_FIELDS = ["status", "analysis_round", "recovered_at", "updated_at"]


@dataclass(frozen=True)
class TransitionResult:
    case: RiskCase  # the locked, updated instance
    from_state: str
    to_state: str
    changed: bool  # False only for the idempotent repeat
    audit_event: AuditEvent | None


# ----------------------------------------------------------------------------- transition()
def _actor_info(actor):
    """-> (kind, role, tenant_id)."""
    if isinstance(actor, User):
        if actor.pk is None:
            raise TypeError("actor User must be saved")
        return "USER", actor.role, actor.tenant_id
    if isinstance(actor, SystemPrincipal):
        return "SYSTEM", SYSTEM, actor.tenant_id
    if actor == SYSTEM and isinstance(actor, str):
        return "SYSTEM", SYSTEM, None
    raise TypeError(f"actor must be a User, SystemPrincipal or 'SYSTEM', not {actor!r}")


def _ctx_data(ctx) -> dict:
    out = {}
    for key, value in ctx.items():
        if hasattr(value, "_meta") and getattr(value, "pk", None) is not None:
            out[f"{key}_id"] = value.pk
        elif isinstance(value, (str, int, bool)):
            out[key] = value
    return out


def _refresh_caller(case, locked):
    if case is not locked:
        case.refresh_from_db(fields=_CASE_FIELDS)


def _after_commit(fn, *args):
    """Run `fn(*args)` after the outermost commit. A failure is logged, never raised: the
    decision / transition is already saved (a PENDING execution is picked up later by
    `workflow_resume_pending`)."""

    def callback():
        try:
            fn(*args)
        except Exception:
            logger.exception("after-commit step %s failed", getattr(fn, "__name__", fn))

    transaction.on_commit(callback)


def _request_analysis(case_id, tenant_id, round_, reason, feedback, correlation_id):
    """on_commit: ask the AnalysisTrigger for a new analysis round. Never raises."""
    request = hooks.AnalysisRequest(
        tenant_id=tenant_id,
        risk_case_id=case_id,
        round=round_,
        reason=reason,
        feedback=feedback,
        correlation_id=str(correlation_id),
    )
    try:
        hooks.get_analysis_trigger().request_analysis(request)
    except Exception as exc:
        logger.exception("analysis request failed for %s", case_id)
        try:
            case = RiskCase.objects.get(pk=case_id)
            record(
                SYSTEM,
                "analysis.request_failed",
                case,
                correlation_id=correlation_id,
                data={"round": round_, "error": f"{type(exc).__name__}: {str(exc)[:200]}"},
            )
        except Exception:
            logger.exception("audit of analysis.request_failed failed")


def transition(
    case: RiskCase, to_state: str, *, actor, reason: str, correlation_id=None, **ctx
) -> TransitionResult:
    """Move `case` to `to_state`, or raise. See WORKFLOW_DESIGN.md s2."""
    if to_state not in RiskCase.Status.values:
        raise ValueError(f"unknown status {to_state!r}")
    kind, role, actor_tenant_id = _actor_info(actor)
    reason = (reason or "")[:REASON_MAX]

    with transaction.atomic():
        try:
            locked = RiskCase.objects.select_for_update().get(
                pk=case.pk, tenant_id=case.tenant_id
            )
        except RiskCase.DoesNotExist:
            raise CaseNotFound() from None
        if actor_tenant_id is not None and actor_tenant_id != locked.tenant_id:
            raise CaseNotFound()

        frm = locked.status
        if frm == to_state:  # idempotent repeat: no audit, no precondition, no hook
            allowed = {a for e in TRANSITIONS.values() if e.to == to_state for a in e.actors}
            if role not in (allowed or {SYSTEM}):
                raise TransitionNotPermitted(frm, to_state, role)
            _refresh_caller(case, locked)
            return TransitionResult(locked, frm, to_state, False, None)

        edge = TRANSITIONS.get((frm, to_state))
        if edge is None:
            raise InvalidTransition(frm, to_state)
        if role not in edge.actors:
            raise TransitionNotPermitted(frm, to_state, role)
        err = edge.precondition(locked, ctx, reason)
        if err:
            raise PreconditionFailed(frm, to_state, err)

        fields = ["status", "updated_at"]
        with status_change_allowed():
            locked.status = to_state
            if edge.bumps_round:
                locked.analysis_round += 1
                fields.append("analysis_round")
            if edge.sets_recovered_at:
                locked.recovered_at = timezone.now()
                fields.append("recovered_at")
            locked.save(update_fields=fields)

        cid = correlation_id or locked.correlation_id
        data = {
            "from": frm,
            "to": to_state,
            "reason": reason,
            "trigger": str(edge.trigger),
            "analysis_round": locked.analysis_round,
            **_ctx_data(ctx),
        }
        request_cid = get_correlation_id()
        if request_cid and str(request_cid) != str(cid):
            data["request_correlation_id"] = str(request_cid)
        event = record(
            actor,
            "risk_case.status_changed",
            locked,
            correlation_id=cid,
            data=data,
        )

        if edge.requests_analysis:
            _after_commit(
                _request_analysis,
                locked.pk,
                locked.tenant_id,
                locked.analysis_round,
                reason,
                ctx.get("comments", "") if isinstance(ctx.get("comments", ""), str) else "",
                cid,
            )

    _refresh_caller(case, locked)
    return TransitionResult(locked, frm, to_state, True, event)


# ----------------------------------------------------------------------------- case actions
def close_case(case: RiskCase, *, actor, reason: str) -> TransitionResult:
    """-> CLOSED (T9, T14, T15, T16). PROCUREMENT_MANAGER or ADMIN; reason required where the
    edge says so. No HTTP endpoint yet (proposal in WORKFLOW_DESIGN.md, Q2)."""
    return transition(case, S.CLOSED, actor=actor, reason=reason)


def reanalyze_case(case: RiskCase, *, actor, reason: str, comments: str = "") -> TransitionResult:
    """REJECTED -> ANALYZING (T8), round + 1. PROCUREMENT_MANAGER only."""
    current = RiskCase.objects.filter(pk=case.pk, tenant_id=case.tenant_id).values_list(
        "status", flat=True
    ).first()
    if current is not None and current != S.REJECTED:
        raise InvalidTransition(current, S.ANALYZING)
    return transition(case, S.ANALYZING, actor=actor, reason=reason, comments=comments)


# ----------------------------------------------------------------------------- decision
@dataclass(frozen=True)
class DecisionResult:
    approval: Approval
    recommendation: Recommendation
    case: RiskCase
    execution: Execution | None


_REC_STATUS = {
    Approval.Decision.APPROVE: Recommendation.Status.APPROVED,
    Approval.Decision.REJECT: Recommendation.Status.REJECTED,
    Approval.Decision.REQUEST_CHANGES: Recommendation.Status.CHANGES_REQUESTED,
}


def record_decision(recommendation, decision, comments, *, user) -> DecisionResult:
    """The human approval gate. Lock order: RiskCase first, then Recommendation."""
    comments = (comments or "").strip()
    if not isinstance(user, User) or user.role != "PROCUREMENT_MANAGER":
        raise DecisionNotPermitted(getattr(user, "role", "UNKNOWN"))
    with transaction.atomic():
        try:
            case = RiskCase.objects.select_for_update().get(
                pk=recommendation.risk_case_id, tenant_id=user.tenant_id
            )
            rec = Recommendation.objects.select_for_update().get(
                pk=recommendation.pk, tenant_id=user.tenant_id
            )
        except (RiskCase.DoesNotExist, Recommendation.DoesNotExist):
            raise CaseNotFound() from None

        if case.status != S.PENDING_APPROVAL:
            raise DecisionConflict("CASE_NOT_PENDING_APPROVAL", current_status=case.status)
        if rec.round != case.analysis_round:
            raise DecisionConflict(
                "STALE_ROUND", round=rec.round, current_round=case.analysis_round
            )
        if rec.unsupported:
            raise DecisionRejected(
                "UNSUPPORTED_OPTION", unsupported_reasons=rec.unsupported_reasons
            )
        if rec.status != Recommendation.Status.PENDING:
            raise DecisionConflict("ALREADY_DECIDED", status=rec.status)

        try:
            with transaction.atomic():
                approval = Approval.objects.create(
                    tenant_id=case.tenant_id,
                    risk_case=case,
                    recommendation=rec,
                    round=rec.round,
                    decided_by=user,
                    decision=decision,
                    comments=comments,
                )
        except IntegrityError:
            raise DecisionConflict("ALREADY_APPROVED") from None

        rec.status = _REC_STATUS[decision]
        rec.save(update_fields=["status"])
        record(
            user,
            "recommendation.decided",
            rec,
            correlation_id=case.correlation_id,
            data={
                "decision": decision,
                "comments": comments,
                "approval_id": approval.pk,
                "risk_case_id": case.pk,
                "round": rec.round,
                "rank": rec.rank,
                "option_type": rec.option_type,
            },
        )

        execution = None
        if decision == Approval.Decision.APPROVE:
            transition(
                case, S.APPROVED, actor=user, reason="approved by user",
                approval=approval, recommendation=rec, comments=comments,
            )
            execution = Execution.objects.create(
                tenant_id=case.tenant_id,
                approval=approval,
                status=Execution.Status.PENDING,
                target_system="mock-erp",
                request_payload=rec.proposed_action,
            )
            _after_commit(start_execution, execution.pk, case.tenant_id, case.correlation_id)
        elif decision == Approval.Decision.REJECT:
            if not actionable_recommendations(case).exists():
                transition(
                    case, S.REJECTED, actor=user, reason="all options rejected",
                    approval=approval, recommendation=rec, comments=comments,
                )
        else:
            transition(
                case, S.ANALYZING, actor=user, reason="changes requested",
                approval=approval, recommendation=rec, comments=comments,
            )
    return DecisionResult(approval, rec, case, execution)


# ----------------------------------------------------------------------------- execution
def _lock_case_and_execution(execution_id, tenant_id):
    """Lock case first, then execution (same order everywhere)."""
    execution = Execution.objects.select_related("approval").get(
        pk=execution_id, tenant_id=tenant_id
    )
    case = RiskCase.objects.select_for_update().get(
        pk=execution.approval.risk_case_id, tenant_id=tenant_id
    )
    execution = Execution.objects.select_for_update().select_related("approval").get(
        pk=execution_id, tenant_id=tenant_id
    )
    return case, execution


def start_execution(execution_id: str, tenant_id: str, correlation_id) -> None:
    """APPROVED -> EXECUTING (T10) and hand the action to the ExecutionTrigger. Idempotent."""
    with transaction.atomic():
        try:
            case, exe = _lock_case_and_execution(execution_id, tenant_id)
        except (Execution.DoesNotExist, RiskCase.DoesNotExist):
            logger.warning("start_execution: %s not found", execution_id)
            return
        if exe.status != Execution.Status.PENDING or case.status != S.APPROVED:
            logger.info("start_execution: %s already started, nothing to do", execution_id)
            return
        transition(
            case, S.EXECUTING, actor=SYSTEM, reason="execution started", execution=exe,
            correlation_id=correlation_id,
        )
        exe.status = Execution.Status.RUNNING
        exe.started_at = timezone.now()
        exe.save(update_fields=["status", "started_at"])
        _after_commit(dispatch_execution, execution_id, tenant_id, correlation_id)


def _error_text(exc) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def dispatch_execution(execution_id: str, tenant_id: str, correlation_id) -> None:
    """Call the ExecutionTrigger OUTSIDE any transaction. A failing trigger fails the execution."""
    exe = Execution.objects.select_related("approval__recommendation").get(
        pk=execution_id, tenant_id=tenant_id
    )
    case_status = RiskCase.objects.filter(pk=exe.approval.risk_case_id).values_list(
        "status", flat=True
    ).first()
    if exe.status != Execution.Status.RUNNING or case_status != S.EXECUTING:
        logger.info("dispatch_execution: %s is not running, skipped", execution_id)
        return
    approval = exe.approval
    request = hooks.ExecutionRequest(
        execution_id=exe.pk,
        tenant_id=tenant_id,
        risk_case_id=approval.risk_case_id,
        approval_id=approval.pk,
        recommendation_id=approval.recommendation_id,
        option_type=approval.recommendation.option_type,
        proposed_action=exe.request_payload,
        correlation_id=str(correlation_id),
        attempt=exe.retry_count + 1,
    )
    try:
        hooks.get_execution_trigger().trigger(request)
    except Exception as exc:
        logger.exception("execution dispatch failed for %s", execution_id)
        record_execution_result(
            exe, outcome="FAILED", error_class=Execution.ErrorClass.DOWNSTREAM,
            error=_error_text(exc), actor=SYSTEM, correlation_id=correlation_id,
            attempt=request.attempt,
        )


def retry_execution(execution, *, actor, correlation_id=None) -> TransitionResult:
    """EXECUTION_FAILED -> EXECUTING (T13) for U-11's retry endpoint. Same Execution id means the
    same ERP Idempotency-Key, so a retry cannot duplicate the PO update (TC-08)."""
    with transaction.atomic():
        try:
            case, exe = _lock_case_and_execution(execution.pk, execution.tenant_id)
        except (Execution.DoesNotExist, RiskCase.DoesNotExist):
            raise CaseNotFound() from None
        if exe.status != Execution.Status.FAILED:
            raise DecisionConflict("EXECUTION_NOT_FAILED", status=exe.status)
        result = transition(
            case, S.EXECUTING, actor=actor, reason="retry requested", execution=exe,
            correlation_id=correlation_id,
        )
        exe.retry_count += 1
        exe.status = Execution.Status.RUNNING
        exe.error_class = None
        exe.error = None
        exe.started_at = timezone.now()
        exe.finished_at = None
        exe.save(update_fields=[
            "retry_count", "status", "error_class", "error", "started_at", "finished_at"
        ])
        _after_commit(
            dispatch_execution, exe.pk, exe.tenant_id, correlation_id or case.correlation_id
        )
    return result


def record_execution_result(
    execution,
    *,
    outcome: Literal["SUCCEEDED", "FAILED"],
    attempt: int,
    error_class=None,
    error=None,
    response_status=None,
    response_payload=None,
    actor,
    correlation_id=None,
) -> TransitionResult:
    """For U-10's result endpoint. SUCCEEDED means the ERP write was verified by re-reading the
    PO (TC-07); an unverified success is reported as FAILED / BUSINESS. `attempt` is the
    `ExecutionRequest.attempt` the result belongs to; anything but retry_count + 1 is a stale
    callback (409 STALE_EXECUTION_RESULT)."""
    if outcome not in ("SUCCEEDED", "FAILED"):
        raise ValueError(f"outcome must be SUCCEEDED or FAILED, not {outcome!r}")
    with transaction.atomic():
        try:
            case, exe = _lock_case_and_execution(execution.pk, execution.tenant_id)
        except (Execution.DoesNotExist, RiskCase.DoesNotExist):
            raise CaseNotFound() from None
        if attempt != exe.retry_count + 1:  # a late callback of an earlier attempt
            raise DecisionConflict(
                "STALE_EXECUTION_RESULT", attempt=attempt, current_attempt=exe.retry_count + 1
            )
        if exe.status == outcome:  # idempotent repeat (Boomi callback retry)
            return TransitionResult(case, case.status, case.status, False, None)
        if exe.status != Execution.Status.RUNNING:
            raise DecisionConflict("EXECUTION_NOT_RUNNING", status=exe.status)

        exe.status = outcome
        exe.finished_at = timezone.now()
        exe.response_status = response_status
        exe.response_payload = response_payload
        if outcome == "FAILED":
            exe.error_class = error_class
            exe.error = error
        else:
            exe.error_class = exe.error = None
        exe.save(update_fields=[
            "status", "finished_at", "response_status", "response_payload", "error_class", "error"
        ])
        if outcome == "SUCCEEDED":
            to_state, why = S.RECOVERED, "execution verified"
        else:
            to_state, why = S.EXECUTION_FAILED, f"execution failed: {error_class}"
        return transition(
            case, to_state, actor=actor, reason=why, execution=exe, correlation_id=correlation_id,
            error_class=error_class or "", retryable=exe.retryable,
        )
