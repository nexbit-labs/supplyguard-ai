"""Outbound client for Boomi (Event Streams / listener process behind API Management).

Rules (docs/CONTRACTS.md s1, CLAUDE.md s6):
- Only transient failures are retried (timeouts, connection errors, HTTP 429/502/503/504), at
  most `BOOMI_MAX_RETRIES` times with exponential backoff. Other 4xx are never retried.
- The whole publish (all attempts and backoffs) is bounded by `total_timeout` seconds
  (`BOOMI_TOTAL_TIMEOUT_SECONDS`): it runs inside the intake request, so it must stay short.
- BOOMI_BASE_URL / BOOMI_EVENT_PATH must not contain credentials, a query string or a fragment
  (they would end up in URLs and logs).
- Errors are classed like `Execution.error_class`: VALIDATION / AUTH / BUSINESS / TRANSIENT /
  DOWNSTREAM.
- The API key, the Authorization header and the request/response bodies are never logged and
  never put in an exception message. Logged: method, host, status, attempt, correlation id.
"""
import logging
import time
from urllib.parse import urlsplit

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

VALIDATION = "VALIDATION"
AUTH = "AUTH"
BUSINESS = "BUSINESS"
TRANSIENT = "TRANSIENT"
DOWNSTREAM = "DOWNSTREAM"

RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


class BoomiNotConfigured(Exception):
    """BOOMI_BASE_URL is empty (or the auth settings are unusable): nothing to call."""


class BoomiError(Exception):
    """A failed Boomi call. `kind` is one of the class names above; no payload or secret inside."""

    def __init__(self, kind, status=None, attempts=1):
        self.kind = kind
        self.status = status
        self.attempts = attempts
        super().__init__(f"Boomi call failed: kind={kind} status={status} attempts={attempts}")


def _classify_status(status: int) -> str:
    if status in RETRYABLE_STATUSES:
        return TRANSIENT
    if status in (401, 403):
        return AUTH
    if 400 <= status < 500:
        return VALIDATION
    return DOWNSTREAM


def host_of(url: str) -> str:
    """Hostname only (no scheme, port, path, query or credentials)."""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _reject_unsafe_url(value, name, is_path=False):
    """No userinfo, query string or fragment in the configured URL/path. The message never
    contains the value."""
    value = value or ""
    try:
        parts = urlsplit(value)
        bad = bool(parts.query or parts.fragment or "?" in value or "#" in value)
        if is_path:
            bad = bad or bool(parts.netloc)
        else:
            bad = bad or parts.username is not None or parts.password is not None
            bad = bad or "@" in parts.netloc
    except ValueError:
        bad = True
    if bad:
        raise BoomiNotConfigured(
            f"{name} must not contain credentials, a query string or a fragment."
        )


class BoomiClient:
    def __init__(
        self,
        base_url,
        api_key="",
        auth_mode="apikey",
        event_path="/ws/simple/createDisruptionEvent",
        timeout_connect=3.0,
        timeout_read=5.0,
        max_retries=1,
        backoff_seconds=0.5,
        total_timeout=8.0,
        transport=None,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        if not base_url:
            raise BoomiNotConfigured("BOOMI_BASE_URL is not set.")
        if auth_mode not in ("apikey", "basic"):
            raise BoomiNotConfigured("BOOMI_AUTH_MODE must be 'apikey' or 'basic'.")
        _reject_unsafe_url(base_url, "BOOMI_BASE_URL")
        _reject_unsafe_url(event_path, "BOOMI_EVENT_PATH", is_path=True)
        self._auth = None
        if auth_mode == "basic":
            user, sep, password = (api_key or "").partition(":")
            if not sep or not user:
                raise BoomiNotConfigured("BOOMI_API_KEY must be 'user:password' in basic mode.")
            self._auth = httpx.BasicAuth(user, password)
        self.base_url = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.host = host_of(self.base_url)
        self._api_key = api_key or ""
        self._path = "/" + event_path.lstrip("/")
        self._timeout_read = float(timeout_read)
        self._timeout_connect = float(timeout_connect)
        self._total_timeout = float(total_timeout)
        self._clock = clock
        self._max_retries = max(0, int(max_retries))
        self._backoff = float(backoff_seconds)
        self._transport = transport
        self._sleep = sleep

    def _attempt_timeout(self, remaining: float) -> httpx.Timeout:
        """Every phase of one attempt is capped to what is left of the total budget."""
        read = min(self._timeout_read, remaining)
        connect = min(self._timeout_connect, remaining)
        return httpx.Timeout(read, connect=connect, write=read, pool=connect)

    def _client(self, timeout):
        # follow_redirects stays off: a redirect must not carry the key to another host.
        return httpx.Client(timeout=timeout, transport=self._transport, follow_redirects=False)

    def _headers(self, correlation_id, idempotency_key=None):
        headers = {"X-Correlation-ID": str(correlation_id), "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if self.auth_mode == "apikey" and self._api_key:
            headers["X-Api-Key"] = self._api_key
        return headers

    def publish_event(self, event_payload: dict, correlation_id: str) -> int:
        """POST the canonical event. Returns the HTTP status on success, raises BoomiError."""
        idempotency_key = f"{event_payload.get('tenant_id')}:{event_payload.get('event_id')}"
        url = self.base_url + self._path
        headers = self._headers(correlation_id, idempotency_key)
        attempts = 0
        last_kind, last_status = TRANSIENT, None
        deadline = self._clock() + self._total_timeout
        while True:
            remaining = deadline - self._clock()
            if remaining <= 0:  # budget used up: stop, never start another attempt
                raise BoomiError(TRANSIENT, last_status, attempts)
            attempts += 1
            status = None
            try:
                with self._client(self._attempt_timeout(remaining)) as client:
                    response = client.post(
                        url, json=event_payload, headers=headers, auth=self._auth
                    )
                status = response.status_code
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                # The exception text can contain the URL only; log the class name.
                logger.warning(
                    "boomi request failed method=POST host=%s error=%s attempt=%s "
                    "correlation_id=%s",
                    self.host, type(exc).__name__, attempts, correlation_id,
                )
                last_kind, last_status = TRANSIENT, None
            except httpx.HTTPError as exc:
                logger.warning(
                    "boomi request failed method=POST host=%s error=%s attempt=%s "
                    "correlation_id=%s",
                    self.host, type(exc).__name__, attempts, correlation_id,
                )
                raise BoomiError(DOWNSTREAM, None, attempts) from None
            else:
                logger.info(
                    "boomi response method=POST host=%s status=%s attempt=%s "
                    "correlation_id=%s",
                    self.host, status, attempts, correlation_id,
                )
                if 200 <= status < 300:
                    return status
                last_kind, last_status = _classify_status(status), status
                if status not in RETRYABLE_STATUSES:
                    raise BoomiError(last_kind, status, attempts)
            if attempts > self._max_retries:
                raise BoomiError(last_kind, last_status, attempts)
            delay = self._backoff * (2 ** (attempts - 1))
            if deadline - self._clock() <= delay:  # no budget left for the wait + another try
                raise BoomiError(last_kind, last_status, attempts)
            self._sleep(delay)

    def health(self) -> dict:
        """Short reachability probe: any HTTP answer counts as reachable (no auth is sent)."""
        timeout = httpx.Timeout(3.0, connect=2.0)
        started = time.monotonic()
        try:
            with self._client(timeout) as client:
                client.get(self.base_url)
            reachable = True
        except httpx.HTTPError:
            reachable = False
        latency = round((time.monotonic() - started) * 1000)
        return {"reachable": reachable, "latency_ms": latency if reachable else None}


def get_boomi_client(transport=None, sleep=None) -> BoomiClient:
    """Build the client from settings. Tests override this function (or pass a MockTransport).

    Raises BoomiNotConfigured when BOOMI_BASE_URL is empty.
    """
    kwargs = {}
    if sleep is not None:
        kwargs["sleep"] = sleep
    return BoomiClient(
        base_url=settings.BOOMI_BASE_URL,
        api_key=settings.BOOMI_API_KEY,
        auth_mode=settings.BOOMI_AUTH_MODE,
        event_path=settings.BOOMI_EVENT_PATH,
        timeout_connect=settings.BOOMI_TIMEOUT_CONNECT,
        timeout_read=settings.BOOMI_TIMEOUT_READ,
        max_retries=settings.BOOMI_MAX_RETRIES,
        backoff_seconds=settings.BOOMI_BACKOFF_SECONDS,
        total_timeout=settings.BOOMI_TOTAL_TIMEOUT_SECONDS,
        transport=transport,
        **kwargs,
    )
