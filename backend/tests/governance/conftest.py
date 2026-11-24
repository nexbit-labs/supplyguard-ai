import io

import pytest
from django.core.management import call_command

from apps.tenants.models import Tenant
from tests import workflow_support as ws
from tests.factories import make_user
from tests.helpers import api_key_client, jwt_client
from tests.workflow_support import fake_triggers  # noqa: F401  (re-exported fixture)


@pytest.fixture
def acme(db):
    call_command("load_seed", stdout=io.StringIO(), stderr=io.StringIO())
    return Tenant.objects.get(pk="TEN-ACME")


@pytest.fixture
def system(acme):
    """Boomi's credential: a SYSTEM API key of the seeded tenant."""
    return api_key_client(acme)


@pytest.fixture
def pm(acme):
    return make_user(acme, role="PROCUREMENT_MANAGER")


@pytest.fixture
def pm_client(pm):
    return jwt_client(pm)


@pytest.fixture
def triggers(fake_triggers):  # noqa: F811
    """Recording fake ExecutionTrigger / AnalysisTrigger; `triggers.exec` is the call list."""

    class _T:
        exec = ws.EXEC_REQUESTS
        analysis = ws.ANALYSIS_REQUESTS

    return _T
