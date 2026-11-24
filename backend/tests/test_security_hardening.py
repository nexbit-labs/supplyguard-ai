"""Regression tests for the U-04 security review (each fails without its fix)."""
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.accounts.permissions import (
    NO_TENANT_MESSAGE,
    ROLE_DENIED_MESSAGE,
    SYSTEM_DENIED_MESSAGE,
)
from apps.audit.models import AuditEvent
from apps.tenants.settings_schema import validate_tenant_settings
from tests.factories import make_case, make_tenant, make_user
from tests.helpers import api_key_client, jwt_client, set_throttle_rates

pytestmark = pytest.mark.django_db
USERS, ME, AUDIT, SETTINGS = "/api/v1/users", "/api/v1/me", "/api/v1/audit", "/api/v1/settings"
GOOD_PW = "Tr1cky-Horse-Battery-9"


@pytest.fixture
def tenancy_urls():
    with override_settings(ROOT_URLCONF="tests.urls_tenancy"):
        yield


def bad_key(i=0):
    return "sgk_" + f"{i:040d}"


def post_bad_keys(n, ip="10.0.0.1"):
    client = APIClient()
    return [
        client.get(ME, HTTP_X_API_KEY=bad_key(i), REMOTE_ADDR=ip).status_code for i in range(n)
    ]


# --- 3: failure flooding -----------------------------------------------------------------------
def test_bad_api_keys_write_bounded_audit_rows_then_429():
    codes = post_bad_keys(40)
    assert codes[:30] == [401] * 30
    assert codes[30:] == [429] * 10
    rows = AuditEvent.objects.filter(action="security.api_key_rejected").count()
    assert rows == 10  # AUTH_FAIL_AUDIT_PER_MIN, not 30 and not 40


def test_429_for_api_keys_has_rate_limited_code():
    post_bad_keys(30)
    r = APIClient().get(ME, HTTP_X_API_KEY=bad_key(99), REMOTE_ADDR="10.0.0.1")
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"


@override_settings(ROOT_URLCONF="tests.urls_auth")
def test_blocked_ip_still_accepts_a_valid_key_but_429s_a_wrong_one(tenant_a):
    """Egress IPs (Boomi) are shared between tenants: a valid key always authenticates."""
    raw = api_key_client(tenant_a)._credentials["HTTP_X_API_KEY"]
    post_bad_keys(35)  # 10.0.0.1 is now over the failure limit
    from apps.accounts import failguard

    def failures():
        from django.test import RequestFactory

        return failguard.api_key_failures(RequestFactory().get("/", REMOTE_ADDR="10.0.0.1"))

    before = failures()
    for _ in range(5):
        ok = APIClient().get("/test/whoami", HTTP_X_API_KEY=raw, REMOTE_ADDR="10.0.0.1")
        assert ok.status_code == 200 and ok.json()["role"] == "SYSTEM"
    assert failures() == before  # valid requests are not counted
    wrong = APIClient().get("/test/whoami", HTTP_X_API_KEY=bad_key(999), REMOTE_ADDR="10.0.0.1")
    assert wrong.status_code == 429
    other_ip = APIClient().get("/test/whoami", HTTP_X_API_KEY=bad_key(999), REMOTE_ADDR="10.0.0.2")
    assert other_ip.status_code == 401


@override_settings(ROOT_URLCONF="tests.urls_auth")
def test_revoked_key_retry_loop_does_not_block_other_tenants_on_the_same_ip(tenant_a, tenant_b):
    revoked = api_key_client(tenant_a)
    good = api_key_client(tenant_b)
    from apps.accounts.models import ApiKey

    ApiKey.objects.filter(tenant=tenant_a).update(is_active=False)
    for _ in range(40):
        revoked.get("/test/whoami", REMOTE_ADDR="10.9.9.9")
    r = good.get("/test/whoami", REMOTE_ADDR="10.9.9.9")
    assert r.status_code == 200 and r.json()["tenant"] == "TEN-B"


def test_spoofed_forwarded_for_does_not_escape_the_failure_counter():
    client = APIClient()
    codes = [
        client.get(
            ME,
            HTTP_X_API_KEY=bad_key(i),
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR=f"198.51.100.{i}",
        ).status_code
        for i in range(32)
    ]
    assert codes[-2:] == [429, 429]


def test_failed_login_audit_rows_are_bounded(tenant_a):
    make_user(tenant_a, username="alice")
    client = APIClient()
    for i in range(25):
        client.post(
            "/api/v1/auth/token", {"username": f"nobody{i}", "password": "x"}, format="json"
        )
    assert AuditEvent.objects.filter(action="auth.login_failed").count() == 10


def test_skipped_audit_write_logs_one_warning(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="apps.accounts.failguard"):
        post_bad_keys(14)
    warnings = [r for r in caplog.records if r.name == "apps.accounts.failguard"]
    assert len(warnings) == 1
    assert "sgk_" not in warnings[0].getMessage()


# --- 4: secure by default ----------------------------------------------------------------------
def test_default_permission_is_the_role_permission():
    from django.conf import settings

    assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
        "apps.accounts.permissions.RolePermission"
    ]


def test_view_without_permission_key_is_refused_for_user_and_system(tenancy_urls, tenant_a):
    admin = jwt_client(make_user(tenant_a, role="ADMIN"))
    r = admin.get("/test/nokey")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    assert "leaked" not in r.content.decode()
    assert api_key_client(tenant_a).get("/test/nokey").status_code == 403
    assert APIClient().get("/test/nokey").status_code == 401


def test_public_endpoints_still_work(db):
    client = APIClient()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/schema/").status_code == 200
    assert client.get("/api/docs/").status_code == 200


def test_mixin_refuses_inactive_tenant(tenancy_urls):
    off = make_tenant("TEN-OFF", status="INACTIVE")
    make_case(off)
    r = jwt_client(make_user(off, role="ADMIN")).get("/test/risk-cases")
    assert r.status_code == 403
    assert r.json()["error"]["message"] == NO_TENANT_MESSAGE


# --- 5: no false cross-tenant alarms -----------------------------------------------------------
def test_own_row_hidden_by_a_narrowing_queryset_is_a_plain_404_without_audit(
    tenancy_urls, tenant_a
):
    own = make_case(tenant_a)  # risk_level HIGH; the view only shows LOW
    r = jwt_client(make_user(tenant_a, role="ADMIN")).get(f"/test/narrowed/{own.pk}")
    assert r.status_code == 404
    assert not AuditEvent.objects.filter(action="security.cross_tenant_denied").exists()


def test_real_foreign_hit_through_a_narrowing_view_still_audits(tenancy_urls, tenant_a, tenant_b):
    foreign = make_case(tenant_b)
    r = jwt_client(make_user(tenant_a, role="ADMIN")).get(f"/test/narrowed/{foreign.pk}")
    assert r.status_code == 404
    row = AuditEvent.objects.get(action="security.cross_tenant_denied")
    assert row.tenant_id == tenant_a.pk


# --- messages ----------------------------------------------------------------------------------
def message(response):
    return response.json()["error"]["message"]


def test_message_role_not_permitted(tenant_a):
    r = jwt_client(make_user(tenant_a, role="VIEWER")).get(USERS)
    assert r.status_code == 403 and message(r) == ROLE_DENIED_MESSAGE


def test_message_system_not_permitted(tenant_a):
    r = api_key_client(tenant_a).get(AUDIT)
    assert r.status_code == 403 and message(r) == SYSTEM_DENIED_MESSAGE


def test_message_inactive_tenant_user_and_system_superuser(tenant_a):
    off = make_tenant("TEN-OFF", status="INACTIVE")
    r = jwt_client(make_user(off, role="ADMIN")).get(USERS)
    assert r.status_code == 403 and message(r) == NO_TENANT_MESSAGE
    su = get_user_model().objects.create_superuser(
        username="root", password="pw-not-secret-1", email="r@example.com"
    )
    c = APIClient()
    c.force_authenticate(su)
    assert message(c.get(USERS)) == NO_TENANT_MESSAGE


# --- 15: SYSTEM exclusion is RolePermission's own job ------------------------------------------
def test_role_permission_alone_refuses_system_where_matrix_does_not_list_it(tenant_a):
    from rest_framework.test import APIRequestFactory

    from apps.accounts.permissions import RolePermission
    from apps.accounts.principals import SystemPrincipal

    class FakeKey:
        tenant = tenant_a
        tenant_id = tenant_a.pk

    request = APIRequestFactory().get("/x")
    request.user = SystemPrincipal(FakeKey())

    class View:
        permission_key = "users.read"
        get = None

    assert RolePermission().has_permission(request, View()) is False

    class IntakeView:
        permission_key = "events.create"  # SYSTEM is listed (POST only)
        post = None

    post = APIRequestFactory().post("/x")
    post.user = request.user
    assert RolePermission().has_permission(post, IntakeView()) is True


# --- 6: settings bounds and overflow -----------------------------------------------------------
FULL = {
    "risk_thresholds": {"medium": 40, "high": 65, "critical": 85},
    "max_cost_increase_pct": 15,
    "require_qualified_alternates": True,
}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["risk_thresholds"].update(critical=10**400),
        lambda d: d["risk_thresholds"].update(critical=101),
        lambda d: d["risk_thresholds"].update(medium=-1),
        lambda d: d["risk_thresholds"].update(exposure_full_scale=10**400),
        lambda d: d["risk_thresholds"].update(exposure_full_scale=1e13),
        lambda d: d["risk_thresholds"].update(exposure_full_scale=1e12 * 10),
        lambda d: d.update(max_cost_increase_pct=10**400),
        lambda d: d.update(max_cost_increase_pct=1000.5),
        lambda d: d.update(max_cost_increase_pct=-0.1),
    ],
)
def test_settings_out_of_bounds_or_huge_rejected(mutate):
    import copy

    data = copy.deepcopy(FULL)
    mutate(data)
    with pytest.raises(ValidationError):
        validate_tenant_settings(data)


def test_settings_bounds_inclusive_edges_accepted():
    data = {
        "risk_thresholds": {"medium": 0, "high": 50, "critical": 100, "exposure_full_scale": 1e12},
        "max_cost_increase_pct": 1000,
        "require_qualified_alternates": False,
    }
    assert validate_tenant_settings(data) == data


def test_huge_int_over_http_is_400_not_500(tenant_a):
    api = jwt_client(make_user(tenant_a, role="ADMIN"))
    r = api.generic(
        "PATCH", SETTINGS, b'{"max_cost_increase_pct": 1' + b"0" * 400 + b"}", "application/json"
    )
    assert r.status_code == 400


# --- 7: usernames ------------------------------------------------------------------------------
def create(api, username, **kw):
    body = {"username": username, "role": "VIEWER", "password": GOOD_PW, **kw}
    return api.post(USERS, body, format="json")


@pytest.fixture
def admin_api(tenant_a):
    return jwt_client(make_user(tenant_a, role="ADMIN", username="boss"))


@pytest.mark.parametrize(
    "name", ["evil user/<b>", "a b", "tab\tname", "name​", "​name", "x/y", "<script>"]
)
def test_bad_characters_in_username_rejected(admin_api, name):
    r = create(admin_api, name)
    assert r.status_code == 400
    assert "username" in r.json()["error"]["details"]
    assert not get_user_model().objects.filter(username__startswith="name").exists()


def test_case_insensitive_duplicate_rejected_with_generic_message(admin_api):
    assert create(admin_api, "alice").status_code == 201
    r = create(admin_api, "Alice")
    assert r.status_code == 400
    assert r.json()["error"]["details"]["username"] == ["This username is not available."]
    assert get_user_model().objects.filter(username__iexact="alice").count() == 1


def test_fullwidth_lookalike_is_normalised_and_clashes(admin_api):
    fullwidth = "ｂｏｓｓ"  # fullwidth "boss" (the admin's own username)
    r = create(admin_api, fullwidth)
    assert r.status_code == 400
    assert r.json()["error"]["details"]["username"] == ["This username is not available."]


def test_fullwidth_username_is_stored_normalised(admin_api):
    assert create(admin_api, "ｎｅｗ").status_code == 201
    assert get_user_model().objects.filter(username="new").exists()


# --- throttle rates stay configurable ----------------------------------------------------------
def test_set_throttle_rates_helper_overrides(monkeypatch, tenant_a):
    set_throttle_rates(monkeypatch, auth="1/min")
    make_user(tenant_a, username="zed")
    client = APIClient()
    codes = [
        client.post("/api/v1/auth/token", {"username": "zed", "password": "bad"}, format="json")
        .status_code
        for _ in range(2)
    ]
    assert codes == [401, 429]
