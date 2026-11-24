"""Boomi client hardening (U-06 review): total time budget, URL secrets, httpx logging."""
import copy
import io
import logging
import logging.config

import httpx
import pytest

from apps.integrations import boomi
from apps.integrations.boomi import BoomiClient, BoomiError, BoomiNotConfigured
from tests.boomi_helpers import BASE, SECRET, Recorder, make_client

EVENT = {"event_id": "EVT-000001", "tenant_id": "TEN-A", "description": "BODY-MARKER-XYZ"}
CID = "2d68e761-19b2-4481-ae35-95fae61044b3"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def hanging_client(clock, **kw):
    """Every request hangs until its read timeout (the clock advances by that timeout)."""
    seen = []

    def handler(request):
        timeout = request.extensions["timeout"]["read"]
        seen.append(timeout)
        clock.now += timeout
        raise httpx.ReadTimeout("hang")

    client = BoomiClient(
        base_url=BASE,
        api_key=SECRET,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        clock=clock,
        **kw,
    )
    return client, seen


# --- M3: total budget ---------------------------------------------------------------------
def test_defaults_are_short(settings):
    assert settings.BOOMI_TIMEOUT_READ == 5
    assert settings.BOOMI_MAX_RETRIES == 1
    assert settings.BOOMI_TOTAL_TIMEOUT_SECONDS == 8


def test_hanging_boomi_never_exceeds_the_total_budget():
    clock = FakeClock()
    client, seen = hanging_client(
        clock, timeout_read=5, max_retries=5, backoff_seconds=0.5, total_timeout=8
    )
    with pytest.raises(BoomiError) as err:
        client.publish_event(EVENT, CID)
    assert clock.now <= 8.0 + 1e-9
    assert err.value.kind == boomi.TRANSIENT and err.value.status is None
    assert err.value.attempts == len(seen) >= 2
    assert seen[0] == 5 and seen[1] < 5  # the second attempt is capped to what is left


def test_without_a_budget_the_same_hang_would_take_far_longer():
    clock = FakeClock()
    client, _ = hanging_client(
        clock, timeout_read=5, max_retries=5, backoff_seconds=0.5, total_timeout=10_000
    )
    with pytest.raises(BoomiError):
        client.publish_event(EVENT, CID)
    assert clock.now > 8.0


def test_budget_stops_retries_of_503_too():
    clock = FakeClock()
    calls = []

    def handler(request):
        calls.append(1)
        clock.now += 3.0
        return httpx.Response(503)

    client = BoomiClient(
        base_url=BASE,
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        clock=clock,
        max_retries=10,
        backoff_seconds=1.0,
        total_timeout=8,
    )
    with pytest.raises(BoomiError) as err:
        client.publish_event(EVENT, CID)
    assert clock.now <= 8.0 + 1e-9
    assert err.value.kind == boomi.TRANSIENT and err.value.status == 503
    assert err.value.attempts == len(calls)


def test_4xx_still_never_retries_with_a_budget():
    rec, sleeps = Recorder([422]), []
    with pytest.raises(BoomiError) as err:
        make_client(rec, sleeps, total_timeout=8).publish_event(EVENT, CID)
    assert rec.calls == 1 and sleeps == [] and err.value.kind == boomi.VALIDATION


# --- M4: secrets in URLs ------------------------------------------------------------------
@pytest.mark.parametrize(
    "base",
    [
        "https://svc:topsecret@boomi.example.test",
        "https://@boomi.example.test",
        "https://boomi.example.test?token=topsecret",
        "https://boomi.example.test/#topsecret",
    ],
)
def test_base_url_with_userinfo_query_or_fragment_is_refused(base):
    with pytest.raises(BoomiNotConfigured) as err:
        BoomiClient(base_url=base)
    assert "topsecret" not in str(err.value) and "boomi.example.test" not in str(err.value)


@pytest.mark.parametrize(
    "path", ["/x?key=topsecret", "/x#topsecret", "//svc:topsecret@evil.example/x"]
)
def test_event_path_with_userinfo_query_or_fragment_is_refused(path):
    with pytest.raises(BoomiNotConfigured) as err:
        BoomiClient(base_url=BASE, event_path=path)
    assert "topsecret" not in str(err.value)


def test_factory_refuses_an_unsafe_setting(settings):
    settings.BOOMI_BASE_URL = "https://svc:topsecret@boomi.example.test"
    with pytest.raises(BoomiNotConfigured):
        boomi.get_boomi_client()


def test_a_normal_base_url_is_accepted():
    assert BoomiClient(base_url=BASE + "/", event_path="/ws/simple/x").host == "boomi.example.test"


# --- M4: httpx logging --------------------------------------------------------------------
def test_real_logging_config_keeps_urls_and_secrets_out_of_the_output(settings):
    stream = io.StringIO()
    config = copy.deepcopy(settings.LOGGING)
    config["handlers"]["console"]["stream"] = stream
    root, httpx_log, httpcore_log = (logging.getLogger(n) for n in ("", "httpx", "httpcore"))
    saved = [(lg, lg.level, list(lg.handlers)) for lg in (root, httpx_log, httpcore_log)]
    try:
        logging.config.dictConfig(config)
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
        rec = Recorder([202])
        make_client(rec).publish_event(EVENT, CID)
        output = stream.getvalue()
    finally:
        for lg, level, handlers in saved:
            lg.setLevel(level)
            lg.handlers[:] = handlers
    assert "boomi response" in output  # our own INFO line is still there (host only)
    for forbidden in ("https://", "createDisruptionEvent", SECRET, "BODY-MARKER-XYZ"):
        assert forbidden not in output
