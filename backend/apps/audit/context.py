"""Per-request correlation id, held in a ContextVar so it is isolated per thread / async task."""
from contextvars import ContextVar, Token

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str | None) -> Token:
    """Set the id and return the token to pass to `reset_correlation_id`."""
    return _correlation_id.set(value)


def reset_correlation_id(token: Token) -> None:
    _correlation_id.reset(token)
