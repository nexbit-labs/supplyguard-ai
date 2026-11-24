"""Table-driven tests of transition() (WORKFLOW_DESIGN.md s5.1)."""
import itertools
from unittest import mock

import pytest
from django.db import transaction

from apps.audit.models import AuditEvent
from apps.core.models import AgentRun, Recommendation, RiskCase
from apps.core.models.cases import StatusChangeNotAllowed
from apps.workflow import services
from apps.workflow.exceptions import (
    CaseNotFound,
    InvalidTransition,
    PreconditionFailed,
    TransitionNotPermitted,
)
from apps.workflow.services import transition
from apps.workflow.states import TRANSITIONS
from tests import workflow_support as ws
from tests.factories import (
    make_agent_run,
    make_approval,
    make_execution,
    make_rec,
    make_user,
)
from tests.workflow_support import case_in, fake_triggers  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("fake_triggers")]
S = RiskCase.Status
ALL = list(RiskCase.Status.values)

# Written out by hand (NOT derived from TRANSITIONS): a change to either side fails the test.
ALLOWED = [
    ("RECEIVED", "ANALYZING"),  # T1
    ("ANALYZING", "RECOMMENDATION_READY"),  # T2
    ("RECOMMENDATION_READY", "PENDING_APPROVAL"),  # T3
    ("RECOMMENDATION_READY", "CLOSED"),  # T4
    ("PENDING_APPROVAL", "APPROVED"),  # T5
    ("PENDING_APPROVAL", "REJECTED"),  # T6
    ("PENDING_APPROVAL", "ANALYZING"),  # T7
    ("REJECTED", "ANALYZING"),  # T8
    ("REJECTED", "CLOSED"),  # T9
    ("APPROVED", "EXECUTING"),  # T10
    ("EXECUTING", "RECOVERED"),  # T11
    ("EXECUTING", "EXECUTION_FAILED"),  # T12
    ("EXECUTION_FAILED", "EXECUTING"),  # T13
    ("EXECUTION_FAILED", "CLOSED"),  # T14
    ("RECOVERED", "CLOSED"),  # T15
    ("ANALYZING", "CLOSED"),  # T16 (Q3)
]
FORBIDDEN = [(a, b) for a, b in itertools.permutations(ALL, 2) if (a, b) not in ALLOWED]
ROUND_BUMPS = {("PENDING_APPROVAL", "ANALYZING"), ("REJECTED", "ANALYZING")}
PM_ROLE = "PROCUREMENT_MANAGER"


def test_allowed_table_is_exactly_the_16_pairs():
    assert len(ALLOWED) == 16 and len(FORBIDDEN) == 74
    assert set(TRANSITIONS) == set(ALLOWED)


def _build(tenant, frm, to):
    """-> (case in `frm`, actor, ctx) with everything the edge's precondition needs."""
    case = case_in(tenant, frm)
    pm = make_user(tenant, role=PM_ROLE)
    ctx = {}
    actor = "SYSTEM"
    edge = (frm, to)
    if edge == ("ANALYZING", "RECOMMENDATION_READY"):
        make_agent_run(tenant, case, agent=AgentRun.Agent.RECOVERY_PLANNER, valid=True)
    elif edge == ("RECOMMENDATION_READY", "PENDING_APPROVAL"):
        make_rec(tenant, case)
    elif edge == ("PENDING_APPROVAL", "APPROVED"):
        ctx["approval"] = make_approval(tenant, make_rec(tenant, case), pm)
        actor = pm
    elif edge == ("PENDING_APPROVAL", "REJECTED"):
        rec = make_rec(tenant, case, status=Recommendation.Status.REJECTED)
        ctx["approval"] = make_approval(tenant, rec, pm, decision="REJECT", comments="no")
        actor = pm
    elif edge == ("PENDING_APPROVAL", "ANALYZING"):
        rec = make_rec(tenant, case, status=Recommendation.Status.CHANGES_REQUESTED)
        ctx["approval"] = make_approval(
            tenant, rec, pm, decision="REQUEST_CHANGES", comments="cheaper"
        )
        ctx["comments"] = "cheaper"
        actor = pm
    elif frm in ("APPROVED", "EXECUTING", "EXECUTION_FAILED") and to == "EXECUTING":
        approval = make_approval(tenant, make_rec(tenant, case), pm)
        if frm == "APPROVED":
            ctx["execution"] = make_execution(tenant, approval)
            actor = "SYSTEM"
        else:
            ctx["execution"] = make_execution(
                tenant, approval, status="FAILED", error_class="TRANSIENT"
            )
            actor = pm
    elif edge in (("EXECUTING", "RECOVERED"), ("EXECUTING", "EXECUTION_FAILED")):
        approval = make_approval(tenant, make_rec(tenant, case), pm)
        if to == "RECOVERED":
            ctx["execution"] = make_execution(tenant, approval, status="SUCCEEDED")
        else:
            ctx["execution"] = make_execution(
                tenant, approval, status="FAILED", error_class="TRANSIENT"
            )
    elif to == "CLOSED" and frm in ("REJECTED", "EXECUTION_FAILED", "RECOVERED", "ANALYZING"):
        actor = pm
    elif edge == ("REJECTED", "ANALYZING"):
        actor = pm
    return case, actor, ctx


@pytest.mark.parametrize("frm,to", ALLOWED)
def test_every_allowed_pair_succeeds(tenant_a, frm, to):
    case, actor, ctx = _build(tenant_a, frm, to)
    round_before = case.analysis_round
    before = AuditEvent.objects.filter(action="risk_case.status_changed").count()

    result = transition(case, to, actor=actor, reason="because", **ctx)

    assert result.changed and result.from_state == frm and result.to_state == to
    case.refresh_from_db()
    assert case.status == to and result.case.status == to
    events = AuditEvent.objects.filter(action="risk_case.status_changed")
    assert events.count() == before + 1
    ev = events.latest("id")
    assert ev.data["from"] == frm and ev.data["to"] == to and ev.data["reason"] == "because"
    assert ev.tenant_id == tenant_a.id and ev.correlation_id == case.correlation_id
    assert ev.entity_id == case.pk and result.audit_event.pk == ev.pk
    assert case.analysis_round == round_before + ((frm, to) in ROUND_BUMPS)
    assert (case.recovered_at is not None) == (to == "RECOVERED")


@pytest.mark.parametrize("frm,to", FORBIDDEN)
def test_every_forbidden_pair_is_invalid(tenant_a, frm, to):
    case = case_in(tenant_a, frm)
    pm = make_user(tenant_a, role=PM_ROLE)
    with pytest.raises(InvalidTransition) as exc:
        transition(case, to, actor=pm, reason="x")
    assert exc.value.status_code == 409
    case.refresh_from_db()
    assert case.status == frm
    assert not AuditEvent.objects.filter(action="risk_case.status_changed").exists()


@pytest.mark.parametrize("state", ALL)
def test_self_transition_is_a_noop(tenant_a, state):
    case = case_in(tenant_a, state)
    actors = {a for e in TRANSITIONS.values() if e.to == state for a in e.actors} or {"SYSTEM"}
    actor = "SYSTEM" if "SYSTEM" in actors else make_user(tenant_a, role=sorted(actors)[0])
    result = transition(case, state, actor=actor, reason="again")
    assert result.changed is False and result.audit_event is None
    assert not AuditEvent.objects.filter(action="risk_case.status_changed").exists()
    assert not ws.ANALYSIS_REQUESTS


def test_unknown_state_and_bad_actor_are_programming_errors(tenant_a):
    case = case_in(tenant_a, S.RECEIVED)
    with pytest.raises(ValueError):
        transition(case, "NOPE", actor="SYSTEM", reason="x")
    with pytest.raises(TypeError):
        transition(case, S.ANALYZING, actor="someone", reason="x")


# --- preconditions -> 422 ----------------------------------------------------------------------
def _pm(tenant):
    return make_user(tenant, role=PM_ROLE)


def _fails(case, to, actor="SYSTEM", **ctx):
    with pytest.raises(PreconditionFailed) as exc:
        transition(case, to, actor=actor, reason="x", **ctx)
    assert exc.value.status_code == 422
    assert exc.value.details["reason"] == "PRECONDITION_FAILED"
    case.refresh_from_db()
    assert not AuditEvent.objects.filter(action="risk_case.status_changed").exists()


def test_t2_needs_valid_planner_run_of_this_round(tenant_a):
    case = case_in(tenant_a, S.ANALYZING)
    _fails(case, S.RECOMMENDATION_READY)
    make_agent_run(tenant_a, case, agent="RECOVERY_PLANNER", valid=False)
    make_agent_run(tenant_a, case, agent="RISK_ANALYST", valid=True)
    make_agent_run(tenant_a, case, agent="RECOVERY_PLANNER", valid=True, round=2)
    _fails(case, S.RECOMMENDATION_READY)


def test_t3_never_pending_approval_with_zero_options(tenant_a):
    case = case_in(tenant_a, S.RECOMMENDATION_READY)
    _fails(case, S.PENDING_APPROVAL)
    make_rec(tenant_a, case, unsupported=True)
    make_rec(tenant_a, case, round=2)
    _fails(case, S.PENDING_APPROVAL)


def test_t4_close_only_without_actionable_options(tenant_a):
    case = case_in(tenant_a, S.RECOMMENDATION_READY)
    make_rec(tenant_a, case)
    _fails(case, S.CLOSED)


def test_t5_t6_t7_approval_checks(tenant_a, tenant_b):
    pm = _pm(tenant_a)
    case = case_in(tenant_a, S.PENDING_APPROVAL)
    rec = make_rec(tenant_a, case)
    _fails(case, S.APPROVED, pm)  # missing approval
    reject = make_approval(tenant_a, rec, pm, decision="REJECT", comments="n")
    _fails(case, S.APPROVED, pm, approval=reject)  # wrong decision
    other = case_in(tenant_a, S.PENDING_APPROVAL)
    other_appr = make_approval(tenant_a, make_rec(tenant_a, other), pm)
    _fails(case, S.APPROVED, pm, approval=other_appr)  # other case
    _fails(case, S.REJECTED, pm, approval=reject)  # an actionable option is left
    _fails(case, S.ANALYZING, pm, approval=reject)  # wrong decision for T7


def test_t5_other_round_approval(tenant_a):
    pm = _pm(tenant_a)
    case = case_in(tenant_a, S.PENDING_APPROVAL)
    old = make_rec(tenant_a, case, round=1)
    approval = make_approval(tenant_a, old, pm)
    RiskCase.objects.filter(pk=case.pk).update(analysis_round=2)
    case.refresh_from_db()
    _fails(case, S.APPROVED, pm, approval=approval)


def test_t10_execution_checks(tenant_a):
    pm = _pm(tenant_a)
    case = case_in(tenant_a, S.APPROVED)
    approval = make_approval(tenant_a, make_rec(tenant_a, case), pm)
    _fails(case, S.EXECUTING)  # missing
    running = make_execution(tenant_a, approval, status="RUNNING")
    _fails(case, S.EXECUTING, execution=running)  # not PENDING


def test_t11_t12_wrong_execution_status(tenant_a):
    pm = _pm(tenant_a)
    case = case_in(tenant_a, S.EXECUTING)
    approval = make_approval(tenant_a, make_rec(tenant_a, case), pm)
    running = make_execution(tenant_a, approval, status="RUNNING")
    _fails(case, S.RECOVERED, execution=running)
    _fails(case, S.EXECUTION_FAILED, execution=running)


def test_t13_not_retryable(tenant_a, settings):
    pm = _pm(tenant_a)
    case = case_in(tenant_a, S.EXECUTION_FAILED)
    approval = make_approval(tenant_a, make_rec(tenant_a, case), pm)
    exe = make_execution(
        tenant_a, approval, status="FAILED", error_class="TRANSIENT",
        retry_count=settings.EXECUTION_MAX_RETRIES,
    )
    _fails(case, S.EXECUTING, pm, execution=exe)
    exe.retry_count, exe.error_class = 0, "VALIDATION"
    exe.save()
    _fails(case, S.EXECUTING, pm, execution=exe)


@pytest.mark.parametrize("frm", ["REJECTED", "EXECUTION_FAILED", "ANALYZING"])
def test_close_needs_a_reason(tenant_a, frm):
    case = case_in(tenant_a, frm)
    with pytest.raises(PreconditionFailed) as exc:
        services.close_case(case, actor=_pm(tenant_a), reason="  ")
    assert exc.value.status_code == 422
    assert RiskCase.objects.get(pk=case.pk).status == frm


# --- actors -> 403 -----------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["VIEWER", "OPS_MANAGER", "ADMIN"])
def test_non_pm_users_cannot_run_pm_only_edges(tenant_a, role):
    case, _, ctx = _build(tenant_a, "PENDING_APPROVAL", "APPROVED")
    with pytest.raises(TransitionNotPermitted) as exc:
        transition(case, S.APPROVED, actor=make_user(tenant_a, role=role), reason="x", **ctx)
    assert exc.value.status_code == 403
    case.refresh_from_db()
    assert case.status == S.PENDING_APPROVAL


def test_system_cannot_run_user_edge_and_pm_cannot_run_system_edge(tenant_a):
    case, _, ctx = _build(tenant_a, "PENDING_APPROVAL", "APPROVED")
    with pytest.raises(TransitionNotPermitted):
        transition(case, S.APPROVED, actor="SYSTEM", reason="x", **ctx)
    case2 = case_in(tenant_a, S.RECEIVED)
    with pytest.raises(TransitionNotPermitted):
        transition(case2, S.ANALYZING, actor=_pm(tenant_a), reason="x")


def test_close_roles_and_reanalyze_roles(tenant_a):
    admin = make_user(tenant_a, role="ADMIN")
    ops = make_user(tenant_a, role="OPS_MANAGER")
    case = case_in(tenant_a, S.RECOVERED)
    with pytest.raises(TransitionNotPermitted):
        services.close_case(case, actor=ops, reason="x")
    assert services.close_case(case, actor=admin, reason="done").to_state == S.CLOSED
    rejected = case_in(tenant_a, S.REJECTED)
    with pytest.raises(TransitionNotPermitted):  # re-analysing is PM only
        services.reanalyze_case(rejected, actor=admin, reason="again")
    result = services.reanalyze_case(rejected, actor=_pm(tenant_a), reason="again", comments="c")
    assert result.to_state == S.ANALYZING and result.case.analysis_round == 2


def test_reanalyze_only_from_rejected(tenant_a):
    case = case_in(tenant_a, S.PENDING_APPROVAL)
    with pytest.raises(InvalidTransition):
        services.reanalyze_case(case, actor=_pm(tenant_a), reason="x")


# --- tenant ------------------------------------------------------------------------------------
def test_other_tenant_actor_gets_not_found(tenant_a, tenant_b):
    case = case_in(tenant_a, S.RECOVERED)
    stranger = make_user(tenant_b, role=PM_ROLE)
    with pytest.raises(CaseNotFound) as exc:
        transition(case, S.CLOSED, actor=stranger, reason="x")
    assert exc.value.status_code == 404
    case.refresh_from_db()
    assert case.status == S.RECOVERED
    assert not AuditEvent.objects.filter(action="risk_case.status_changed").exists()


def test_wrong_tenant_id_on_case_instance_is_not_found(tenant_a, tenant_b):
    case = case_in(tenant_a, S.RECEIVED)
    case.tenant_id = tenant_b.id
    with pytest.raises(CaseNotFound):
        transition(case, S.ANALYZING, actor="SYSTEM", reason="x")


# --- guard, atomicity, rounds, hooks ---------------------------------------------------------
def test_stale_caller_instance_is_refreshed_and_cannot_revert_status(tenant_a):
    case = case_in(tenant_a, S.RECEIVED)
    stale = RiskCase.objects.get(pk=case.pk)
    transition(case, S.ANALYZING, actor="SYSTEM", reason="x")
    assert stale.status == S.RECEIVED  # untouched copy
    again = RiskCase.objects.get(pk=case.pk)
    transition(again, S.ANALYZING, actor="SYSTEM", reason="repeat")  # no-op refreshes caller
    assert case.status == S.ANALYZING
    case.title = "t"
    case.save()  # full save must not write an old status back
    assert RiskCase.objects.get(pk=case.pk).status == S.ANALYZING


def test_status_cannot_be_changed_outside_transition(tenant_a):
    case = case_in(tenant_a, S.RECEIVED)
    case.status = S.CLOSED
    with pytest.raises(StatusChangeNotAllowed):
        case.save()


def test_audit_failure_rolls_the_status_back(tenant_a):
    case = case_in(tenant_a, S.RECEIVED)
    with mock.patch.object(services, "record", side_effect=RuntimeError("audit down")):
        with pytest.raises(RuntimeError):
            transition(case, S.ANALYZING, actor="SYSTEM", reason="x")
    assert RiskCase.objects.get(pk=case.pk).status == S.RECEIVED


def test_request_changes_and_reanalyze_bump_round_by_one_and_ask_for_analysis(
    tenant_a, django_capture_on_commit_callbacks
):
    pm = _pm(tenant_a)
    case, _, ctx = _build(tenant_a, "PENDING_APPROVAL", "ANALYZING")
    with django_capture_on_commit_callbacks(execute=True):
        transition(case, S.ANALYZING, actor=pm, reason="changes", **ctx)
    assert case.analysis_round == 2
    assert len(ws.ANALYSIS_REQUESTS) == 1
    req = ws.ANALYSIS_REQUESTS[0]
    assert req.round == 2 and req.feedback == "cheaper" and req.risk_case_id == case.pk

    reject = case_in(tenant_a, S.REJECTED)
    with django_capture_on_commit_callbacks(execute=True):
        services.reanalyze_case(reject, actor=pm, reason="again")
    assert reject.analysis_round == 2 and len(ws.ANALYSIS_REQUESTS) == 2


def test_idempotent_repeat_schedules_no_hook(tenant_a, django_capture_on_commit_callbacks):
    case = case_in(tenant_a, S.ANALYZING)
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        transition(case, S.ANALYZING, actor=_pm(tenant_a), reason="x")
    assert callbacks == [] and not ws.ANALYSIS_REQUESTS


def test_failing_analysis_trigger_is_audited_not_raised(
    tenant_a, settings, django_capture_on_commit_callbacks
):
    settings.WORKFLOW_ANALYSIS_TRIGGER = "tests.test_workflow_transitions.BrokenAnalysis"
    case = case_in(tenant_a, S.REJECTED)
    with django_capture_on_commit_callbacks(execute=True):
        services.reanalyze_case(case, actor=_pm(tenant_a), reason="again")
    assert AuditEvent.objects.filter(action="analysis.request_failed").count() == 1


class BrokenAnalysis:
    def request_analysis(self, request):
        raise TimeoutError("boomi")


def test_nested_in_outer_atomic_rolls_back_with_it(tenant_a):
    case = case_in(tenant_a, S.RECEIVED)
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            transition(case, S.ANALYZING, actor="SYSTEM", reason="x")
            raise RuntimeError("outer failure")
    assert RiskCase.objects.get(pk=case.pk).status == S.RECEIVED
    assert not AuditEvent.objects.filter(action="risk_case.status_changed").exists()


def test_self_transition_still_checks_the_actor(tenant_a):
    case = case_in(tenant_a, S.APPROVED)  # only a PM may move a case into APPROVED
    for actor in ("SYSTEM", make_user(tenant_a, role="VIEWER")):
        with pytest.raises(TransitionNotPermitted) as exc:
            transition(case, S.APPROVED, actor=actor, reason="again")
        assert exc.value.status_code == 403
    assert transition(case, S.APPROVED, actor=_pm(tenant_a), reason="again").changed is False
