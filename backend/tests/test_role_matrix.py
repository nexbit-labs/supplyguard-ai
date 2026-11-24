"""ROLE_MATRIX x principals x methods, plus the consistency checks between matrix and routes."""
import itertools

import pytest
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient

from apps.accounts.permissions import (
    ALL_ROLES,
    ROLE_MATRIX,
    USER_ROLES,
    RolePermission,
)
from apps.audit.models import AuditEvent
from tests.factories import make_tenant, make_user
from tests.helpers import api_key_client, jwt_client

pytestmark = pytest.mark.django_db
API = "/api/v1/"
_seq = itertools.count(1)


def _user_body():
    return {
        "username": f"matrix.user{next(_seq)}",
        "role": "VIEWER",
        "password": "Tr1cky-Horse-Battery-9",
    }


# The expected permissions, written out by hand from docs/CONTRACTS.md s2 + s4 and the DESIGN
# paragraph of U-04 in tasks/UDAY.md. It is deliberately NOT derived from ROLE_MATRIX: a change
# to the matrix must fail this test until this table is changed on purpose, too.
_ALL4 = {"ADMIN", "PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"}
EXPECTED = {
    "me.read": {"GET": _ALL4},
    "settings.read": {"GET": {"ADMIN"}},
    "settings.write": {"PATCH": {"ADMIN"}},
    "users.read": {"GET": {"ADMIN"}},
    "users.write": {"POST": {"ADMIN"}},
    # agenda D-2.6, not yet approved - update here and in ROLE_MATRIX together
    "audit.read": {"GET": {"ADMIN", "PROCUREMENT_MANAGER"}},
    "suppliers.read": {"GET": _ALL4},
    "dashboard.read": {"GET": _ALL4},
    "risk-cases.read": {"GET": _ALL4},
    "what-if": {"POST": {"ADMIN", "PROCUREMENT_MANAGER", "OPS_MANAGER"}},
    "recommendations.decision": {"POST": {"PROCUREMENT_MANAGER"}},
    "executions.retry": {"POST": {"PROCUREMENT_MANAGER"}},
    "events.create": {"POST": {"SYSTEM", "ADMIN"}},
    "demo.simulate": {"POST": {"ADMIN"}},
    "internal.risk-cases": {"POST": {"SYSTEM"}},
    "internal.analysis": {"POST": {"SYSTEM"}},
    "internal.execution-result": {"POST": {"SYSTEM"}},
    "integrations.status": {"GET": {"ADMIN"}},
}
EXPECTED_ROUTED = {"me.read", "settings.read", "settings.write", "users.read", "users.write",
                   "audit.read", "what-if"}  # keys that have a real endpoint today; add when routed

# permission_key -> (path, body factory or None). One entry per ROUTED matrix row.
ROUTES = {
    "me.read": (API + "me", None),
    "settings.read": (API + "settings", None),
    "settings.write": (API + "settings", lambda: {"max_cost_increase_pct": 5}),
    "users.read": (API + "users", None),
    "users.write": (API + "users", _user_body),
    "audit.read": (API + "audit", None),
    "what-if": (
        API + "what-if",
        lambda: {"supplier_id": "SUP-1008", "duration_days": 2, "event_type": "FACTORY_OUTAGE"},
    ),
}


@pytest.fixture(autouse=True)
def _stub_erp_for_what_if(monkeypatch):
    """The what-if route reads the ERP; the matrix tests only care about roles, not ERP data."""
    from apps.intelligence import whatif

    monkeypatch.setattr(
        whatif,
        "fetch_snapshot",
        lambda client, supplier_id, tenant_id, *a, **k: {
            "supplier": {"supplier_id": supplier_id, "name": "S", "tenant_id": tenant_id},
            "purchase_orders": [],
            "inventory": [],
            "alternate_suppliers": [],
        },
    )
    monkeypatch.setattr("apps.intelligence.views._client", lambda: None)
OK = {200, 201}


def _request(client, method, path, body_factory):
    body = body_factory() if body_factory else None
    return getattr(client, method.lower())(path, body, format="json")


def routed_cases():
    for key in sorted(EXPECTED_ROUTED):
        for method in EXPECTED[key]:
            yield key, method


def test_role_matrix_equals_the_hand_written_expectation():
    actual = {k: {m: set(r) for m, r in rule.methods.items()} for k, rule in ROLE_MATRIX.items()}
    assert actual == EXPECTED
    assert {k for k, rule in ROLE_MATRIX.items() if rule.routed} == EXPECTED_ROUTED


def _views_with_keys():
    """{permission_key: view class} for every view in the real urlconf that declares one."""
    found = {}

    def walk(patterns):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns)
            elif isinstance(p, URLPattern):
                cls = getattr(p.callback, "cls", None)
                key = getattr(cls, "permission_key", None)
                if key is not None:
                    for k in key.values() if isinstance(key, dict) else [key]:
                        found.setdefault(k, set()).add(cls)

    walk(get_resolver("config.urls").url_patterns)
    return found


def _views_using_role_permission():
    out = []

    def walk(patterns):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns)
            elif isinstance(p, URLPattern):
                cls = getattr(p.callback, "cls", None)
                if cls and any(
                    isinstance(c, type) and issubclass(c, RolePermission)
                    for c in getattr(cls, "permission_classes", [])
                ):
                    out.append(cls)

    walk(get_resolver("config.urls").url_patterns)
    return out


# --- the matrix itself ----------------------------------------------------------------------
@pytest.mark.parametrize("principal", sorted(ALL_ROLES))
def test_matrix_every_routed_row_for_every_principal(principal):
    tenant = make_tenant("TEN-MATRIX")
    if principal == "SYSTEM":
        client = api_key_client(tenant)
    else:
        client = jwt_client(make_user(tenant, role=principal))
    checked = 0
    for key, method in routed_cases():
        path, body = ROUTES[key]
        response = _request(client, method, path, body)
        allowed = principal in EXPECTED[key][method]
        where = f"{principal} {method} {path} ({key})"
        if allowed:
            assert response.status_code in OK, f"{where}: {response.status_code}"
        else:
            assert response.status_code == 403, f"{where}: {response.status_code}"
            assert response.json()["error"]["code"] == "FORBIDDEN"
        checked += 1
    assert checked == sum(len(EXPECTED[k]) for k in EXPECTED_ROUTED)


def test_unauthenticated_is_401_everywhere():
    client = APIClient()
    for key, method in routed_cases():
        path, body = ROUTES[key]
        r = _request(client, method, path, body)
        assert r.status_code == 401, f"{method} {path}"
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_inactive_tenant_user_403_everywhere_for_every_role():
    tenant = make_tenant("TEN-OFF", status="INACTIVE")
    for role in sorted(USER_ROLES):
        client = jwt_client(make_user(tenant, role=role))
        for key, method in routed_cases():
            path, body = ROUTES[key]
            r = _request(client, method, path, body)
            assert r.status_code == 403, f"{role} {method} {path}: {r.status_code}"


def test_tenantless_superuser_403_everywhere():
    from django.contrib.auth import get_user_model

    su = get_user_model().objects.create_superuser(
        username="root", password="pw-not-secret-1", email="r@example.com"
    )
    client = APIClient()
    client.force_authenticate(su)
    for key, method in routed_cases():
        path, body = ROUTES[key]
        r = _request(client, method, path, body)
        assert r.status_code == 403, f"{method} {path}: {r.status_code}"


def test_tenant_params_in_body_and_query_do_not_widen_access():
    own, other = make_tenant("TEN-OWN"), make_tenant("TEN-OTH")
    make_user(other, username="other.person")
    foreign = AuditEvent.objects.create(
        tenant=other, actor="SYSTEM", action="x.done", entity_type="RiskCase", entity_id="RC-1"
    )
    client = jwt_client(make_user(own, role="ADMIN", username="own.admin"))
    for param in ("tenant", "tenant_id"):
        users = client.get(f"{API}users?{param}=TEN-OTH").json()["results"]
        assert [u["username"] for u in users] == ["own.admin"]
        audit = client.get(f"{API}audit?{param}=TEN-OTH").json()["results"]
        assert foreign.pk not in [row["id"] for row in audit]
    # (settings and POST /users variants live in test_settings_users_api.py)


# --- consistency between matrix, views and routes ----------------------------------------------
def test_every_view_key_is_in_the_matrix():
    for key, classes in _views_with_keys().items():
        assert key in ROLE_MATRIX, f"{classes} use permission_key {key!r} missing from ROLE_MATRIX"


def test_every_routed_matrix_row_has_a_view_and_a_test_route():
    declared = _views_with_keys()
    for key, rule in ROLE_MATRIX.items():
        if rule.routed:
            assert key in declared, f"routed matrix row {key!r} has no view"
            assert key in ROUTES, f"routed matrix row {key!r} has no entry in ROUTES"
        else:
            assert key not in declared, f"{key!r} is routed but the matrix says routed=False"
    assert set(ROUTES) == {k for k, r in ROLE_MATRIX.items() if r.routed}


def test_every_role_permission_view_declares_a_key():
    for cls in _views_using_role_permission():
        assert getattr(cls, "permission_key", None) is not None, cls


def test_view_methods_are_all_covered_by_the_matrix():
    """A view must not implement a method the matrix does not list for its key."""
    for classes in _views_with_keys().values():
        for cls in classes:
            pk = cls.permission_key
            for method in cls.http_method_names:
                if method in ("options", "head") or not hasattr(cls, method):
                    continue
                if isinstance(pk, dict):
                    k = pk.get(method.upper())
                    assert k in ROLE_MATRIX, f"{cls.__name__}.{method} has no key"
                    assert method.upper() in ROLE_MATRIX[k].methods
                else:
                    assert method.upper() in ROLE_MATRIX[pk].methods, (
                        f"{cls.__name__}.{method} not in matrix row {pk}"
                    )


def test_matrix_shape_and_roles():
    for key, rule in ROLE_MATRIX.items():
        for method, roles in rule.methods.items():
            assert method in {"GET", "POST", "PATCH", "PUT", "DELETE"}, (key, method)
            assert roles and set(roles) <= ALL_ROLES, (key, roles)
    # SYSTEM appears only on event intake and the Boomi callbacks
    system_keys = {
        k for k, r in ROLE_MATRIX.items() if any("SYSTEM" in v for v in r.methods.values())
    }
    assert system_keys == {
        "events.create", "internal.risk-cases", "internal.analysis", "internal.execution-result",
    }


# --- fail-closed behaviour of the permission class ---------------------------------------------
def test_view_without_key_or_with_unknown_key_is_refused():
    from rest_framework.test import APIRequestFactory

    tenant = make_tenant("TEN-X")
    request = APIRequestFactory().get("/x")
    request.user = make_user(tenant, role="ADMIN")
    perm = RolePermission()

    class NoKey:
        pass

    class Unknown:
        permission_key = "does.not.exist"
        get = lambda self: None  # noqa: E731

    assert perm.has_permission(request, NoKey()) is False
    assert perm.has_permission(request, Unknown()) is False


def test_unlisted_method_the_view_implements_is_refused():
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    tenant = make_tenant("TEN-Y")

    class View:
        permission_key = "users.read"  # only GET listed

        def delete(self, request):  # pragma: no cover
            pass

    request = Request(APIRequestFactory().delete("/x"))
    request.user = make_user(tenant, role="ADMIN")
    assert RolePermission().has_permission(request, View()) is False
