"""Role-based access (docs/CONTRACTS.md s2 and s4).

The whole matrix lives in `ROLE_MATRIX` below, nowhere else. A view declares
`permission_key = "<key>"` (or a mapping `{"GET": "<key>", "PATCH": "<key>"}` when one URL needs
different keys per method) and uses `RolePermission`.
"""
from dataclasses import dataclass

from rest_framework.permissions import BasePermission

from apps.tenants.models import Tenant

from .principals import SystemPrincipal

ADMIN = "ADMIN"
PROCUREMENT_MANAGER = "PROCUREMENT_MANAGER"
OPS_MANAGER = "OPS_MANAGER"
VIEWER = "VIEWER"
SYSTEM = "SYSTEM"

USER_ROLES = frozenset({ADMIN, PROCUREMENT_MANAGER, OPS_MANAGER, VIEWER})
ALL_ROLES = USER_ROLES | {SYSTEM}


@dataclass(frozen=True)
class Rule:
    """`methods`: HTTP method -> roles allowed. `routed=False` = declared here for another
    task's endpoint that does not exist yet (the matrix tests skip it until it is routed)."""

    methods: dict
    routed: bool = True


def _rule(methods, routed=True):
    return Rule({m: frozenset(r) for m, r in methods.items()}, routed)


_READERS = (ADMIN, PROCUREMENT_MANAGER, OPS_MANAGER, VIEWER)

ROLE_MATRIX: dict[str, Rule] = {
    # --- routed by U-04 ---
    "me.read": _rule({"GET": _READERS}),
    "settings.read": _rule({"GET": (ADMIN,)}),
    "settings.write": _rule({"PATCH": (ADMIN,)}),
    "users.read": _rule({"GET": (ADMIN,)}),
    "users.write": _rule({"POST": (ADMIN,)}),
    # ADMIN + PROCUREMENT_MANAGER is the agenda D-2.6 SUGGESTION and is NOT yet approved by
    # the team; change it here (and only here) when the decision is made.
    "audit.read": _rule({"GET": (ADMIN, PROCUREMENT_MANAGER)}),
    # --- declared for other tasks, not routed yet ---
    "suppliers.read": _rule({"GET": _READERS}, routed=False),
    "dashboard.read": _rule({"GET": _READERS}, routed=False),
    "risk-cases.read": _rule({"GET": _READERS}, routed=False),
    "what-if": _rule({"POST": (ADMIN, PROCUREMENT_MANAGER, OPS_MANAGER)}, routed=False),
    "recommendations.decision": _rule({"POST": (PROCUREMENT_MANAGER,)}, routed=False),
    "executions.retry": _rule({"POST": (PROCUREMENT_MANAGER,)}, routed=False),
    "events.create": _rule({"POST": (SYSTEM, ADMIN)}, routed=False),
    "demo.simulate": _rule({"POST": (ADMIN,)}, routed=False),
    "internal.risk-cases": _rule({"POST": (SYSTEM,)}, routed=False),
    "internal.analysis": _rule({"POST": (SYSTEM,)}, routed=False),
    "internal.execution-result": _rule({"POST": (SYSTEM,)}, routed=False),
    "integrations.status": _rule({"GET": (ADMIN,)}, routed=False),
}


NO_TENANT_MESSAGE = "No active tenant for this credential."
ROLE_DENIED_MESSAGE = "Your role is not permitted for this endpoint."
SYSTEM_DENIED_MESSAGE = "This credential is not permitted for this endpoint."


def _has_active_tenant(user) -> bool:
    tenant = getattr(user, "tenant", None)
    return tenant is not None and tenant.status == Tenant.Status.ACTIVE


def _effective_method(method: str) -> str:
    return "GET" if method in ("HEAD", "OPTIONS") else method


def _keys_of(view) -> dict | None:
    """The view's `permission_key` as {method: key}; a plain string covers every method."""
    key = getattr(view, "permission_key", None)
    if key is None:
        return None
    return key if isinstance(key, dict) else {"*": key}


class RolePermission(BasePermission):
    """Allow when the matrix lists the caller's role for the view's key and method.

    Requires an authenticated principal whose tenant is ACTIVE (tenant-less superusers and
    users of an INACTIVE tenant are refused for every role). SYSTEM (API key) callers pass only
    where the matrix lists SYSTEM. A view without `permission_key`, a key that is not in the
    matrix, or a method the matrix does not list is refused (fail closed) - except a method the
    view does not implement at all, which is let through for any role the key knows so DRF
    answers 405 as usual.
    """

    message = ROLE_DENIED_MESSAGE

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False  # DRF turns this into 401 (the first authenticator has a header)
        if not _has_active_tenant(user):
            self.message = NO_TENANT_MESSAGE
            return False
        self.message = (
            SYSTEM_DENIED_MESSAGE if isinstance(user, SystemPrincipal) else ROLE_DENIED_MESSAGE
        )
        return self._role_allowed(request, view, user)

    @staticmethod
    def _role_allowed(request, view, user) -> bool:
        keys = _keys_of(view)
        if keys is None:
            return False
        method = _effective_method(request.method)
        key = keys.get(method, keys.get("*"))
        rule = ROLE_MATRIX.get(key) if key else None
        if rule is not None:
            roles = rule.methods.get(method)
            if roles is not None:
                return user.role in roles
        if hasattr(view, method.lower()):
            return False
        known = set()
        for k in keys.values():
            if k in ROLE_MATRIX:
                for roles in ROLE_MATRIX[k].methods.values():
                    known |= roles
        return user.role in known
