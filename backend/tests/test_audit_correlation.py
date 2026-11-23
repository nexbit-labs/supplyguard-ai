import io
import logging
import uuid

import pytest
from django.conf import settings
from django.test import Client, override_settings

from apps.audit.context import get_correlation_id
from apps.audit.models import AuditEvent

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("_urlconf"),
]


@pytest.fixture
def _urlconf():
    with override_settings(ROOT_URLCONF="tests.urls_correlation"):
        yield


@pytest.fixture
def client():
    return Client(raise_request_exception=False)


def test_middleware_is_first():
    assert settings.MIDDLEWARE[0] == "apps.audit.middleware.CorrelationIdMiddleware"
    assert settings.MIDDLEWARE.index("corsheaders.middleware.CorsMiddleware") == 1


def test_valid_uuid_echoed_and_normalised(client):
    upper = "3F2504E0-4F89-11D3-9A0C-0305E82C3301"  # a v1 UUID, uppercase
    response = client.get("/api/ok", HTTP_X_CORRELATION_ID=upper)
    assert response["X-Correlation-ID"] == upper.lower()
    assert response.json()["cid"] == upper.lower()


def test_hex_without_hyphens_is_normalised(client):
    raw = uuid.uuid4().hex
    response = client.get("/api/ok", HTTP_X_CORRELATION_ID=raw)
    assert response["X-Correlation-ID"] == str(uuid.UUID(raw))


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "<script>alert(1)</script>", "1" * 200])
def test_malformed_replaced_never_echoed(client, bad):
    response = client.get("/api/ok", HTTP_X_CORRELATION_ID=bad)
    value = response["X-Correlation-ID"]
    assert value != bad
    assert uuid.UUID(value).version == 4


def test_nil_uuid_is_rejected(client):
    nil = "00000000-0000-0000-0000-000000000000"
    value = client.get("/api/ok", HTTP_X_CORRELATION_ID=nil)["X-Correlation-ID"]
    assert value != nil
    assert uuid.UUID(value).version == 4


def test_missing_header_generates_distinct_v4_per_request(client):
    first = client.get("/api/ok")["X-Correlation-ID"]
    second = client.get("/api/ok")["X-Correlation-ID"]
    assert uuid.UUID(first).version == 4
    assert first != second


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("get", "/api/ok", 200),
        ("get", "/api/protected", 401),
        ("get", "/api/missing", 404),
        ("post", "/api/get-only", 405),
        ("get", "/api/boom", 500),
    ],
)
def test_header_on_every_response(client, method, path, status):
    cid = str(uuid.uuid4())
    response = getattr(client, method)(path, HTTP_X_CORRELATION_ID=cid)
    assert response.status_code == status
    assert response["X-Correlation-ID"] == cid


def test_context_cleared_after_request(client):
    assert get_correlation_id() is None
    client.get("/api/ok")
    assert get_correlation_id() is None
    client.get("/api/boom")
    assert get_correlation_id() is None


def test_audit_row_correlation_id_equals_response_header(client):
    cid = str(uuid.uuid4())
    response = client.get("/api/record", HTTP_X_CORRELATION_ID=cid)
    assert response.status_code == 200
    row = AuditEvent.objects.get(pk=response.json()["audit_id"])
    assert str(row.correlation_id) == response["X-Correlation-ID"] == cid


def test_log_records_carry_correlation_id(client):
    # Use the real console handler from settings.LOGGING, swapping only its stream.
    handler = logging.getLogger().handlers[0]
    buffer = io.StringIO()
    old = handler.setStream(buffer)
    try:
        cid = str(uuid.uuid4())
        client.get("/api/log", HTTP_X_CORRELATION_ID=cid)
        logging.getLogger("tests.correlation").warning("outside request")
    finally:
        handler.setStream(old)
    lines = buffer.getvalue().splitlines()
    assert any(f"[{cid}]" in line and "hello from view" in line for line in lines)
    assert any("[-]" in line and "outside request" in line for line in lines)


def _console_output(run):
    handler = logging.getLogger().handlers[0]
    buffer = io.StringIO()
    old = handler.setStream(buffer)
    try:
        run()
    finally:
        handler.setStream(old)
    return buffer.getvalue()


@pytest.mark.parametrize("path", ["/api/protected", "/api/missing"])
def test_django_request_log_lines_carry_id_after_context_reset(client, path):
    # django.request logs 4xx after the middleware chain, when the ContextVar is reset.
    cid = str(uuid.uuid4())
    out = _console_output(lambda: client.get(path, HTTP_X_CORRELATION_ID=cid))
    lines = [ln for ln in out.splitlines() if "django.request" in ln]
    assert lines, out
    assert all(f"[{cid}]" in ln for ln in lines), lines


def test_django_request_500_logged_once_with_id(client):
    cid = str(uuid.uuid4())
    out = _console_output(lambda: client.get("/api/boom", HTTP_X_CORRELATION_ID=cid))
    assert out.count("Traceback") == 1
    assert f"[{cid}]" in out
