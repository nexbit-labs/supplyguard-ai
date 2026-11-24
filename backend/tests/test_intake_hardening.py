"""Review fixes on the event side: time range, deep JSON, duplicate re-publish, throttle,
simulation ids (U-06 review m7-m11)."""
import httpx
import pytest
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.core.models import DisruptionEvent, IdSequence
from apps.integrations.throttles import CredentialScopedThrottle
from tests.boomi_helpers import Recorder, patch_boomi
from tests.factories import make_supplier, make_tenant, make_user
from tests.helpers import api_key_client, jwt_client

pytestmark = pytest.mark.django_db
URL = "/api/v1/events/disruptions"
SIM = "/api/v1/demo/simulate-disruption"


def payload(**kw):
    body = {
        "event_id": "EVT-000123",
        "supplier_id": "SUP-1008",
        "event_type": "FACTORY_OUTAGE",
        "severity": "HIGH",
        "event_time": "2026-12-02T10:15:00Z",
        "expected_duration_hours": 48,
    }
    body.update(kw)
    return body


@pytest.fixture
def acme():
    tenant = make_tenant("TEN-ACME")
    make_supplier(tenant, erp_id="SUP-1008")
    return tenant


@pytest.fixture
def key(acme):
    return api_key_client(acme)


@pytest.fixture
def admin(acme):
    return jwt_client(make_user(acme, role="ADMIN"))


def post(client, body):
    return client.post(URL, body, format="json")


# --- m7 ----------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "when",
    [
        "0001-01-01T00:00:00+14:00",
        "9999-12-31T23:59:59-14:00",
        "1999-12-31T23:59:59Z",
        "2101-01-01T00:00:00Z",
    ],
)
def test_event_time_outside_2000_2100_is_a_400(key, when):
    response = post(key, payload(event_time=when))
    assert response.status_code == 400, response.content
    assert "event_time" in response.json()["error"]["details"]
    assert DisruptionEvent.objects.count() == 0


def test_event_time_in_range_is_accepted(key):
    assert post(key, payload(event_time="2000-01-01T00:00:00Z")).status_code == 202


# --- m8 ----------------------------------------------------------------------------------------
DEEP = "[" * 200_000


@pytest.mark.parametrize("path", [URL, "/api/v1/internal/risk-cases", "/api/v1/auth/token"])
def test_deeply_nested_json_is_a_400_not_a_500(key, path):
    client = APIClient() if path.endswith("token") else key
    response = client.post(path, DEEP, content_type="application/json")
    assert response.status_code == 400, response.status_code
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# --- m9 ----------------------------------------------------------------------------------------
def test_duplicate_of_a_failed_publish_is_published_again(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([503]), max_retries=0)
    first = post(key, payload())
    assert first.status_code == 202
    event = DisruptionEvent.objects.get()
    assert event.publish_status == "PUBLISH_FAILED"

    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    again = post(key, payload())
    assert again.status_code == 200
    assert again.json()["correlation_id"] == first.json()["correlation_id"]
    assert again.json()["status"] == "RECEIVED" and again.json()["risk_case_id"] is None
    assert rec.calls == 1
    assert rec.requests[0].headers["Idempotency-Key"] == "TEN-ACME:EVT-000123"
    event.refresh_from_db()
    assert event.publish_status == "PUBLISHED"


def test_duplicate_of_a_never_published_event_is_published(key, acme, settings, monkeypatch):
    settings.BOOMI_BASE_URL = ""  # not configured: the first call only stores it
    assert post(key, payload()).status_code == 202
    event = DisruptionEvent.objects.get()
    assert event.publish_status == "RECEIVED"
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    assert post(key, payload()).status_code == 200
    assert rec.calls == 1
    event.refresh_from_db()
    assert event.publish_status == "PUBLISHED"


def test_duplicate_of_a_published_event_does_not_call_boomi(key, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    post(key, payload())
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    assert post(key, payload()).status_code == 200
    assert rec.calls == 0


def test_duplicate_of_an_unknown_supplier_event_never_publishes(key, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    for _ in range(3):
        post(key, payload(event_id="EVT-000777", supplier_id="SUP-NOPE"))
    assert rec.calls == 0
    assert DisruptionEvent.objects.get().publish_status == "RECEIVED"


def test_duplicate_that_fails_again_stays_failed_and_still_answers_200(key, monkeypatch):
    patch_boomi(monkeypatch, Recorder([503]), max_retries=0)
    post(key, payload())
    patch_boomi(monkeypatch, Recorder([httpx.ConnectError("x")]), max_retries=0)
    assert post(key, payload()).status_code == 200
    assert DisruptionEvent.objects.get().publish_status == "PUBLISH_FAILED"
    assert AuditEvent.objects.filter(action="event.publish_failed").count() == 2


# --- m10 ---------------------------------------------------------------------------------------
@pytest.fixture
def low_rates(monkeypatch):
    monkeypatch.setattr(
        CredentialScopedThrottle, "THROTTLE_RATES", {"events": "2/min", "simulate": "1/min"}
    )


def test_event_intake_is_throttled_per_credential(key, acme, low_rates):
    other = api_key_client(acme, label="second")
    assert post(key, payload()).status_code == 202
    assert post(key, payload()).status_code == 200  # duplicate, audited
    blocked = post(key, payload())
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"
    # duplicates are audited, but only up to the throttle
    assert AuditEvent.objects.filter(action="event.duplicate").count() == 1
    # another credential of the same tenant has its own bucket
    assert post(other, payload(event_id="EVT-000124")).status_code == 202


def test_simulate_is_throttled_per_user(admin, acme, low_rates):
    other = jwt_client(make_user(acme, role="ADMIN"))
    assert admin.post(SIM, {"scenario": "demo"}, format="json").status_code == 200
    assert admin.post(SIM, {"scenario": "demo"}, format="json").status_code == 429
    assert other.post(SIM, {"scenario": "demo"}, format="json").status_code == 200


def test_pytest_env_keeps_the_suite_unthrottled(settings):
    rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
    assert rates["events"] == "100000/min" and rates["simulate"] == "100000/min"


# --- m11 ---------------------------------------------------------------------------------------
def test_simulation_recreates_a_missing_evt_sequence(admin, acme):
    IdSequence.objects.filter(prefix="EVT").delete()
    response = admin.post(SIM, {"scenario": "demo"}, format="json")
    assert response.status_code == 200
    assert response.json()["event_id"] == "EVT-900001"


def test_simulation_skips_ids_the_tenant_already_has(admin, acme):
    from tests.factories import make_event

    make_event(acme, event_id="EVT-900001")
    make_event(acme, event_id="EVT-900002")
    response = admin.post(SIM, {"scenario": "demo"}, format="json")
    assert response.json()["event_id"] == "EVT-900003"
