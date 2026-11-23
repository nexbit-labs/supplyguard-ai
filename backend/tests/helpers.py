"""Shared helpers for the tenancy / RBAC tests (real JWT and API-key authentication, so
`request.tenant` is set exactly as in production; `force_authenticate` would skip that)."""
import re
from io import StringIO

from django.core.management import call_command
from rest_framework.test import APIClient

from apps.accounts.serializers import TokenObtainSerializer


def set_throttle_rates(monkeypatch, **rates):
    """Override login/refresh throttle rates for one test (the suite default is 'off')."""
    from rest_framework.throttling import ScopedRateThrottle

    from apps.accounts.throttles import LoginUsernameThrottle

    merged = {"auth": "100000/min", "auth_username": "100000/min", "auth_refresh": "100000/min"}
    merged.update(rates)
    for cls in (ScopedRateThrottle, LoginUsernameThrottle):
        monkeypatch.setattr(cls, "THROTTLE_RATES", merged)


def jwt_client(user) -> APIClient:
    client = APIClient()
    token = str(TokenObtainSerializer.get_token(user).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def api_key_client(tenant, label="boomi") -> APIClient:
    out = StringIO()
    call_command("create_api_key", tenant=tenant.id, label=label, stdout=out)
    raw = re.search(r"sgk_[A-Za-z0-9_-]{40}", out.getvalue()).group(0)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw)
    return client


def strip_cid(response) -> dict:
    """Response JSON without the per-request correlation id (everything else must match)."""
    body = response.json()
    body["error"].pop("correlation_id", None)
    return body
