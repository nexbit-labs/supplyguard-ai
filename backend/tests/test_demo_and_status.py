"""POST /api/v1/demo/simulate-disruption and GET /api/v1/integrations/status."""
import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.core.models import DisruptionEvent, IdSequence
from tests.boomi_helpers import BASE, SECRET, Recorder, patch_boomi
from tests.factories import make_supplier, make_tenant, make_user
from tests.helpers import api_key_client, jwt_client

pytestmark = pytest.mark.django_db
SIM = "/api/v1/demo/simulate-disruption"
STATUS = "/api/v1/integrations/status"


@pytest.fixture
def acme():
    tenant = make_tenant("TEN-ACME")
    for erp_id in ("SUP-1008", "SUP-1015"):
        make_supplier(tenant, erp_id=erp_id)
    return tenant


@pytest.fixture
def admin(acme):
    return jwt_client(make_user(acme, role="ADMIN"))


def sim(client, **body):
    return client.post(SIM, body, format="json")


# --- simulate ----------------------------------------------------------------------------------
def test_simulate_runs_scenario_for_callers_tenant(admin, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    r = sim(admin, scenario="demo")
    assert r.status_code == 200
    body = r.json()
    assert body["scenario"] == "demo" and body["event_id"] == "EVT-900001"
    (sent,) = body["sent"]
    assert sent["http_status"] == 202 and sent["event_id"] == "EVT-900001"
    assert sent["status"] == "RECEIVED" and sent["risk_case_id"] is None
    e = DisruptionEvent.objects.get(tenant=acme, event_id="EVT-900001")
    assert e.supplier_erp_id == "SUP-1008" and e.publish_status == "PUBLISHED"
    assert rec.calls == 1
    assert json.loads(rec.requests[0].content)["tenant_id"] == "TEN-ACME"


def test_fresh_ids_increment_from_evt_900001(admin, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    ids = [sim(admin, scenario="demo").json()["event_id"] for _ in range(3)]
    assert ids == ["EVT-900001", "EVT-900002", "EVT-900003"]
    assert IdSequence.objects.get(prefix="EVT").last_value == 900003
    # every simulated run is its own flow (the scenario's fixed correlation id is dropped)
    cids = set(DisruptionEvent.objects.values_list("correlation_id", flat=True))
    assert len(cids) == 3


def test_fresh_event_id_false_keeps_scenario_id_and_repeat_is_duplicate(admin, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    first = sim(admin, scenario="demo", fresh_event_id=False).json()
    second = sim(admin, scenario="demo", fresh_event_id=False).json()
    assert first["event_id"] == second["event_id"] == "EVT-000123"
    assert first["sent"][0]["http_status"] == 202
    assert second["sent"][0]["http_status"] == 200
    assert first["sent"][0]["correlation_id"] == second["sent"][0]["correlation_id"]
    assert DisruptionEvent.objects.filter(tenant=acme).count() == 1
    assert rec.calls == 1
    assert IdSequence.objects.get(prefix="EVT").last_value == 900000  # untouched


def test_tc09_send_times_two_second_is_duplicate(admin, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    body = sim(admin, scenario="tc09_duplicate_event", fresh_event_id=False).json()
    assert [s["http_status"] for s in body["sent"]] == [202, 200]
    assert body["sent"][0]["correlation_id"] == body["sent"][1]["correlation_id"]
    assert body["sent"][0]["correlation_id"] == "30ea54a9-5467-4dc8-9b66-de56a55b2332"
    assert DisruptionEvent.objects.filter(tenant=acme).count() == 1
    assert rec.calls == 1


def test_send_times_with_fresh_id_still_one_event(admin, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    body = sim(admin, scenario="tc09_duplicate_event").json()
    assert [s["http_status"] for s in body["sent"]] == [202, 200]
    assert DisruptionEvent.objects.filter(tenant=acme).count() == 1


def test_event_time_override(admin, acme, monkeypatch):
    patch_boomi(monkeypatch, Recorder([202]))
    sim(admin, scenario="demo", event_time="2026-12-29T08:00:00Z")
    e = DisruptionEvent.objects.get(tenant=acme)
    assert e.event_time.isoformat() == "2026-12-29T08:00:00+00:00"


def test_event_time_override_must_be_aware(admin):
    r = sim(admin, scenario="demo", event_time="2026-12-29T08:00:00")
    assert r.status_code == 400 and "event_time" in r.json()["error"]["details"]


def test_unknown_supplier_scenario_is_flagged_not_published(admin, acme, monkeypatch):
    rec = Recorder([202])
    patch_boomi(monkeypatch, rec)
    body = sim(admin, scenario="tc02_unknown_supplier").json()
    assert body["sent"][0]["http_status"] == 202
    assert DisruptionEvent.objects.get().needs_data_correction is True
    assert rec.calls == 0


def test_always_the_callers_tenant_even_for_other_tenant_scenarios(monkeypatch):
    other = make_tenant("TEN-GLOBEX")
    make_supplier(other, erp_id="SUP-1008")
    patch_boomi(monkeypatch, Recorder([202]))
    client = jwt_client(make_user(other, role="ADMIN"))
    # scenario file says TEN-ACME; it must still land in the caller's tenant (no 403)
    r = sim(client, scenario="demo", tenant_id="TEN-ACME")
    assert r.status_code == 200 and r.json()["sent"][0]["http_status"] == 202
    assert DisruptionEvent.objects.get().tenant_id == "TEN-GLOBEX"
    assert not AuditEvent.objects.filter(action="security.tenant_mismatch").exists()


@pytest.mark.parametrize(
    "name",
    ["../etc/passwd", "demo/../x", "..", "DEMO", "demo.json", "a b", "", "demo\\x", "%2e%2e"],
)
def test_scenario_name_whitelist(admin, name):
    r = sim(admin, scenario=name)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert DisruptionEvent.objects.count() == 0


def test_unknown_scenario_is_404_contract_body(admin):
    r = sim(admin, scenario="no_such_thing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def test_scenario_dir_is_respected_and_traversal_by_symlink_stays_out(
    admin, settings, tmp_path
):
    (tmp_path / "mine.json").write_text("{}")  # no "event" key -> not a scenario
    settings.SCENARIO_DIR = Path(tmp_path)
    assert sim(admin, scenario="mine").status_code == 404
    assert sim(admin, scenario="demo").status_code == 404  # not in this folder


def test_missing_scenario_field_is_400(admin):
    assert sim(admin).status_code == 400


def test_simulate_is_admin_only(acme):
    for role in ("PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"):
        assert sim(jwt_client(make_user(acme, role=role)), scenario="demo").status_code == 403
    assert sim(api_key_client(acme), scenario="demo").status_code == 403
    assert DisruptionEvent.objects.count() == 0
    assert IdSequence.objects.get(prefix="EVT").last_value == 900000


# --- integrations status -----------------------------------------------------------------------
def test_status_not_configured(admin, settings):
    settings.BOOMI_BASE_URL = ""
    r = admin.get(STATUS)
    assert r.status_code == 200
    body = r.json()
    assert body["boomi"] == {
        "status": "NOT_CONFIGURED",
        "configured": False,
        "auth_mode": "apikey",
        "host": "",
        "reachable": None,
        "latency_ms": None,
        "checked_at": None,
    }
    assert body["events_24h"] == {
        "RECEIVED": 0, "PUBLISHED": 0, "PUBLISH_FAILED": 0, "needs_data_correction": 0,
    }


def test_status_configured_and_reachable_with_cache(admin, settings, monkeypatch):
    settings.BOOMI_BASE_URL = BASE + "/some/path"
    settings.BOOMI_API_KEY = SECRET
    rec = Recorder([200])
    patch_boomi(monkeypatch, rec, base_url=BASE + "/some/path")
    body = admin.get(STATUS).json()["boomi"]
    assert body["configured"] is True and body["reachable"] is True
    assert body["host"] == "boomi.example.test"  # hostname only
    assert isinstance(body["latency_ms"], int) and body["checked_at"]
    assert body["status"] == "REACHABLE"
    admin.get(STATUS)
    assert rec.calls == 1  # the probe is cached


def test_status_unreachable(admin, settings, monkeypatch):
    settings.BOOMI_BASE_URL = BASE
    patch_boomi(monkeypatch, Recorder([httpx.ConnectError("c")]))
    body = admin.get(STATUS).json()["boomi"]
    assert body["reachable"] is False and body["status"] == "UNREACHABLE"
    assert body["latency_ms"] is None


def test_status_never_contains_secrets_or_paths(admin, settings, monkeypatch):
    settings.BOOMI_BASE_URL = "https://user:pw-in-url@boomi.example.test/secret/path?token=abc"
    settings.BOOMI_API_KEY = SECRET
    settings.BOOMI_AUTH_MODE = "basic"
    patch_boomi(monkeypatch, Recorder([200]), api_key="svc:pw")
    text = admin.get(STATUS).content.decode()
    for needle in (SECRET, "pw-in-url", "secret/path", "token=abc", "svc:pw", "user:"):
        assert needle not in text
    assert json.loads(text)["boomi"]["host"] == "boomi.example.test"
    assert json.loads(text)["boomi"]["auth_mode"] == "basic"


def test_status_counts_are_tenant_scoped_and_24h(admin, acme):
    from tests.factories import make_event

    other = make_tenant("TEN-GLOBEX")
    sup = make_supplier(acme, erp_id="SUP-5")
    make_event(acme, event_id="EVT-1", supplier=sup, publish_status="PUBLISHED")
    make_event(acme, event_id="EVT-2", supplier=sup, publish_status="PUBLISH_FAILED")
    make_event(acme, event_id="EVT-3", supplier=None, needs_data_correction=True)
    old = make_event(acme, event_id="EVT-4", supplier=sup, publish_status="PUBLISHED")
    stale = timezone.now() - timedelta(hours=25)
    DisruptionEvent.objects.filter(pk=old.pk).update(created_at=stale)
    make_event(other, event_id="EVT-1", supplier=None, needs_data_correction=True)
    make_event(other, event_id="EVT-9", supplier=None, publish_status="PUBLISH_FAILED")
    counts = admin.get(STATUS).json()["events_24h"]
    assert counts == {
        "RECEIVED": 1, "PUBLISHED": 1, "PUBLISH_FAILED": 1, "needs_data_correction": 1,
    }


def test_status_is_admin_only(acme):
    for role in ("PROCUREMENT_MANAGER", "OPS_MANAGER", "VIEWER"):
        assert jwt_client(make_user(acme, role=role)).get(STATUS).status_code == 403
    assert api_key_client(acme).get(STATUS).status_code == 403


def test_status_ignores_tenant_query(admin, acme):
    other = make_tenant("TEN-GLOBEX")
    from tests.factories import make_event

    make_event(other, event_id="EVT-9", supplier=None, publish_status="PUBLISH_FAILED")
    counts = admin.get(STATUS + "?tenant_id=TEN-GLOBEX").json()["events_24h"]
    assert counts["PUBLISH_FAILED"] == 0
