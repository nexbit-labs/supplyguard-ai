"""`record()`: the single way application code writes to the audit log (docs/CONTRACTS.md s10)."""
import json
import re
import uuid

from django.core.serializers.json import DjangoJSONEncoder

from apps.accounts.ids import format_user_id
from apps.accounts.models import User
from apps.accounts.principals import SystemPrincipal
from apps.tenants.models import Tenant

from .context import get_correlation_id
from .models import AuditEvent

REDACTED = "[REDACTED]"
# Matched against the key with every non-alphanumeric character removed and lowercased,
# so `X-Api-Key`, `api_key` and `apiKey` all become `apikey`.
_SENSITIVE_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "credential",
    "apikey",
    "privatekey",
    "jwt",
    "bearer",
    "signature",
    "session",
)
_SENSITIVE_EXACT = {"access", "refresh", "key"}
_AUTH_PREFIXES = ("bearer ", "basic ")


def _is_sensitive(key) -> bool:
    name = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return name in _SENSITIVE_EXACT or any(part in name for part in _SENSITIVE_PARTS)


def redact(value):
    """Return a copy with sensitive values replaced by `[REDACTED]`.

    Redaction is by key name (see `_is_sensitive`) plus any string starting with 'Bearer ' or
    'Basic '. Numbers, booleans and null under a sensitive key stay visible (`prompt_tokens: 12`).
    Callers must still never put raw credentials in values under innocent keys.
    """
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _is_sensitive(k) and not (v is None or isinstance(v, (bool, int, float))):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str) and value.lower().startswith(_AUTH_PREFIXES):
        return REDACTED
    return value


def _json_safe(data) -> dict:
    """Round-trip through Django's JSON encoder (Decimal/datetime/date/UUID -> str)."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TypeError("audit data must be a dict or None")
    try:
        return json.loads(json.dumps(data, cls=DjangoJSONEncoder, allow_nan=False))
    except RecursionError as exc:
        raise TypeError("audit data is nested too deeply or circular") from exc
    except ValueError as exc:  # NaN / Infinity / circular reference
        raise TypeError(f"audit data is not JSON-safe: {exc}") from exc


def _check_length(name: str, value: str) -> None:
    limit = AuditEvent._meta.get_field(name).max_length
    if len(value) > limit:
        raise ValueError(f"{name} is longer than {limit} characters")


def _resolve_tenant(explicit, entity_tenant, actor_tenant):
    """explicit, else entity's, else actor's; the ones that are set must all agree."""
    candidates = [t for t in (explicit, entity_tenant, actor_tenant) if t is not None]
    if len({t.pk for t in candidates}) > 1:
        raise ValueError("tenant mismatch between explicit tenant, entity and actor")
    return candidates[0] if candidates else None


def _actor_text(actor):
    if isinstance(actor, SystemPrincipal):  # an API-key caller: `record(request.user, ...)`
        return "SYSTEM", actor.tenant
    if isinstance(actor, User):
        if actor.pk is None:
            raise TypeError("actor User must be saved")
        return format_user_id(actor.pk), actor.tenant
    if isinstance(actor, str):
        if not actor.strip():
            raise TypeError("actor must not be empty")
        return actor, None
    raise TypeError(f"actor must be a User or str, not {type(actor).__name__}")


def _entity_parts(entity, entity_type, entity_id):
    tenant = None
    if entity is None:
        pass
    elif isinstance(entity, tuple):
        if len(entity) != 2:
            raise TypeError("entity tuple must be (type, id)")
        entity_type, entity_id = entity
    elif hasattr(entity, "_meta"):
        entity_type = entity.__class__.__name__
        erp_id = getattr(entity, "erp_id", None)
        entity_id = erp_id if erp_id else entity.pk
        if isinstance(entity, Tenant):
            tenant = entity
        else:
            tenant = getattr(entity, "tenant", None)
    else:
        raise TypeError("entity must be a model instance, a (type, id) tuple or None")
    return (
        str(entity_type) if entity_type else "",
        "" if entity_id is None else str(entity_id),
        tenant,
    )


def _correlation(correlation_id):
    if correlation_id is None:
        correlation_id = get_correlation_id()
    if correlation_id is None:
        return None
    try:
        return uuid.UUID(str(correlation_id))
    except ValueError as exc:
        raise ValueError(f"invalid correlation_id: {correlation_id!r}") from exc


def record(
    actor,
    action,
    entity=None,
    correlation_id=None,
    data=None,
    tenant=None,
    entity_type=None,
    entity_id=None,
) -> AuditEvent:
    """Append one audit row and return it.

    tenant: explicit, else the entity's tenant, else the actor's tenant, else None. If more than
    one is set and they differ, ValueError (a cross-tenant record is never written).
    correlation_id: explicit, else the current request's, else None.
    data: JSON-safe dict; values under sensitive key names (and 'Bearer'/'Basic' strings) are
    redacted, the caller's object is never mutated. Never put raw credentials in other values.
    """
    actor_text, actor_tenant = _actor_text(actor)
    etype, eid, entity_tenant = _entity_parts(entity, entity_type, entity_id)
    cid = _correlation(correlation_id)
    try:
        payload = redact(_json_safe(data))
    except RecursionError as exc:
        raise TypeError("audit data is nested too deeply") from exc
    for name, value in (
        ("actor", actor_text),
        ("action", action),
        ("entity_type", etype),
        ("entity_id", eid),
    ):
        _check_length(name, str(value))
    return AuditEvent.objects.create(
        tenant=_resolve_tenant(tenant, entity_tenant, actor_tenant),
        actor=actor_text,
        action=action,
        entity_type=etype,
        entity_id=eid,
        correlation_id=cid,
        data=payload,
    )
