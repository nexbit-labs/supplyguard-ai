"""Regression tests for the second U-04 review round (m1 .. m11)."""
import copy
import logging
import os

import pytest
from django.contrib.auth import get_user_model
from django.core.checks import Warning as CheckWarning
from django.db import IntegrityError, transaction
from django.test import Client, override_settings
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.test import APIClient
from rest_framework.views import APIView

from apps.accounts.checks import num_proxies_check
from apps.accounts.permissions import RolePermission
from apps.audit import record
from apps.audit.models import AuditEvent
from tests.factories import make_supplier, make_user
from tests.helpers import api_key_client, jwt_client, set_throttle_rates

pytestmark = pytest.mark.django_db
TOKEN, USERS, SETTINGS = "/api/v1/auth/token", "/api/v1/users", "/api/v1/settings"
PW = "pw-not-secret-1"
GOOD_PW = "Tr1cky-Horse-Battery-9"


def login(client, username, password="bad", **extra):
    return client.post(TOKEN, {"username": username, "password": password}, format="json", **extra)


# --- m1: separate audit budgets per action; one warning with IP and skip count ----------------
def test_bad_api_keys_do_not_hide_failed_logins(tenant_a):
    make_user(tenant_a, username="alice")
    client = APIClient()
    for i in range(14):
        client.get("/api/v1/me", HTTP_X_API_KEY="sgk_" + f"{i:040d}", REMOTE_ADDR="10.1.1.1")
    assert AuditEvent.objects.filter(action="security.api_key_rejected").count() == 10
    login(client, "alice", REMOTE_ADDR="10.1.1.1")
    assert AuditEvent.objects.filter(action="auth.login_failed").count() == 1


def test_skip_warning_names_the_ip_and_the_skip_count(caplog):
    client = APIClient()
    with caplog.at_level(logging.WARNING, logger="apps.accounts.failguard"):
        for i in range(13):
            client.get("/api/v1/me", HTTP_X_API_KEY="sgk_" + f"{i:040d}", REMOTE_ADDR="10.2.2.2")
    msgs = [r.getMessage() for r in caplog.records if r.name == "apps.accounts.failguard"]
    assert len(msgs) == 1
    assert "10.2.2.2" in msgs[0] and "skipped 1 so far" in msgs[0]
    assert "sgk_" not in msgs[0]


# --- m2 / m3: per-username throttle counts FAILED logins, any JSON scalar type -----------------
def test_numeric_username_cannot_skip_the_limit(monkeypatch):
    set_throttle_rates(monkeypatch, auth_username="2/min")
    client = APIClient()
    codes = [client.post(TOKEN, {"username": 12345, "password": "x"}, format="json").status_code
             for _ in range(4)]
    assert codes == [401, 401, 429, 429]


@pytest.mark.parametrize("value", [None, {"a": 1}, ["x"], True])
def test_unusable_usernames_do_not_crash(value):
    r = APIClient().post(TOKEN, {"username": value, "password": "x"}, format="json")
    assert r.status_code in (400, 401)


def test_successful_logins_never_use_up_the_username_limit(monkeypatch, tenant_a):
    set_throttle_rates(monkeypatch, auth_username="2/min")
    make_user(tenant_a, username="alice")
    client = APIClient()
    assert [login(client, "alice", PW).status_code for _ in range(6)] == [200] * 6


def test_failures_lock_the_username_even_for_the_correct_password(monkeypatch, tenant_a):
    set_throttle_rates(monkeypatch, auth_username="3/min")
    make_user(tenant_a, username="alice")
    client = APIClient()
    assert [login(client, "alice").status_code for _ in range(3)] == [401, 401, 401]
    locked = login(client, "alice", PW)  # correct password, but inside the lock window
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "RATE_LIMITED"
    assert login(client, "bob", PW).status_code == 401  # other usernames are unaffected


# --- m4: GET -> PATCH round trip leaves DB and audit untouched --------------------------------
def test_settings_round_trip_changes_nothing(tenant_a):
    tenant_a.settings = {
        "risk_thresholds": {"medium": 40, "high": 65, "critical": 85},
        "max_cost_increase_pct": 15,
        "require_qualified_alternates": True,
    }
    tenant_a.save()
    stored = copy.deepcopy(tenant_a.settings)
    api = jwt_client(make_user(tenant_a, role="ADMIN"))
    body = api.get(SETTINGS).json()
    assert body["risk_thresholds"]["exposure_full_scale"] == 500000
    r = api.patch(SETTINGS, body, format="json")
    assert r.status_code == 200 and r.json() == body
    tenant_a.refresh_from_db()
    assert tenant_a.settings == stored  # the default was not stored
    assert not AuditEvent.objects.filter(action="settings.updated").exists()


def test_settings_patch_real_change_alongside_default_exposure(tenant_a):
    tenant_a.settings = {"risk_thresholds": {"medium": 40, "high": 65, "critical": 85}}
    tenant_a.save()
    api = jwt_client(make_user(tenant_a, role="ADMIN"))
    api.patch(
        SETTINGS,
        {"risk_thresholds": {"exposure_full_scale": 500000, "high": 70}},
        format="json",
    )
    tenant_a.refresh_from_db()
    assert "exposure_full_scale" not in tenant_a.settings["risk_thresholds"]
    row = AuditEvent.objects.get(action="settings.updated")
    assert "exposure_full_scale" not in str(row.data)
    assert tenant_a.settings["risk_thresholds"]["high"] == 70


def test_explicit_non_default_exposure_is_stored(tenant_a):
    tenant_a.settings = {"risk_thresholds": {"medium": 40, "high": 65, "critical": 85}}
    tenant_a.save()
    api = jwt_client(make_user(tenant_a, role="ADMIN"))
    api.patch(SETTINGS, {"risk_thresholds": {"exposure_full_scale": 123}}, format="json")
    tenant_a.refresh_from_db()
    assert tenant_a.settings["risk_thresholds"]["exposure_full_scale"] == 123


# --- m5: ASCII usernames and DB-level case-insensitive uniqueness -----------------------------
def create(api, username):
    return api.post(
        USERS, {"username": username, "role": "VIEWER", "password": GOOD_PW}, format="json"
    )


def test_cyrillic_lookalike_rejected(tenant_a):
    api = jwt_client(make_user(tenant_a, role="ADMIN"))
    r = create(api, "аlice")  # Cyrillic a + "lice"
    assert r.status_code == 400 and "username" in r.json()["error"]["details"]
    assert not get_user_model().objects.filter(username__icontains="lice").exists()


def test_db_rejects_case_duplicates_even_without_the_serializer(tenant_a, tenant_b):
    get_user_model().objects.create_user(username="alice", password=PW, tenant=tenant_a)
    with pytest.raises(IntegrityError), transaction.atomic():
        get_user_model().objects.create_user(username="Alice", password=PW, tenant=tenant_b)


def test_migration_adds_the_lower_unique_constraint():
    names = [c.name for c in get_user_model()._meta.constraints]
    assert "accounts_user_username_lower_uniq" in names


# --- m6: own row with the same erp_id is not a cross-tenant alarm -----------------------------
@override_settings(ROOT_URLCONF="tests.urls_tenancy")
def test_own_erp_row_hidden_by_narrowing_while_another_tenant_has_the_same_erp_id(
    tenant_a, tenant_b
):
    make_supplier(tenant_a, erp_id="SUP-1")
    make_supplier(tenant_b, erp_id="SUP-1")
    api = jwt_client(make_user(tenant_a, role="ADMIN"))
    assert api.get("/test/narrowed-suppliers/SUP-1").status_code == 404
    assert not AuditEvent.objects.filter(action="security.cross_tenant_denied").exists()


@override_settings(ROOT_URLCONF="tests.urls_tenancy")
def test_foreign_only_erp_id_still_audits(tenant_a, tenant_b):
    make_supplier(tenant_b, erp_id="SUP-9")
    api = jwt_client(make_user(tenant_a, role="ADMIN"))
    assert api.get("/test/narrowed-suppliers/SUP-9").status_code == 404
    assert AuditEvent.objects.filter(action="security.cross_tenant_denied").count() == 1


# --- m7: the JSON error handlers are really wired ---------------------------------------------
def test_error_handlers_are_wired_to_the_core_handlers():
    from apps.core import handlers

    resolver = get_resolver("config.urls")
    for code, fn in ((400, handlers.bad_request), (403, handlers.permission_denied),
                     (404, handlers.not_found), (500, handlers.server_error)):
        assert resolver.resolve_error_handler(code) is fn, code


def test_api_404_is_json_through_the_real_urlconf():
    r = Client().get("/api/v1/definitely-not-a-route")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert r["X-Correlation-ID"] == r.json()["error"]["correlation_id"]


# --- m9: every DRF view is role-protected or explicitly public ---------------------------------
PUBLIC_VIEWS = {
    "TokenObtainView",  # login
    "TokenRefreshView",  # refresh
    "SpectacularAPIView",  # /api/schema/ (public for now, gate in U-12)
    "SpectacularSwaggerView",  # /api/docs/
}


def _drf_views():
    found = []

    def walk(patterns):
        for p in patterns:
            if isinstance(p, URLResolver):
                walk(p.url_patterns)
            elif isinstance(p, URLPattern):
                cls = getattr(p.callback, "cls", None)
                if cls is not None and issubclass(cls, APIView):
                    found.append(cls)

    walk(get_resolver("config.urls").url_patterns)
    return found


def test_every_drf_view_uses_role_permission_or_is_explicitly_public():
    views = _drf_views()
    assert views
    for cls in views:
        uses_roles = any(
            isinstance(c, type) and issubclass(c, RolePermission) for c in cls.permission_classes
        )
        assert uses_roles or cls.__name__ in PUBLIC_VIEWS, f"{cls.__name__} is neither"
    assert {c.__name__ for c in views} >= PUBLIC_VIEWS  # the allowlist is not stale


# --- m10: record() takes a SystemPrincipal ----------------------------------------------------
def _principal(tenant):
    from apps.accounts.principals import SystemPrincipal

    api_key_client(tenant)
    from apps.accounts.models import ApiKey

    return SystemPrincipal(ApiKey.objects.get(tenant=tenant))


def test_record_accepts_system_principal_as_actor(tenant_a):
    row = record(_principal(tenant_a), "event.received", ("DisruptionEvent", "EVT-1"))
    assert (row.actor, row.tenant_id) == ("SYSTEM", tenant_a.pk)


def test_record_system_principal_with_explicit_other_tenant_raises(tenant_a, tenant_b):
    with pytest.raises(ValueError):
        record(_principal(tenant_a), "event.received", ("DisruptionEvent", "E1"), tenant=tenant_b)
    assert not AuditEvent.objects.filter(action="event.received").exists()


def test_record_system_principal_with_same_explicit_tenant_ok(tenant_a):
    row = record(_principal(tenant_a), "event.received", ("X", "1"), tenant=tenant_a)
    assert row.tenant_id == tenant_a.pk


# --- m11: system check -------------------------------------------------------------------------
def test_check_warns_when_not_debug_and_num_proxies_unset(monkeypatch, settings):
    settings.DEBUG = False
    monkeypatch.delenv("NUM_PROXIES", raising=False)
    result = num_proxies_check(None)
    assert len(result) == 1 and isinstance(result[0], CheckWarning)
    assert result[0].id == "accounts.W001" and "NUM_PROXIES" in result[0].msg


def test_check_silent_when_set_or_debug(monkeypatch, settings):
    settings.DEBUG = False
    monkeypatch.setenv("NUM_PROXIES", "1")
    assert num_proxies_check(None) == []
    monkeypatch.delenv("NUM_PROXIES")
    settings.DEBUG = True
    assert num_proxies_check(None) == []


def test_test_environment_pins_num_proxies_to_zero():
    assert os.environ["NUM_PROXIES"] == "0"
