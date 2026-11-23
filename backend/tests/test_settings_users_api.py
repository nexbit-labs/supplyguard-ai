"""GET/PATCH /settings and GET/POST /users (ADMIN, own tenant only)."""
import copy
import json
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.audit.models import AuditEvent
from tests.factories import make_tenant, make_user
from tests.helpers import jwt_client

pytestmark = pytest.mark.django_db
SETTINGS, USERS = "/api/v1/settings", "/api/v1/users"
STORED = {
    "risk_thresholds": {"medium": 40, "high": 65, "critical": 85},
    "max_cost_increase_pct": 15,
    "require_qualified_alternates": True,
}
PASSWORD = "Tr1cky-Horse-Battery-9"


@pytest.fixture
def acme():
    return make_tenant("TEN-ACME", settings=copy.deepcopy(STORED))


@pytest.fixture
def globex():
    return make_tenant("TEN-GLOBEX", settings=copy.deepcopy(STORED))


@pytest.fixture
def admin(acme):
    return make_user(acme, role="ADMIN", username="acme.admin")


@pytest.fixture
def api(admin):
    return jwt_client(admin)


def new_user(**kw):
    body = {
        "username": "new.person",
        "email": "n@x.io",
        "name": "New Person",
        "role": "VIEWER",
        "password": PASSWORD,
    }
    body.update(kw)
    return body


# --- settings ---------------------------------------------------------------------------------
EFFECTIVE = copy.deepcopy(STORED)
EFFECTIVE["risk_thresholds"]["exposure_full_scale"] = 500000


def test_get_returns_effective_settings_with_default_exposure_not_stored(api, acme):
    assert api.get(SETTINGS).json() == EFFECTIVE
    acme.refresh_from_db()
    assert "exposure_full_scale" not in acme.settings["risk_thresholds"]  # default is not saved


def test_get_shows_stored_exposure_when_set(api, acme):
    acme.settings["risk_thresholds"]["exposure_full_scale"] = 123
    acme.save()
    assert api.get(SETTINGS).json()["risk_thresholds"]["exposure_full_scale"] == 123


def test_get_empty_settings_is_empty_object(api, acme):
    acme.settings = {}
    acme.save()
    assert api.get(SETTINGS).json() == {}


def test_patch_partial_merge_and_audit(api, acme, admin):
    r = api.patch(
        SETTINGS,
        {
            "risk_thresholds": {"high": 70, "exposure_full_scale": 100000},
            "max_cost_increase_pct": 20,
        },
        format="json",
    )
    assert r.status_code == 200
    expected = copy.deepcopy(STORED)
    expected["risk_thresholds"].update(high=70, exposure_full_scale=100000)
    expected["max_cost_increase_pct"] = 20
    assert r.json() == expected
    acme.refresh_from_db()
    assert acme.settings == expected

    r2 = api.patch(SETTINGS, {"max_cost_increase_pct": 1}, format="json")
    assert r2.json()["risk_thresholds"]["exposure_full_scale"] == 100000  # kept
    row = AuditEvent.objects.filter(action="settings.updated").order_by("id").first()
    assert (row.tenant_id, row.actor, row.entity_type, row.entity_id) == (
        "TEN-ACME", f"USR-{admin.pk}", "Tenant", "TEN-ACME"
    )
    assert row.data["changed"]["max_cost_increase_pct"] == {"old": 15, "new": 20}
    assert "risk_thresholds" in row.data["changed"]
    assert "require_qualified_alternates" not in row.data["changed"]


@pytest.mark.parametrize(
    "body",
    [
        {"risk_thresholds": {"high": 90}},  # >= stored critical
        {"risk_thresholds": {"exposure_full_scale": 0}},
        {"risk_thresholds": {"exposure_full_scale": True}},
        {"max_cost_increase_pct": -1},
        {"max_cost_increase_pct": True},
        {"require_qualified_alternates": "yes"},
        {"unknown": 1},
        [1, 2],
    ],
)
def test_patch_invalid_400_and_nothing_changes(api, acme, body):
    r = api.patch(SETTINGS, body, format="json")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    acme.refresh_from_db()
    assert acme.settings == STORED
    assert not AuditEvent.objects.filter(action="settings.updated").exists()


@pytest.mark.parametrize("key", ["tenant", "tenant_id"])
def test_patch_tenant_in_body_rejected_and_other_tenant_untouched(api, globex, key):
    r = api.patch(SETTINGS, {key: "TEN-GLOBEX", "max_cost_increase_pct": 99}, format="json")
    assert r.status_code == 400
    globex.refresh_from_db()
    assert globex.settings == STORED


@pytest.mark.parametrize("key", ["tenant", "tenant_id"])
def test_tenant_in_query_ignored_for_get_and_patch(api, acme, globex, key):
    globex.settings = {"max_cost_increase_pct": 1}
    globex.save()
    assert api.get(f"{SETTINGS}?{key}=TEN-GLOBEX").json() == EFFECTIVE
    r = api.patch(f"{SETTINGS}?{key}=TEN-GLOBEX", {"max_cost_increase_pct": 77}, format="json")
    assert r.status_code == 200
    globex.refresh_from_db()
    acme.refresh_from_db()
    assert globex.settings == {"max_cost_increase_pct": 1}
    assert acme.settings["max_cost_increase_pct"] == 77


def test_patch_runs_atomically_under_a_row_lock(api, monkeypatch):
    """The concurrency guard is the row lock inside one transaction: assert the code path."""
    from django.db.models import QuerySet

    calls = []
    real_sfu = QuerySet.select_for_update

    def spy(self, *a, **k):
        calls.append(("select_for_update", transaction.get_connection().in_atomic_block))
        return real_sfu(self, *a, **k)

    monkeypatch.setattr(QuerySet, "select_for_update", spy)
    with mock.patch("apps.accounts.views.transaction.atomic", wraps=transaction.atomic) as atomic:
        assert api.patch(SETTINGS, {"max_cost_increase_pct": 5}, format="json").status_code == 200
    assert atomic.called
    assert calls == [("select_for_update", True)]


def test_settings_audit_failure_rolls_the_update_back(api, acme, monkeypatch):
    monkeypatch.setattr(
        "apps.accounts.views.record", mock.Mock(side_effect=RuntimeError("audit down"))
    )
    with pytest.raises(RuntimeError):
        api.patch(SETTINGS, {"max_cost_increase_pct": 5}, format="json")
    acme.refresh_from_db()
    assert acme.settings == STORED


def test_settings_other_methods_405(api):
    for method in ("post", "put", "delete"):
        r = getattr(api, method)(SETTINGS, {}, format="json")
        assert r.status_code == 405, method


# --- users ------------------------------------------------------------------------------------
def test_list_only_own_tenant_and_fields(api, admin, acme, globex):
    other = make_user(acme, role="VIEWER", username="v1")
    make_user(globex, role="ADMIN", username="g.admin")
    body = api.get(USERS).json()
    assert [u["id"] for u in body["results"]] == [f"USR-{admin.pk}", f"USR-{other.pk}"]
    assert set(body["results"][0]) == {"id", "username", "email", "name", "role", "is_active"}
    assert body["count"] == 2


@pytest.mark.parametrize("key", ["tenant", "tenant_id"])
def test_list_ignores_tenant_query(api, admin, globex, key):
    make_user(globex, username="g.one")
    r = api.get(f"{USERS}?{key}=TEN-GLOBEX").json()
    assert [u["username"] for u in r["results"]] == [admin.username]


def test_create_user_in_admins_tenant_and_audit(api, acme):
    r = api.post(USERS, new_user(), format="json")
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "new.person" and body["role"] == "VIEWER"
    assert body["id"].startswith("USR-") and body["is_active"] is True
    assert "password" not in body and PASSWORD not in r.content.decode()
    user = get_user_model().objects.get(username="new.person")
    assert user.tenant_id == "TEN-ACME"
    assert user.check_password(PASSWORD) and user.password != PASSWORD
    assert (user.is_staff, user.is_superuser) == (False, False)
    row = AuditEvent.objects.get(action="user.created")
    assert row.tenant_id == "TEN-ACME"
    assert (row.entity_type, row.entity_id) == ("User", str(user.pk))
    assert row.data == {"username": "new.person", "role": "VIEWER"}
    everything = json.dumps(list(AuditEvent.objects.values("actor", "action", "data")))
    assert PASSWORD not in everything
    # the list response never carries the password either
    assert PASSWORD not in api.get(USERS).content.decode()


@pytest.mark.parametrize("key", ["tenant", "tenant_id"])
def test_create_ignores_tenant_in_body_and_query(api, globex, key):
    r = api.post(f"{USERS}?{key}=TEN-GLOBEX", new_user(**{key: "TEN-GLOBEX"}), format="json")
    assert r.status_code == 201
    assert get_user_model().objects.get(username="new.person").tenant_id == "TEN-ACME"
    assert not get_user_model().objects.filter(tenant=globex, username="new.person").exists()


@pytest.mark.parametrize("role", ["SYSTEM", "SUPERUSER", "admin", "", None])
def test_role_must_be_one_of_the_four(api, role):
    r = api.post(USERS, new_user(role=role), format="json")
    assert r.status_code == 400
    assert not get_user_model().objects.filter(username="new.person").exists()


@pytest.mark.parametrize("role", ["ADMIN", "PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"])
def test_each_user_role_can_be_created(api, role):
    assert api.post(USERS, new_user(role=role), format="json").status_code == 201


def test_staff_and_superuser_flags_ignored(api):
    r = api.post(USERS, new_user(is_staff=True, is_superuser=True, is_active=False), format="json")
    assert r.status_code == 201
    user = get_user_model().objects.get(username="new.person")
    assert (user.is_staff, user.is_superuser, user.is_active) == (False, False, True)


@pytest.mark.parametrize("password", ["short1!", "12345678901", "password", "Aa1!"])
def test_weak_or_short_password_rejected(api, password):
    r = api.post(USERS, new_user(password=password), format="json")
    assert r.status_code == 400
    assert "password" in r.json()["error"]["details"]
    assert not get_user_model().objects.filter(username="new.person").exists()
    assert not AuditEvent.objects.filter(action="user.created").exists()


def test_password_required(api):
    body = new_user()
    del body["password"]
    assert api.post(USERS, body, format="json").status_code == 400


def test_duplicate_username_400_generic_even_across_tenants(api, globex):
    make_user(globex, username="taken.elsewhere")
    first = api.post(USERS, new_user(username="dup"), format="json")
    again = api.post(USERS, new_user(username="dup"), format="json")
    foreign = api.post(USERS, new_user(username="taken.elsewhere"), format="json")
    assert first.status_code == 201
    assert again.status_code == foreign.status_code == 400
    assert again.json()["error"]["details"] == foreign.json()["error"]["details"]
    assert "TEN-" not in again.content.decode()


def test_concurrent_duplicate_create_is_400_not_500(api):
    # validation passes both times (patched), so the DB unique constraint is what stops it
    with mock.patch(
        "apps.accounts.serializers.UserSerializer.validate_username", lambda self, v: v
    ):
        api.post(USERS, new_user(username="race"), format="json")
        r = api.post(USERS, new_user(username="race"), format="json")
    assert r.status_code == 400


def test_users_no_update_or_delete_routes(api, admin):
    for method in ("put", "patch", "delete"):
        assert getattr(api, method)(USERS, {}, format="json").status_code == 405
        assert getattr(api, method)(f"{USERS}/USR-{admin.pk}", {}, format="json").status_code == 404
