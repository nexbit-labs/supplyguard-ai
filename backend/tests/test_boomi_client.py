import base64
import logging

import httpx
import pytest

from apps.integrations import boomi
from apps.integrations.boomi import BoomiClient, BoomiError, BoomiNotConfigured
from tests.boomi_helpers import BASE, SECRET, Recorder, make_client

EVENT = {"event_id": "EVT-000001", "tenant_id": "TEN-A", "description": "BODY-MARKER-XYZ"}
CID = "2d68e761-19b2-4481-ae35-95fae61044b3"


def test_success_sends_headers_and_body():
    rec = Recorder([202])
    assert make_client(rec).publish_event(EVENT, CID) == 202
    (req,) = rec.requests
    assert req.method == "POST"
    assert str(req.url) == BASE + "/ws/simple/createDisruptionEvent"
    assert req.headers["X-Correlation-ID"] == CID
    assert req.headers["Idempotency-Key"] == "TEN-A:EVT-000001"
    assert req.headers["X-Api-Key"] == SECRET
    assert "authorization" not in req.headers
    assert b"EVT-000001" in req.content


def test_basic_auth_mode_uses_authorization_not_api_key_header():
    rec = Recorder([200])
    make_client(rec, api_key="svc:p@ss", auth_mode="basic").publish_event(EVENT, CID)
    req = rec.requests[0]
    expected = "Basic " + base64.b64encode(b"svc:p@ss").decode()
    assert req.headers["Authorization"] == expected
    assert "x-api-key" not in req.headers


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_transient_status_retried_up_to_cap_then_transient_error(status):
    rec, sleeps = Recorder([status]), []
    client = make_client(rec, sleeps, max_retries=2, backoff_seconds=0.5)
    with pytest.raises(BoomiError) as err:
        client.publish_event(EVENT, CID)
    assert rec.calls == 3  # first try + 2 retries
    assert err.value.kind == boomi.TRANSIENT
    assert err.value.status == status
    assert err.value.attempts == 3
    assert sleeps == [0.5, 1.0]  # exponential backoff


@pytest.mark.parametrize(
    "exc",
    [httpx.ReadTimeout("t"), httpx.ConnectTimeout("t"), httpx.ConnectError("c")],
)
def test_timeouts_and_connection_errors_are_retried(exc):
    rec = Recorder([exc])
    with pytest.raises(BoomiError) as err:
        make_client(rec, max_retries=2).publish_event(EVENT, CID)
    assert rec.calls == 3
    assert err.value.kind == boomi.TRANSIENT and err.value.status is None


def test_success_after_one_retry():
    rec = Recorder([503, 202])
    assert make_client(rec).publish_event(EVENT, CID) == 202
    assert rec.calls == 2


def test_max_retries_zero_means_single_try():
    rec = Recorder([503])
    with pytest.raises(BoomiError) as err:
        make_client(rec, max_retries=0).publish_event(EVENT, CID)
    assert rec.calls == 1 and err.value.attempts == 1


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, boomi.VALIDATION),
        (401, boomi.AUTH),
        (403, boomi.AUTH),
        (404, boomi.VALIDATION),
        (422, boomi.VALIDATION),
    ],
)
def test_client_errors_are_never_retried(status, kind):
    rec, sleeps = Recorder([status]), []
    with pytest.raises(BoomiError) as err:
        make_client(rec, sleeps).publish_event(EVENT, CID)
    assert rec.calls == 1
    assert sleeps == []
    assert err.value.kind == kind and err.value.status == status and err.value.attempts == 1


@pytest.mark.parametrize("status", [500, 501, 505])
def test_other_5xx_is_downstream_and_not_retried(status):
    rec = Recorder([status])
    with pytest.raises(BoomiError) as err:
        make_client(rec).publish_event(EVENT, CID)
    assert rec.calls == 1 and err.value.kind == boomi.DOWNSTREAM


def test_redirect_is_not_followed():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    client = BoomiClient(
        base_url=BASE, api_key=SECRET, transport=httpx.MockTransport(handler), sleep=lambda s: None
    )
    with pytest.raises(BoomiError) as err:
        client.publish_event(EVENT, CID)
    assert err.value.kind == boomi.DOWNSTREAM
    assert len(seen) == 1  # exactly one request in total
    assert all(r.url.host != "evil.example" for r in seen)  # the key never left


def test_not_configured():
    with pytest.raises(BoomiNotConfigured):
        BoomiClient(base_url="")
    with pytest.raises(BoomiNotConfigured):
        BoomiClient(base_url=BASE, auth_mode="basic", api_key="no-colon")
    with pytest.raises(BoomiNotConfigured):
        BoomiClient(base_url=BASE, auth_mode="weird")


def test_factory_reads_settings(settings):
    settings.BOOMI_BASE_URL = ""
    with pytest.raises(BoomiNotConfigured):
        boomi.get_boomi_client()
    settings.BOOMI_BASE_URL = BASE + "/"
    settings.BOOMI_API_KEY = SECRET
    client = boomi.get_boomi_client()
    assert client.base_url == BASE and client.host == "boomi.example.test"


def test_secrets_and_body_never_in_logs_or_exception(caplog):
    caplog.set_level(logging.DEBUG)
    for script in ([503], [401], [httpx.ReadTimeout("t")]):
        rec = Recorder(script)
        with pytest.raises(BoomiError) as err:
            make_client(rec, api_key=SECRET).publish_event(EVENT, CID)
        text = str(err.value) + repr(err.value) + repr(err.value.args)
        assert SECRET not in text and "BODY-MARKER-XYZ" not in text
    assert caplog.records  # something was logged
    assert SECRET not in caplog.text
    assert "BODY-MARKER-XYZ" not in caplog.text
    assert "authorization" not in caplog.text.lower()
    assert CID in caplog.text  # correlation id is logged


def test_basic_credentials_never_in_logs(caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder([503])
    with pytest.raises(BoomiError):
        make_client(rec, api_key="svc:hunter2-pass", auth_mode="basic").publish_event(EVENT, CID)
    b64 = base64.b64encode(b"svc:hunter2-pass").decode()
    assert "hunter2-pass" not in caplog.text and b64 not in caplog.text


def test_health_reachable_and_unreachable():
    ok = make_client(Recorder([404])).health()  # any HTTP answer = reachable
    assert ok["reachable"] is True and isinstance(ok["latency_ms"], int)
    down = make_client(Recorder([httpx.ConnectError("c")])).health()
    assert down == {"reachable": False, "latency_ms": None}


def test_health_does_not_send_credentials():
    rec = Recorder([200])
    make_client(rec).health()
    assert "x-api-key" not in rec.requests[0].headers
    assert "authorization" not in rec.requests[0].headers
