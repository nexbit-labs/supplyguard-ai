def record(*args, **kwargs):
    """Write an audit row. Lazy wrapper so importing `apps.audit` never needs the app registry.

    See `apps.audit.service.record` for the arguments.
    """
    from .service import record as _record

    return _record(*args, **kwargs)
