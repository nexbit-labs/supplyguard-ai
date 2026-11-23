"""TC-10 and the TenantScopedMixin guards."""
import json

import pytest
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from apps.audit.models import AuditEvent
from tests.factories import make_case, make_supplier, make_tenant, make_user
from tests.helpers import jwt_client, strip_cid

pytestmark = pytest.mark.django_db
DENIED = "security.cross_tenant_denied"


@pytest.fixture(autouse=True)
def _urlconf():
    with override_settings(ROOT_URLCONF="tests.urls_tenancy"):
        yield


@pytest.fixture
def acme():
    return make_tenant("TEN-ACME")


@pytest.fixture
def globex():
    return make_tenant("TEN-GLOBEX")


@pytest.fixture
def acme_client(acme):
    return jwt_client(make_user(acme, role="ADMIN"))


@pytest.fixture
def globex_client(globex):
    return jwt_client(make_user(globex, role="ADMIN"))


def test_tc10_risk_case_of_other_tenant_is_indistinguishable_from_missing(
    acme_client, acme, globex
):
    foreign = make_case(globex)
    own = make_case(acme)
    assert acme_client.get(f"/test/risk-cases/{own.pk}").status_code == 200
    denied = acme_client.get(f"/test/risk-cases/{foreign.pk}")
    missing = acme_client.get("/test/risk-cases/RC-999999")
    assert denied.status_code == missing.status_code == 404
    assert strip_cid(denied) == strip_cid(missing)
    assert denied.json()["error"]["code"] == "NOT_FOUND"
    # same wire format except the correlation id: compare serialised bytes after removing it
    assert json.dumps(strip_cid(denied), sort_keys=True) == json.dumps(
        strip_cid(missing), sort_keys=True
    )
    assert foreign.pk not in denied.content.decode()
    assert "TEN-GLOBEX" not in denied.content.decode()


def test_tc10_supplier_of_other_tenant_404_and_audited(acme_client, acme, globex):
    foreign = make_supplier(globex)
    denied = acme_client.get(f"/test/suppliers/{foreign.pk}")
    missing = acme_client.get("/test/suppliers/999999")
    assert denied.status_code == missing.status_code == 404
    assert strip_cid(denied) == strip_cid(missing)
    row = AuditEvent.objects.get(action=DENIED)
    assert (row.entity_type, row.entity_id) == ("Supplier", str(foreign.pk))


def test_tc10_audit_row_belongs_to_requesting_tenant_only(
    acme_client, globex_client, acme, globex
):
    foreign = make_case(globex)
    acme_client.get(f"/test/risk-cases/{foreign.pk}")
    acme_client.get("/test/risk-cases/RC-999999")  # missing: no audit row
    rows = AuditEvent.objects.filter(action=DENIED)
    assert rows.count() == 1
    row = rows.get()
    assert row.tenant_id == "TEN-ACME"
    assert row.actor.startswith("USR-")
    assert row.entity_type == "RiskCase"
    assert row.data == {"path": f"/test/risk-cases/{foreign.pk}", "method": "GET"}
    # no foreign data in the row
    blob = json.dumps(
        [row.actor, row.action, row.entity_type, row.data, str(row.tenant_id)]
    )
    assert "TEN-GLOBEX" not in blob
    # visible to an ACME admin (GET /audit), invisible to GLOBEX
    seen = acme_client.get("/api/v1/audit?action=" + DENIED).json()["results"]
    assert [r["id"] for r in seen] == [row.pk]
    assert globex_client.get("/api/v1/audit?action=" + DENIED).json()["results"] == []


def test_own_row_and_missing_row_write_no_audit(acme_client, acme):
    own = make_case(acme)
    assert acme_client.get(f"/test/risk-cases/{own.pk}").status_code == 200
    acme_client.get("/test/risk-cases/RC-999999")
    assert not AuditEvent.objects.filter(action=DENIED).exists()


def test_wrongly_typed_id_is_a_plain_404(acme_client):
    assert acme_client.get("/test/suppliers/not-a-number").status_code == 404
    assert not AuditEvent.objects.filter(action=DENIED).exists()


def test_audit_failure_does_not_change_the_404(acme_client, globex, monkeypatch):
    foreign = make_case(globex)

    def boom(*a, **k):
        raise RuntimeError("audit down")

    monkeypatch.setattr("apps.audit.service.record", boom)
    r = acme_client.get(f"/test/risk-cases/{foreign.pk}")
    assert r.status_code == 404


def test_list_is_scoped_and_ignores_tenant_params(acme_client, acme, globex):
    own = make_case(acme)
    make_case(globex)
    for query in ("", "?tenant_id=TEN-GLOBEX", "?tenant=TEN-GLOBEX"):
        body = acme_client.get("/test/risk-cases" + query).json()
        assert [r["id"] for r in body["results"]] == [own.pk]


def test_api_key_caller_is_scoped_and_audited_as_system(acme, globex):
    from tests.helpers import api_key_client

    foreign, own = make_case(globex), make_case(acme)
    client = api_key_client(acme)
    assert client.get(f"/test/any/risk-cases/{own.pk}").status_code == 200
    assert client.get(f"/test/any/risk-cases/{foreign.pk}").status_code == 404
    row = AuditEvent.objects.get(action=DENIED)
    assert (row.actor, row.tenant_id) == ("SYSTEM", "TEN-ACME")


def test_mixin_fails_closed_without_a_tenant(acme, globex):
    from tests.urls_tenancy import RiskCaseViewSet

    make_case(acme)
    make_case(globex)
    view = RiskCaseViewSet()
    view.request = APIRequestFactory().get("/x")  # no request.tenant at all
    with pytest.raises(PermissionDenied):
        view.get_queryset()
    view.request.tenant = None
    with pytest.raises(PermissionDenied):
        view.get_queryset()


def test_mixin_fails_closed_over_http_when_tenant_missing(acme, monkeypatch):
    """A JWT user passes authentication; if request.tenant were absent the view must 403."""
    from apps.accounts import authentication

    monkeypatch.setattr(
        authentication.TenantJWTAuthentication,
        "authenticate",
        lambda self, request: (make_user(acme), None),
    )
    r = jwt_client(make_user(acme)).get("/test/risk-cases")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_perform_create_sets_requesters_tenant(acme):
    from apps.core.tenancy import TenantScopedMixin

    class Saved:
        kwargs = None

        def save(self, **kwargs):
            Saved.kwargs = kwargs

    mixin = TenantScopedMixin()
    mixin.request = APIRequestFactory().post("/x", {"tenant": "TEN-GLOBEX"})
    mixin.request.tenant = acme
    mixin.perform_create(Saved())
    assert Saved.kwargs == {"tenant": acme}
