import json

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from apps.accounts.serializers import TokenObtainSerializer
from apps.audit.models import AuditEvent
from tests.factories import make_user
from tests.helpers import set_throttle_rates

pytestmark = pytest.mark.django_db
PASSWORD = "pw-not-secret-1"
TOKEN, REFRESH, ME = "/api/v1/auth/token", "/api/v1/auth/refresh", "/api/v1/me"
GENERIC = "No active account found with the given credentials"


@pytest.fixture(autouse=True)
def _urlconf():
    with override_settings(ROOT_URLCONF="tests.urls_auth"):
        yield


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(tenant_a):
    return make_user(tenant_a, username="alice", role="ADMIN", email="a@x.io", name="Alice")


def login(api, username="alice", password=PASSWORD):
    return api.post(TOKEN, {"username": username, "password": password}, format="json")


def bearer(token):
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


# --- login ---------------------------------------------------------------------------------
def test_login_success_returns_only_tokens_with_claims(api, user):
    r = login(api)
    assert r.status_code == 200
    assert set(r.json()) == {"access", "refresh"}
    for claims in (AccessToken(r.json()["access"]), RefreshToken(r.json()["refresh"])):
        assert claims["tenant_id"] == "TEN-A"
        assert claims["role"] == "ADMIN"
        # this simplejwt version serialises the pk claim as a string
        assert str(claims["user_id"]) == str(user.pk)


def test_wrong_password_and_unknown_user_same_401(api, user):
    a = login(api, password="nope")
    b = login(api, username="ghost")
    assert a.status_code == b.status_code == 401
    ea, eb = a.json()["error"], b.json()["error"]
    assert ea["message"] == eb["message"] == GENERIC
    assert ea["code"] == eb["code"] == "UNAUTHENTICATED"


def test_inactive_user_cannot_login(api, user):
    user.is_active = False
    user.save()
    r = login(api)
    assert r.status_code == 401 and r.json()["error"]["message"] == GENERIC


def test_inactive_tenant_cannot_login(api, user, tenant_a):
    tenant_a.status = "INACTIVE"
    tenant_a.save()
    r = login(api)
    assert r.status_code == 401 and r.json()["error"]["message"] == GENERIC
    assert AuditEvent.objects.filter(action="auth.login_failed").count() == 1


def test_superuser_without_tenant_cannot_login(api):
    get_user_model().objects.create_superuser("root", "r@x.io", PASSWORD)
    r = login(api, "root")
    assert r.status_code == 401 and r.json()["error"]["message"] == GENERIC


def test_login_audit_rows_and_no_password_anywhere(api, user):
    login(api)
    login(api, password="wrong-password-xyz")
    ok = AuditEvent.objects.get(action="auth.login")
    assert ok.actor == user.api_id and ok.tenant_id == "TEN-A"
    bad = AuditEvent.objects.get(action="auth.login_failed")
    assert bad.actor == "anonymous" and bad.tenant is None
    assert bad.data == {"username": "alice"}
    dump = json.dumps(list(AuditEvent.objects.values("actor", "action", "entity_id", "data")))
    assert "wrong-password-xyz" not in dump and PASSWORD not in dump


def test_failed_login_username_truncated(api, tenant_a):
    make_user(tenant_a, username="u" * 150)
    login(api, username="u" * 400)
    assert len(AuditEvent.objects.get(action="auth.login_failed").data["username"]) == 150


def test_failed_login_audit_keeps_username_only_when_it_names_a_user(api, user):
    login(api, username="ALICE", password="bad")  # known, case-insensitive
    pasted = "my-secret-pw-Tr0ub4dor"
    login(api, username=pasted, password="bad")  # someone pasted a password as username
    rows = {tuple(r.data) : r.data for r in AuditEvent.objects.filter(action="auth.login_failed")}
    assert rows[("username",)] == {"username": "ALICE"}
    assert rows[("username_known",)] == {"username_known": False}
    assert pasted not in str(list(AuditEvent.objects.values_list("data", flat=True)))


def test_audit_failure_does_not_break_login(api, user, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("audit down")

    monkeypatch.setattr("apps.audit.service.record", boom)
    assert login(api).status_code == 200
    assert login(api, password="bad").status_code == 401


# --- token validation against the DB ---------------------------------------------------------
def test_valid_token_authenticates(api, user):
    access = login(api).json()["access"]
    r = api.get(ME, **bearer(access))
    assert r.status_code == 200


def test_forged_role_claim_rejected(api, user):
    user.role = "VIEWER"
    user.save()
    forged = TokenObtainSerializer.get_token(user).access_token
    forged["role"] = "ADMIN"  # valid signature (same key), claim differs from the DB
    assert api.get(ME, **bearer(str(forged))).status_code == 401


def test_token_without_tenant_or_role_claims_rejected(api, user):
    for missing in ("tenant_id", "role"):
        token = TokenObtainSerializer.get_token(user).access_token
        del token[missing]
        assert api.get(ME, **bearer(str(token))).status_code == 401


def test_token_with_wrong_tenant_claim_rejected(api, user, tenant_b):
    token = TokenObtainSerializer.get_token(user).access_token
    token["tenant_id"] = tenant_b.id
    assert api.get(ME, **bearer(str(token))).status_code == 401


def test_plain_simplejwt_token_without_claims_rejected(api, user):
    assert api.get(ME, **bearer(str(RefreshToken.for_user(user).access_token))).status_code == 401


def test_role_change_after_issue_invalidates_then_refresh_carries_new_role(api, user):
    tokens = login(api).json()
    user.role = "VIEWER"
    user.save()
    assert api.get(ME, **bearer(tokens["access"])).status_code == 401
    new = api.post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert new.status_code == 200 and set(new.json()) == {"access"}
    assert AccessToken(new.json()["access"])["role"] == "VIEWER"
    r = api.get(ME, **bearer(new.json()["access"]))
    assert r.status_code == 200 and r.json()["role"] == "VIEWER"


def test_deactivated_user_token_rejected_and_cannot_refresh(api, user):
    tokens = login(api).json()
    user.is_active = False
    user.save()
    assert api.get(ME, **bearer(tokens["access"])).status_code == 401
    assert api.post(REFRESH, {"refresh": tokens["refresh"]}, format="json").status_code == 401


def test_inactive_tenant_cannot_refresh(api, user, tenant_a):
    tokens = login(api).json()
    tenant_a.status = "INACTIVE"
    tenant_a.save()
    assert api.post(REFRESH, {"refresh": tokens["refresh"]}, format="json").status_code == 401


def test_access_token_as_refresh_rejected(api, user):
    tokens = login(api).json()
    r = api.post(REFRESH, {"refresh": tokens["access"]}, format="json")
    assert r.status_code == 401


def test_refresh_garbage_and_missing(api, user):
    assert api.post(REFRESH, {"refresh": "garbage"}, format="json").status_code == 401
    assert api.post(REFRESH, {}, format="json").status_code == 400


def test_refresh_does_not_rotate(api, user):
    tokens = login(api).json()
    r = api.post(REFRESH, {"refresh": tokens["refresh"]}, format="json")
    assert "refresh" not in r.json()
    assert api.post(REFRESH, {"refresh": tokens["refresh"]}, format="json").status_code == 200


def test_no_credentials_is_401(api):
    assert api.get(ME).status_code == 401


# --- throttle -----------------------------------------------------------------------------------
def test_login_throttled_after_low_rate(api, user, monkeypatch):
    set_throttle_rates(monkeypatch, auth="3/min")
    codes = [login(api, password="bad").status_code for _ in range(5)]
    assert codes == [401, 401, 401, 429, 429]
    body = login(api, password="bad").json()["error"]
    assert body["code"] == "RATE_LIMITED"  # 429 has its own code (agenda D-1.5)


def test_refresh_has_its_own_budget(api, user, monkeypatch):
    set_throttle_rates(monkeypatch, auth="2/min", auth_refresh="3/min")
    assert [login(api, password="bad").status_code for _ in range(3)] == [401, 401, 429]
    refresh = TokenObtainSerializer.get_token(user)
    codes = [
        api.post(REFRESH, {"refresh": str(refresh)}, format="json").status_code for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]  # login exhaustion did not touch refresh


def test_rotating_x_forwarded_for_does_not_defeat_the_ip_limit(api, user, monkeypatch):
    set_throttle_rates(monkeypatch, auth="3/min")
    codes = [
        api.post(
            TOKEN,
            {"username": f"u{i}", "password": "bad"},
            format="json",
            HTTP_X_FORWARDED_FOR=f"203.0.113.{i}",
        ).status_code
        for i in range(5)
    ]
    assert codes == [401, 401, 401, 429, 429]  # NUM_PROXIES=0: the header is ignored


def test_num_proxies_default_is_zero():
    from django.conf import settings

    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == 0


def test_login_throttled_per_lowercased_username(api, user, monkeypatch):
    set_throttle_rates(monkeypatch, auth_username="2/min")
    codes = [
        login(api, username=name, password="bad").status_code
        for name in ("alice", "ALICE", "Alice", "alice")
    ]
    assert codes == [401, 401, 429, 429]
    assert login(api, username="bob", password="bad").status_code == 401  # other name unaffected


def test_other_endpoints_not_throttled(api, user, monkeypatch):
    set_throttle_rates(monkeypatch, auth="1/min")
    access = login(api).json()["access"]
    for _ in range(4):
        assert api.get(ME, **bearer(access)).status_code == 200


# --- /me ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["ADMIN", "PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"])
def test_me_shape_for_each_role(api, tenant_a, role):
    u = make_user(tenant_a, role=role, email="e@x.io", name="Eve")
    r = api.get(ME, **bearer(str(TokenObtainSerializer.get_token(u).access_token)))
    assert r.status_code == 200
    assert r.json() == {
        "id": f"USR-{u.pk}",
        "email": "e@x.io",
        "name": "Eve",
        "role": role,
        "tenant": {"id": "TEN-A", "name": "TEN-A"},
    }


def test_me_forbidden_for_inactive_tenant(api, user, tenant_a):
    access = login(api).json()["access"]
    tenant_a.status = "INACTIVE"
    tenant_a.save()
    assert api.get(ME, **bearer(access)).status_code == 403


def test_me_forbidden_for_tenantless_superuser(api):
    root = get_user_model().objects.create_superuser("root", "r@x.io", PASSWORD)
    api.force_authenticate(root)
    assert api.get(ME).status_code == 403


def test_request_tenant_ignores_query_and_body(api, user, tenant_b):
    access = login(api).json()["access"]
    r = api.get("/api/v1/me?tenant_id=TEN-B", **bearer(access))
    assert r.json()["tenant"]["id"] == "TEN-A"


def test_schema_builds_with_both_auth_schemes(api, capsys):
    r = api.get("/api/schema/?format=json")
    assert r.status_code == 200
    doc = json.loads(r.content)
    schemes = doc["components"]["securitySchemes"]
    assert schemes["jwtAuth"] == {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    assert schemes["apiKeyAuth"] == {"type": "apiKey", "in": "header", "name": "X-Api-Key"}
    for route in ("/api/v1/auth/token", "/api/v1/auth/refresh", "/api/v1/me"):
        assert route in doc["paths"]
    assert "unable to resolve" not in capsys.readouterr().err.lower()

