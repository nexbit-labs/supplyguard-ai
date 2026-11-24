"""Test doubles for the Boomi client: a MockTransport recorder and a patch helper."""
import httpx

from apps.integrations import boomi

SECRET = "sgk-test-boomi-secret-value-123"
BASE = "https://boomi.example.test"


class Recorder:
    """Records requests; `script` is a list of status codes / exceptions served in order
    (the last one repeats)."""

    def __init__(self, script=(202,)):
        self.script = list(script)
        self.requests = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        item = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(item, Exception):
            raise item
        return httpx.Response(item, json={"ok": item < 300})

    @property
    def calls(self):
        return len(self.requests)


def make_client(recorder, sleeps=None, **kw):
    kw.setdefault("base_url", BASE)
    kw.setdefault("api_key", SECRET)
    sleeps = sleeps if sleeps is not None else []
    return boomi.BoomiClient(
        transport=httpx.MockTransport(recorder), sleep=sleeps.append, **kw
    )


def patch_boomi(monkeypatch, recorder, **kw):
    sleeps = []
    client = make_client(recorder, sleeps, **kw)
    monkeypatch.setattr(boomi, "get_boomi_client", lambda *a, **k: client)
    return sleeps
