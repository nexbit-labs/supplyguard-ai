"""Logging filter that stamps every record with the current correlation id (`-` if none)."""
import logging

from .context import get_correlation_id


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # django.request logs 4xx/5xx AFTER the middleware chain, when the ContextVar is
        # already reset; those records carry the request, so prefer its id.
        request = getattr(record, "request", None)
        record.correlation_id = (
            getattr(request, "correlation_id", None) or get_correlation_id() or "-"
        )
        return True
