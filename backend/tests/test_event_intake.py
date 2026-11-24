"""POST /api/v1/events/disruptions: TC-01, TC-02, TC-09, AC-08, tenant check, race, validation,
Boomi failure handling."""
import json
import uuid

import httpx
import pytest
from django.db import IntegrityError
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.core.models import DisruptionEvent, RiskCase
from apps.integrations import services
from tests.boomi_helpers import SECRET, Recorder, patch_boomi
from tests.factories import make_supplier, make_tenant, make_user
from tests.helpers import api_key_client, jwt_client

pytestmark = pytest.mark.django_db
URL = "/api/v1/events/disruptions"
CID = "2d68e761-19b2-4481-ae35-95fae61044b3"


def payload(**kw):
    body = {
        "event_id": "EVT-000123",
        "supplier_id": "SUP-1008",
        "event_type": "FACTORY_OUTAGE",
        "severity": "HIGH",
        "event_time": "2026-12-02T10:15:00Z",
        "expected_duration_hours": 48,
        "description": "Line 2 fire",
        "source_system": "supplier-portal",
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


def post(client, body, **extra):
    return client.post(URL, body, format="json", **extra)


def audits(action, tenant=None):
    qs = AuditEvent.objects.filter(action=action)
    return qs.filter(tenant=tenant) if tenant else qs


# --- TC-01 -------------------------------------------------------------------------------------
def test_tc01_valid_event_is_stored_and_published_once(key, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    r = post(key, payload(correlation_id=CID))
    assert r.status_code == 202
    assert r.json() == {
        "event_id": "EVT-000123",
        "correlation_id": CID,
        "status": "RECEIVED",
        "risk_case_id": None,
    }
    event = DisruptionEvent.objects.get(tenant=acme, event_id="EVT-000123")
    assert event.publish_status == "PUBLISHED"
    assert event.supplier.erp_id == "SUP-1008" and event.supplier_erp_id == "SUP-1008"
    assert event.needs_data_correction is False
    assert str(event.correlation_id) == CID
    # Boomi called once with the right headers and canonical body (credential's tenant)
    assert rec.calls == 1
    req = rec.requests[0]
    assert req.headers["Idempotency-Key"] == "TEN-ACME:EVT-000123"
    assert req.headers["X-Correlation-ID"] == CID
    assert req.headers["X-Api-Key"] == SECRET
    sent = json.loads(req.content)
    assert sent == {
        "event_id": "EVT-000123",
        "tenant_id": "TEN-ACME",
        "supplier_id": "SUP-1008",
        "event_type": "FACTORY_OUTAGE",
        "severity": "HIGH",
        "event_time": "2026-12-02T10:15:00Z",
        "expected_duration_hours": 48,
        "description": "Line 2 fire",
        "source_system": "supplier-portal",
        "correlation_id": CID,
    }
    row = audits("event.received", acme).get()
    assert row.actor == "SYSTEM" and row.correlation_id == uuid.UUID(CID)
    assert row.entity_type == "DisruptionEvent" and row.entity_id == "EVT-000123"


def test_request_correlation_id_used_when_payload_has_none(key, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    rid = "9c5e6205-49ac-4625-81bb-77268aa269f9"
    r = post(key, payload(), HTTP_X_CORRELATION_ID=rid)
    assert r.status_code == 202
    assert r.json()["correlation_id"] == rid
    assert r["X-Correlation-ID"] == rid


def test_admin_user_may_post_and_is_the_audit_actor(acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    admin = make_user(acme, role="ADMIN")
    r = post(jwt_client(admin), payload())
    assert r.status_code == 202
    assert audits("event.received", acme).get().actor.startswith("USR-")


def test_optional_fields_stored(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    r = post(key, payload(shipment_id="SHP-1", po_id="PO-9", expected_duration_hours=12.5))
    assert r.status_code == 202
    e = DisruptionEvent.objects.get(tenant=acme)
    assert (e.shipment_id, e.po_id, float(e.expected_duration_hours)) == ("SHP-1", "PO-9", 12.5)


# --- TC-02 / AC-08 -----------------------------------------------------------------------------
def test_tc02_unknown_supplier_flagged_no_boomi_no_case(key, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    r = post(key, payload(event_id="EVT-000902", supplier_id="SUP-9999"))
    assert r.status_code == 202
    assert r.json()["risk_case_id"] is None and r.json()["status"] == "RECEIVED"
    e = DisruptionEvent.objects.get(tenant=acme, event_id="EVT-000902")
    assert e.needs_data_correction is True and e.supplier is None
    assert e.supplier_erp_id == "SUP-9999" and e.publish_status == "RECEIVED"
    assert rec.calls == 0
    assert RiskCase.objects.count() == 0
    assert audits("event.flagged_data_correction", acme).count() == 1


def test_ac08_foreign_supplier_looks_identical_to_unknown(acme, monkeypatch):
    other = make_tenant("TEN-GLOBEX")
    make_supplier(other, erp_id="SUP-3001")
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    key = api_key_client(acme)
    foreign = post(key, payload(event_id="EVT-000001", supplier_id="SUP-3001"))
    unknown = post(key, payload(event_id="EVT-000002", supplier_id="SUP-4242"))
    assert foreign.status_code == unknown.status_code == 202
    f, u = foreign.json(), unknown.json()
    assert set(f) == set(u)
    assert (f["status"], f["risk_case_id"]) == (u["status"], u["risk_case_id"])
    e = DisruptionEvent.objects.get(tenant=acme, event_id="EVT-000001")
    assert e.supplier is None and e.needs_data_correction is True
    assert rec.calls == 0
    # nothing about the other tenant leaks into the answer or the audit rows
    assert "GLOBEX" not in json.dumps(f)
    assert not AuditEvent.objects.filter(tenant=other).exists()
    assert DisruptionEvent.objects.filter(tenant=other).count() == 0


# --- TC-09 duplicates --------------------------------------------------------------------------
def test_tc09_duplicate_same_correlation_no_second_event_or_call(key, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    first = post(key, payload(correlation_id=CID))
    second = post(
        key,
        payload(correlation_id=str(uuid.uuid4())),
        HTTP_X_CORRELATION_ID=str(uuid.uuid4()),
    )
    assert first.status_code == 202 and second.status_code == 200
    assert second.json() == first.json()  # stored correlation id, not the new one
    assert DisruptionEvent.objects.filter(tenant=acme).count() == 1
    assert rec.calls == 1
    assert RiskCase.objects.count() == 0
    assert audits("event.duplicate", acme).count() == 1


def test_duplicate_returns_existing_risk_case_id(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    post(key, payload())
    event = DisruptionEvent.objects.get(tenant=acme)
    case = RiskCase.objects.create(
        tenant=acme, event=event, supplier=event.supplier, risk_level="HIGH", risk_score=70,
        exposure_amount=1, currency="INR", correlation_id=event.correlation_id,
    )
    r = post(key, payload())
    assert r.status_code == 200 and r.json()["risk_case_id"] == case.id


def test_same_event_id_from_two_tenants_is_two_independent_events(acme, monkeypatch):
    other = make_tenant("TEN-GLOBEX")
    make_supplier(other, erp_id="SUP-1008")
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    a = post(api_key_client(acme), payload(correlation_id=CID))
    b = post(api_key_client(other), payload())
    assert a.status_code == b.status_code == 202  # not a duplicate
    assert a.json()["correlation_id"] != b.json()["correlation_id"]
    assert DisruptionEvent.objects.filter(event_id="EVT-000123").count() == 2
    assert rec.calls == 2
    assert {json.loads(r.content)["tenant_id"] for r in rec.requests} == {"TEN-ACME", "TEN-GLOBEX"}
    assert audits("event.duplicate").count() == 0


def test_lost_insert_race_answers_as_duplicate(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    first = post(key, payload(correlation_id=CID)).json()
    real = DisruptionEvent.objects

    class Racy:
        """First lookup misses (the other request had not committed yet), create loses."""

        def __init__(self):
            self.lookups = 0

        def filter(self, **kw):
            self.lookups += 1
            if self.lookups == 1:
                return real.none()
            return real.filter(**kw)

        def create(self, **kw):
            raise IntegrityError("uniq_event_tenant_eventid")

    monkeypatch.setattr(services.DisruptionEvent, "objects", Racy())
    r = post(key, payload())
    assert r.status_code == 200 and r.json() == first
    monkeypatch.undo()
    assert DisruptionEvent.objects.filter(tenant=acme).count() == 1


def test_other_integrity_error_is_not_swallowed(key, monkeypatch):
    real = DisruptionEvent.objects

    class Broken:
        def filter(self, **kw):
            return real.none()

        def create(self, **kw):
            raise IntegrityError("something else")

    monkeypatch.setattr(services.DisruptionEvent, "objects", Broken())
    client = APIClient(raise_request_exception=False)
    client.credentials(**{"HTTP_X_API_KEY": key._credentials["HTTP_X_API_KEY"]})
    assert post(client, payload()).status_code == 500


# --- tenant check ------------------------------------------------------------------------------
def test_payload_tenant_mismatch_is_403_with_no_write_and_an_audit_row(key, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    other = make_tenant("TEN-GLOBEX")
    r = post(key, payload(tenant_id="TEN-GLOBEX"))
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "FORBIDDEN" and err["details"] == {}
    assert uuid.UUID(err["correlation_id"])
    assert DisruptionEvent.objects.count() == 0
    assert rec.calls == 0
    row = audits("security.tenant_mismatch").get()
    assert row.tenant_id == acme.id and row.actor == "SYSTEM"
    assert "EVT-000123" not in json.dumps(row.data) and "GLOBEX" not in json.dumps(row.data)
    assert not AuditEvent.objects.filter(tenant=other).exists()


def test_matching_payload_tenant_is_accepted_and_ignored_as_source(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    assert post(key, payload(tenant_id="TEN-ACME")).status_code == 202
    assert DisruptionEvent.objects.get().tenant_id == "TEN-ACME"


def test_tenant_in_query_string_is_ignored(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    make_tenant("TEN-GLOBEX")
    r = key.post(URL + "?tenant_id=TEN-GLOBEX&tenant=TEN-GLOBEX", payload(), format="json")
    assert r.status_code == 202
    assert DisruptionEvent.objects.get().tenant_id == "TEN-ACME"


def test_mismatch_checked_before_dedupe(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    post(key, payload())
    r = post(key, payload(tenant_id="TEN-OTHER"))
    assert r.status_code == 403
    assert audits("event.duplicate").count() == 0


def test_other_roles_and_anonymous_cannot_post(acme):
    for role in ("PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"):
        assert post(jwt_client(make_user(acme, role=role)), payload()).status_code == 403
    assert post(APIClient(), payload()).status_code == 401
    assert DisruptionEvent.objects.count() == 0


# --- validation --------------------------------------------------------------------------------
BAD = [
    ("event_id", "EVT-12x"),
    ("event_id", "evt-000001"),
    ("event_id", "EVT-"),
    ("event_id", "EVT-" + "1" * 80),
    ("event_id", None),
    ("event_type", "METEOR"),
    ("severity", "SEVERE"),
    ("severity", "high"),
    ("event_time", "2026-12-02T10:15:00"),  # naive
    ("event_time", "2026-12-02"),
    ("event_time", "yesterday"),
    ("event_time", 1764670500),
    ("expected_duration_hours", -1),
    ("expected_duration_hours", "abc"),
    ("expected_duration_hours", 10**7),
    ("correlation_id", "not-a-uuid"),
    ("supplier_id", ""),
    ("supplier_id", "S" * 41),
    ("source_system", "s" * 101),
    ("description", "d" * 4001),
    ("shipment_id", "S" * 41),
    ("po_id", "P" * 41),
]


@pytest.mark.parametrize(("field", "value"), BAD)
def test_validation_matrix(key, field, value):
    r = post(key, payload(**{field: value}))
    assert r.status_code == 400, (field, value, r.content)
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert field in r.json()["error"]["details"]
    assert DisruptionEvent.objects.count() == 0


@pytest.mark.parametrize(
    "field", ["event_id", "supplier_id", "event_type", "severity", "event_time"]
)
def test_required_fields(key, field):
    body = payload()
    del body[field]
    r = post(key, body)
    assert r.status_code == 400 and field in r.json()["error"]["details"]


def test_non_object_body_is_400(key):
    assert post(key, [payload()]).status_code == 400


def test_unknown_keys_are_ignored(key, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    r = post(key, payload(tenant="TEN-X", is_admin=True, whatever={"a": 1}))
    assert r.status_code == 202


def test_offset_timestamp_is_stored_as_the_same_instant(key, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    post(key, payload(event_time="2026-12-02T15:45:00+05:30"))
    e = DisruptionEvent.objects.get()
    assert e.event_time.isoformat() == "2026-12-02T10:15:00+00:00"


# --- Boomi failure / not configured ------------------------------------------------------------
def test_boomi_down_event_kept_publish_failed_still_202(key, acme, monkeypatch):
    rec = Recorder([httpx.ReadTimeout("t"), 503, 503])
    sleeps = patch_boomi(monkeypatch, rec, max_retries=2)
    r = post(key, payload())
    assert r.status_code == 202 and r.json()["status"] == "RECEIVED"
    assert rec.calls == 3 and len(sleeps) == 2
    e = DisruptionEvent.objects.get(tenant=acme)
    assert e.publish_status == "PUBLISH_FAILED"
    row = audits("event.publish_failed", acme).get()
    assert row.data == {"kind": "TRANSIENT", "status": 503, "attempts": 3}
    assert SECRET not in json.dumps(row.data)


def test_boomi_4xx_is_not_retried_and_marks_failed(key, acme, monkeypatch):
    rec = Recorder([401])
    patch_boomi(monkeypatch, rec)
    assert post(key, payload()).status_code == 202
    assert rec.calls == 1
    assert audits("event.publish_failed", acme).get().data["kind"] == "AUTH"


def test_boomi_not_configured_keeps_received_and_audits_skip(key, acme, settings):
    settings.BOOMI_BASE_URL = ""
    r = post(key, payload())
    assert r.status_code == 202
    assert DisruptionEvent.objects.get().publish_status == "RECEIVED"
    assert audits("event.publish_skipped", acme).count() == 1
    assert audits("event.publish_failed").count() == 0


def test_no_db_transaction_is_open_while_calling_boomi(key, monkeypatch):
    from django.db import connection

    seen = []

    def handler(request):
        # pytest-django wraps the test in one atomic block; an extra open atomic() would
        # show up as a savepoint.
        seen.append(list(connection.savepoint_ids))
        return httpx.Response(202)

    patch_boomi(monkeypatch, handler)
    post(key, payload())
    assert seen == [[]]


def test_secret_never_in_response_or_audit_rows(key, monkeypatch):
    patch_boomi(monkeypatch, Recorder([503]))
    r = post(key, payload())
    assert SECRET not in r.content.decode()
    for row in AuditEvent.objects.all():
        assert SECRET not in json.dumps(row.data)
