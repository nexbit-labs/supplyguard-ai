"""Workflow errors (WORKFLOW_DESIGN.md s2.4). All are DRF APIExceptions, so a view can let them
propagate and the contract handler renders the CONTRACTS s1 error body; `details` reaches
`error.details` through apps.core.exceptions."""
from rest_framework import status
from rest_framework.exceptions import APIException


class WorkflowError(APIException):
    reason = "WORKFLOW_ERROR"

    def __init__(self, message=None, *, reason=None, **details):
        super().__init__(detail=message or self.default_detail)
        self.contract_details = {"reason": reason or self.reason, **details}

    @property
    def details(self):
        return self.contract_details


class CaseNotFound(WorkflowError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"
    default_detail = "Not found."
    reason = "NOT_FOUND"


class InvalidTransition(WorkflowError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"
    reason = "INVALID_TRANSITION"

    def __init__(self, frm, to):
        super().__init__(f"Transition {frm} -> {to} is not allowed.", **{"from": frm, "to": to})


class PreconditionFailed(WorkflowError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "business_rule"
    reason = "PRECONDITION_FAILED"

    def __init__(self, frm, to, message):
        super().__init__(message, **{"from": frm, "to": to})


class TransitionNotPermitted(WorkflowError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "forbidden"
    reason = "ACTOR_NOT_PERMITTED"

    def __init__(self, frm, to, actor_role):
        super().__init__(
            f"{actor_role} may not move a case {frm} -> {to}.",
            **{"from": frm, "to": to, "actor_role": actor_role},
        )


class DecisionConflict(WorkflowError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"

    def __init__(self, reason, message=None, **details):
        super().__init__(
            message or reason.replace("_", " ").capitalize() + ".", reason=reason, **details
        )


class DecisionNotPermitted(WorkflowError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "forbidden"
    reason = "ACTOR_NOT_PERMITTED"

    def __init__(self, role):
        super().__init__(f"{role} may not decide on a recommendation.", actor_role=role)


class DecisionRejected(WorkflowError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "business_rule"

    def __init__(self, reason, message=None, **details):
        super().__init__(
            message or reason.replace("_", " ").capitalize() + ".", reason=reason, **details
        )
