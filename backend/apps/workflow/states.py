"""The RiskCase transition table (WORKFLOW_DESIGN.md s1; CONTRACTS s7).

The table is data: `transition()` has no per-state if-chains. Every (from, to) pair that is not a
key of TRANSITIONS is forbidden. 16 allowed edges (15 of the design + T16 ANALYZING -> CLOSED,
decided in Q3).
"""
from collections.abc import Callable
from dataclasses import dataclass

from django.db import models

from apps.core.models import AgentRun, Approval, Execution, Recommendation, RiskCase

S = RiskCase.Status
SYSTEM = "SYSTEM"
PM = "PROCUREMENT_MANAGER"
ADMIN = "ADMIN"


class Trigger(models.TextChoices):
    SYS_IMPACT = "SYS_IMPACT"
    SYS_AGENT = "SYS_AGENT"
    USER_DECISION = "USER_DECISION"
    SYS_EXEC = "SYS_EXEC"
    USER_RETRY = "USER_RETRY"
    USER_CASE = "USER_CASE"


def actionable_recommendations(case):
    """Options of the CURRENT round that a PM may still decide. The only definition used."""
    return Recommendation.objects.filter(
        risk_case_id=case.pk,
        round=case.analysis_round,
        unsupported=False,
        status=Recommendation.Status.PENDING,
    )


# --- preconditions: (case, ctx, reason) -> error text or None ----------------------------------
def _none(case, ctx, reason):
    return None


def _reason_required(case, ctx, reason):
    return None if (reason or "").strip() else "A reason is required."


def _planner_run(case, ctx, reason):
    ok = AgentRun.objects.filter(
        risk_case_id=case.pk,
        agent=AgentRun.Agent.RECOVERY_PLANNER,
        round=case.analysis_round,
        valid=True,
    ).exists()
    return None if ok else "No valid RECOVERY_PLANNER run for the current round."


def _has_actionable(case, ctx, reason):
    if actionable_recommendations(case).count() >= 1:
        return None
    return "No actionable recommendation in the current round."


def _no_actionable(case, ctx, reason):
    if actionable_recommendations(case).count() == 0:
        return None
    return "An actionable recommendation still exists."


def _approval_check(decision, extra=None):
    def check(case, ctx, reason):
        approval = ctx.get("approval")
        if not isinstance(approval, Approval) or approval.pk is None:
            return "A saved approval is required."
        if approval.decision != decision:
            return f"The approval is not a {decision}."
        if approval.risk_case_id != case.pk:
            return "The approval belongs to another case."
        if approval.round != case.analysis_round:
            return "The approval is from another round."
        if decision == Approval.Decision.APPROVE and approval.recommendation.unsupported:
            return "The approved recommendation is unsupported."
        return extra(case, ctx, reason) if extra else None

    return check


def _execution_of_case(ctx, case):
    execution = ctx.get("execution")
    if not isinstance(execution, Execution) or execution.pk is None:
        return None, "A saved execution is required."
    if execution.approval.risk_case_id != case.pk:
        return None, "The execution belongs to another case."
    return execution, None


def _exec_start(case, ctx, reason):
    execution, err = _execution_of_case(ctx, case)
    if err:
        return err
    if execution.status != Execution.Status.PENDING:
        return "The execution is not PENDING."
    approval = execution.approval
    if approval.decision != Approval.Decision.APPROVE:
        return "The execution's approval is not an APPROVE."
    if approval.round != case.analysis_round:
        return "The execution's approval is from another round."
    return None


def _exec_succeeded(case, ctx, reason):
    execution, err = _execution_of_case(ctx, case)
    if err:
        return err
    return None if execution.status == Execution.Status.SUCCEEDED else "Execution is not SUCCEEDED."


def _exec_failed(case, ctx, reason):
    execution, err = _execution_of_case(ctx, case)
    if err:
        return err
    if execution.status != Execution.Status.FAILED or not execution.error_class:
        return "Execution is not FAILED with an error class."
    return None


def _exec_retry(case, ctx, reason):
    execution, err = _execution_of_case(ctx, case)
    if err:
        return err
    return None if execution.retryable else "The execution is not retryable."


@dataclass(frozen=True)
class Edge:
    frm: str
    to: str
    trigger: Trigger
    actors: tuple  # "SYSTEM" and/or user roles
    precondition: Callable = _none
    bumps_round: bool = False
    sets_recovered_at: bool = False
    requests_analysis: bool = False


_E = Edge
_A = Approval.Decision
EDGES = [
    _E(S.RECEIVED, S.ANALYZING, Trigger.SYS_IMPACT, (SYSTEM,)),
    _E(S.ANALYZING, S.RECOMMENDATION_READY, Trigger.SYS_AGENT, (SYSTEM,), _planner_run),
    _E(S.RECOMMENDATION_READY, S.PENDING_APPROVAL, Trigger.SYS_AGENT, (SYSTEM,), _has_actionable),
    _E(S.RECOMMENDATION_READY, S.CLOSED, Trigger.SYS_AGENT, (SYSTEM,), _no_actionable),
    _E(S.PENDING_APPROVAL, S.APPROVED, Trigger.USER_DECISION, (PM,), _approval_check(_A.APPROVE)),
    _E(S.PENDING_APPROVAL, S.REJECTED, Trigger.USER_DECISION, (PM,),
       _approval_check(_A.REJECT, _no_actionable)),
    _E(S.PENDING_APPROVAL, S.ANALYZING, Trigger.USER_DECISION, (PM,),
       _approval_check(_A.REQUEST_CHANGES), bumps_round=True, requests_analysis=True),
    _E(S.REJECTED, S.ANALYZING, Trigger.USER_CASE, (PM,), bumps_round=True,
       requests_analysis=True),
    _E(S.REJECTED, S.CLOSED, Trigger.USER_CASE, (PM, ADMIN), _reason_required),
    _E(S.APPROVED, S.EXECUTING, Trigger.SYS_EXEC, (SYSTEM,), _exec_start),
    _E(S.EXECUTING, S.RECOVERED, Trigger.SYS_EXEC, (SYSTEM,), _exec_succeeded,
       sets_recovered_at=True),
    _E(S.EXECUTING, S.EXECUTION_FAILED, Trigger.SYS_EXEC, (SYSTEM,), _exec_failed),
    _E(S.EXECUTION_FAILED, S.EXECUTING, Trigger.USER_RETRY, (PM,), _exec_retry),
    _E(S.EXECUTION_FAILED, S.CLOSED, Trigger.USER_CASE, (PM, ADMIN), _reason_required),
    _E(S.RECOVERED, S.CLOSED, Trigger.USER_CASE, (PM, ADMIN)),
    # T16 (Q3): the manual way out of a stuck ANALYZING.
    _E(S.ANALYZING, S.CLOSED, Trigger.USER_CASE, (PM, ADMIN), _reason_required),
]

TRANSITIONS: dict[tuple[str, str], Edge] = {(e.frm, e.to): e for e in EDGES}


def is_allowed(frm: str, to: str) -> bool:
    return (frm, to) in TRANSITIONS
