"""Shared helpers for tests/test_workflow_*.py: recording fake triggers + case builders."""
import pytest

from apps.core.models import RiskCase
from apps.core.models.cases import status_change_allowed
from tests.factories import make_case, make_rec

EXEC_REQUESTS: list = []
ANALYSIS_REQUESTS: list = []


class FakeExecutionTrigger:
    def trigger(self, request):
        EXEC_REQUESTS.append(request)


class RaisingExecutionTrigger:
    def trigger(self, request):
        raise ConnectionError("boomi unreachable")


class FakeAnalysisTrigger:
    def request_analysis(self, request):
        ANALYSIS_REQUESTS.append(request)


@pytest.fixture
def fake_triggers(settings):
    EXEC_REQUESTS.clear()
    ANALYSIS_REQUESTS.clear()
    settings.WORKFLOW_EXECUTION_TRIGGER = "tests.workflow_support.FakeExecutionTrigger"
    settings.WORKFLOW_ANALYSIS_TRIGGER = "tests.workflow_support.FakeAnalysisTrigger"
    yield
    EXEC_REQUESTS.clear()
    ANALYSIS_REQUESTS.clear()


def set_status(case, status):
    """Test-only: put a case in `status` the one way the model guard allows."""
    with status_change_allowed():
        case.status = status
        case.save(update_fields=["status", "updated_at"])
    return case


def case_in(tenant, status, **kw):
    return set_status(make_case(tenant, **kw), status)


def pending_case(tenant, n_recs=1, **kw):
    case = case_in(tenant, RiskCase.Status.PENDING_APPROVAL, **kw)
    recs = [
        make_rec(tenant, case, rank=i + 1, proposed_action={"type": "RESCHEDULE", "n": i})
        for i in range(n_recs)
    ]
    return case, recs
