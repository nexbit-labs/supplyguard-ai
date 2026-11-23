import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from tests.factories import make_tenant, make_user
from tests.helpers import jwt_client

pytestmark = pytest.mark.django_db
URL = "/api/v1/audit"


def mk(tenant, **kw):
    kw.setdefault("actor", "SYSTEM")
    kw.setdefault("action", "x.done")
    kw.setdefault("entity_type", "RiskCase")
    kw.setdefault("entity_id", "RC-1")
    return AuditEvent.objects.create(tenant=tenant, **kw)


def client_for(user):
    if isinstance(user, get_user_model()) and user.tenant_id:
        return jwt_client(user)  # real authentication: request.tenant comes from the credential
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def user_a(tenant_a):
    return make_user(tenant_a)


@pytest.fixture
def api(user_a):
    return client_for(user_a)


def ids(response):
    return [row["id"] for row in response.json()["results"]]


def assert_error(response, status, code):
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert set(error) == {"code", "message", "details", "correlation_id"}
    assert error["correlation_id"] == response["X-Correlation-ID"]


def test_requires_token():
    assert_error(APIClient().get(URL), 401, "UNAUTHENTICATED")


def test_only_own_tenant_even_with_tenant_params(api, tenant_a, tenant_b):
    own = mk(tenant_a)
    mk(tenant_b)
    for query in ("", "?tenant_id=TEN-B", "?tenant=TEN-B", "?tenant_id=TEN-B&tenant=TEN-B"):
        assert ids(api.get(URL + query)) == [own.pk]


def test_tenant_in_body_ignored_on_write_is_405(api, tenant_b):
    response = api.post(URL, {"tenant": tenant_b.pk, "action": "x"}, format="json")
    assert_error(response, 405, "VALIDATION_ERROR")


def test_null_tenant_rows_hidden(api, tenant_a):
    mk(None)
    own = mk(tenant_a)
    assert ids(api.get(URL)) == [own.pk]


def test_serializer_fields(api, tenant_a):
    cid = uuid.uuid4()
    mk(tenant_a, correlation_id=cid, data={"k": "v"})
    row = api.get(URL).json()["results"][0]
    assert set(row) == {
        "id", "timestamp", "actor", "action", "entity_type", "entity_id",
        "correlation_id", "data",
    }  # fmt: skip
    assert row["correlation_id"] == str(cid)
    assert row["data"] == {"k": "v"}


def test_filters_entity_type_action_correlation(api, tenant_a):
    cid = uuid.uuid4()
    a = mk(tenant_a, entity_type="Approval", action="approval.created", correlation_id=cid)
    mk(tenant_a, entity_type="RiskCase", action="case.opened")
    assert ids(api.get(URL + "?entity_type=Approval")) == [a.pk]
    assert ids(api.get(URL + "?action=approval.created")) == [a.pk]
    assert ids(api.get(URL + f"?correlation_id={cid}")) == [a.pk]
    assert ids(api.get(URL + "?entity_type=Approval&action=case.opened")) == []


def test_from_to_boundaries(api, tenant_a):
    base = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    early = mk(tenant_a, timestamp=base - timedelta(days=1))
    edge = mk(tenant_a, timestamp=base)
    late = mk(tenant_a, timestamp=base + timedelta(days=1))
    iso = "2026-11-10T12:00:00Z"
    assert set(ids(api.get(URL + f"?from={iso}"))) == {edge.pk, late.pk}  # inclusive
    assert set(ids(api.get(URL + f"?to={iso}"))) == {early.pk}  # exclusive
    assert ids(api.get(URL + f"?from={iso}&to={iso}")) == []
    # A plain date means midnight UTC; a naive datetime is treated as UTC.
    assert set(ids(api.get(URL + "?from=2026-11-11"))) == {late.pk}
    assert set(ids(api.get(URL + "?to=2026-11-10"))) == {early.pk}
    assert set(ids(api.get(URL + "?from=2026-11-10T12:00:00"))) == {edge.pk, late.pk}


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ("correlation_id=nope", "correlation_id"),
        ("from=yesterday", "from"),
        ("to=2026-13-45", "to"),
        ("from=2026-11-10T99:00:00", "from"),
    ],
)
def test_invalid_filters_400(api, query, field):
    response = api.get(URL + "?" + query)
    assert_error(response, 400, "VALIDATION_ERROR")
    assert field in response.json()["error"]["details"]


def test_user_without_tenant_403():
    # The DB forbids a non-superuser without a tenant, so use a stand-in authenticated user.
    ghost = SimpleNamespace(is_authenticated=True, is_active=True, tenant=None, pk=1)
    assert_error(client_for(ghost).get(URL), 403, "FORBIDDEN")


def test_superuser_without_tenant_403():
    su = get_user_model().objects.create_superuser(
        username="root", password="pw-not-secret-1", email="r@example.com"
    )
    assert_error(client_for(su).get(URL), 403, "FORBIDDEN")


def test_inactive_tenant_403():
    tenant = make_tenant("TEN-OFF", status="INACTIVE")
    mk(tenant)
    assert_error(client_for(make_user(tenant)).get(URL), 403, "FORBIDDEN")


def test_ordering_and_pagination(api, tenant_a):
    base = datetime(2026, 11, 1, tzinfo=UTC)
    rows = [mk(tenant_a, timestamp=base + timedelta(minutes=i)) for i in range(30)]
    same = [mk(tenant_a, timestamp=base + timedelta(days=5)) for _ in range(2)]
    first = api.get(URL).json()
    assert first["count"] == 32
    assert len(first["results"]) == 25
    assert [r["id"] for r in first["results"][:2]] == [same[1].pk, same[0].pk]  # -timestamp, -id
    assert first["results"][2]["id"] == rows[-1].pk
    second = api.get(URL + "?page=2").json()
    assert len(second["results"]) == 7
    assert second["results"][-1]["id"] == rows[0].pk


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_write_methods_405(api, method):
    assert_error(getattr(api, method)(URL, {}, format="json"), 405, "VALIDATION_ERROR")


def test_get_queryset_fails_closed_without_tenant_even_with_permissive_permission(
    monkeypatch, tenant_a
):
    from rest_framework.exceptions import PermissionDenied
    from rest_framework.permissions import AllowAny
    from rest_framework.test import APIRequestFactory

    from apps.audit.views import AuditEventList

    mk(None)  # a null-tenant row that `filter(tenant=None)` would have returned
    mk(tenant_a)
    monkeypatch.setattr(AuditEventList, "permission_classes", [AllowAny])
    ghost = SimpleNamespace(is_authenticated=True, is_active=True, tenant=None, tenant_id=None)
    su = get_user_model().objects.create_superuser(
        username="root2", password="pw-not-secret-1", email="r2@example.com"
    )
    for user in (ghost, su):
        view = AuditEventList()
        view.request = view.initialize_request(APIRequestFactory().get(URL))
        view.request.user = user
        with pytest.raises(PermissionDenied):
            view.get_queryset()
    # Through HTTP too: a permissive permission must still not leak the null-tenant row.
    assert client_for(su).get(URL).status_code == 403


def test_openapi_lists_audit_path():
    response = APIClient().get("/api/schema/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "/api/v1/audit" in body
    assert "entity_type" in body
    assert "%2B" in body  # from/to timezone-offset hint
