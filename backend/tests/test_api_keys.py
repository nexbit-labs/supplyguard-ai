import re
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.apikeys import hash_key
from apps.accounts.authentication import ApiKeyAuthentication
from apps.accounts.models import ApiKey
from apps.accounts.serializers import TokenObtainSerializer
from apps.audit.models import AuditEvent
from tests.factories import make_user

pytestmark = pytest.mark.django_db
WHOAMI = "/test/whoami"


@pytest.fixture(autouse=True)
def _urlconf():
    with override_settings(ROOT_URLCONF="tests.urls_auth"):
        yield


def create_key(tenant="TEN-A", label="boomi"):
    out = StringIO()
    call_command("create_api_key", tenant=tenant, label=label, stdout=out)
    raw = re.search(r"sgk_[A-Za-z0-9_-]{40}", out.getvalue()).group(0)
    return raw, out.getvalue()


@pytest.fixture
def key(tenant_a):
    return create_key()[0]


def get(raw, **extra):
    return APIClient().get(WHOAMI, HTTP_X_API_KEY=raw, **extra)


def test_valid_key_authenticates_as_system_with_key_tenant(key):
    r = get(key)
    assert r.status_code == 200
    assert r.json() == {"tenant": "TEN-A", "role": "SYSTEM", "user": "SYSTEM",
                        "auth_is_api_key": True}


def test_key_format_and_command_output(tenant_a):
    raw, text = create_key()
    assert re.fullmatch(r"sgk_[A-Za-z0-9_-]{40}", raw)
    assert "shown ONCE" in text and "Boomi environment extension" in text
    assert text.count(raw) == 1


def test_key_stored_only_as_hash_and_prefix(key):
    row = ApiKey.objects.get()
    assert row.key_hash == hash_key(key) and row.prefix == key[:8]
    with connection.cursor() as cur:
        cur.execute("SELECT * FROM accounts_apikey")
        values = [str(v) for rec in cur.fetchall() for v in rec]
    assert not any(key in v for v in values)
    dump = "".join(
        str(v) for ev in AuditEvent.objects.values("actor", "action", "entity_id", "data")
        for v in ev.values()
    )
    assert key not in dump


def test_same_key_never_retrievable_again(key):
    other, text = create_key()
    assert other != key and key not in text
    assert ApiKey.objects.count() == 2


def test_command_refuses_unknown_or_inactive_tenant(tenant_a, tenant_b):
    with pytest.raises(CommandError, match="does not exist"):
        call_command("create_api_key", tenant="TEN-NOPE", label="x")
    tenant_b.status = "INACTIVE"
    tenant_b.save()
    with pytest.raises(CommandError, match="not ACTIVE"):
        call_command("create_api_key", tenant="TEN-B", label="x")
    assert ApiKey.objects.count() == 0


def test_unknown_key_401_with_audit_row_without_key(tenant_a):
    bogus = "sgk_" + "A" * 40
    r = get(bogus)
    assert r.status_code == 401
    row = AuditEvent.objects.get(action="security.api_key_rejected")
    assert row.actor == "SYSTEM" and row.tenant is None
    assert row.data == {"prefix": "sgk_AAAA"}
    assert bogus not in str(AuditEvent.objects.values_list("data", "entity_id", "actor"))


def test_malformed_key_audited_without_any_part_of_it(tenant_a):
    r = get("short-secret-value")
    assert r.status_code == 401
    row = AuditEvent.objects.get(action="security.api_key_rejected")
    assert row.data == {} and "short-secret" not in str(row.entity_id)


def test_inactive_key_rejected(key):
    ApiKey.objects.update(is_active=False)
    assert get(key).status_code == 401
    assert AuditEvent.objects.filter(action="security.api_key_rejected").count() == 1


def test_key_of_inactive_tenant_rejected(key, tenant_a):
    tenant_a.status = "INACTIVE"
    tenant_a.save()
    assert get(key).status_code == 401
    assert AuditEvent.objects.filter(action="security.api_key_rejected").count() == 1


def test_audit_failure_does_not_change_rejection(tenant_a, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("apps.audit.service.record", boom)
    assert get("sgk_" + "B" * 40).status_code == 401


def test_last_used_at_updates_at_most_once_a_minute(key):
    assert ApiKey.objects.get().last_used_at is None
    get(key)
    first = ApiKey.objects.get().last_used_at
    assert first is not None
    get(key)
    assert ApiKey.objects.get().last_used_at == first
    ApiKey.objects.update(last_used_at=timezone.now() - timedelta(minutes=2))
    stale = ApiKey.objects.get().last_used_at
    get(key)
    assert ApiKey.objects.get().last_used_at > stale


def test_invalid_bearer_plus_valid_key_still_fails(key):
    r = get(key, HTTP_AUTHORIZATION="Bearer not.a.jwt")
    assert r.status_code == 401


def test_stale_bearer_plus_valid_key_still_fails(key, tenant_a):
    u = make_user(tenant_a, role="ADMIN")
    token = str(TokenObtainSerializer.get_token(u).access_token)
    u.role = "VIEWER"
    u.save()
    assert get(key, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401


def test_valid_bearer_wins_over_key(key, tenant_a):
    u = make_user(tenant_a, role="ADMIN")
    token = str(TokenObtainSerializer.get_token(u).access_token)
    r = get(key, HTTP_AUTHORIZATION=f"Bearer {token}")
    assert r.json()["role"] == "ADMIN"


def test_system_principal_cannot_use_me(key):
    assert get(key).status_code == 200
    r = APIClient().get("/api/v1/me", HTTP_X_API_KEY=key)
    assert r.status_code == 403


def test_authenticator_without_header_defers(tenant_a):
    assert APIClient().get(WHOAMI).status_code == 401
    assert ApiKeyAuthentication().authenticate(type("R", (), {"META": {}})()) is None


def test_admin_api_key_read_only(tenant_a):
    from django.contrib import admin as dj_admin

    model_admin = dj_admin.site._registry[ApiKey]
    assert model_admin.has_add_permission(None) is False
    assert "is_active" not in model_admin.readonly_fields
    assert {"key_hash", "prefix", "label", "tenant"} <= set(model_admin.readonly_fields)
