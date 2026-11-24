"""N-07 persistence errors. They are `WorkflowError`s, so the contract handler renders the
CONTRACTS s1 body and `error.details.reason` carries the code."""
from rest_framework import status

from apps.workflow.exceptions import WorkflowError


class AnalysisConflict(WorkflowError):
    """409: the case is not waiting for this output (wrong state, stale round, duplicate)."""

    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"
    reason = "ANALYSIS_CONFLICT"

    def __init__(self, reason, message, **details):
        super().__init__(message, reason=reason, **details)


class AnalysisInvalid(WorkflowError):
    """422: the agent output failed validation. The AgentRun was still stored."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "business_rule"
    reason = "AGENT_OUTPUT_INVALID"

    def __init__(self, result, limit=50):
        errors = result.validation_errors
        super().__init__(
            "The agent output failed validation and was not applied.",
            agent_run_id=result.agent_run.pk,
            valid=False,
            errors=errors[:limit],
            error_count=len(errors),
            errors_truncated=len(errors) > limit,
            case_status=result.status,
        )


class AnalysisApplyFailed(WorkflowError):
    """422: the output validated but could not be stored (data error). The AgentRun was kept."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "business_rule"
    reason = "AGENT_OUTPUT_NOT_APPLIED"

    def __init__(self, agent_run_id, exc):
        super().__init__(
            "The agent output could not be stored and was not applied.",
            agent_run_id=agent_run_id,
            valid=True,
            error=type(exc).__name__,
        )


class PayloadTooLarge(WorkflowError):
    """413: the request body is over the endpoint limit (nothing is stored)."""

    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    default_code = "payload_too_large"
    reason = "PAYLOAD_TOO_LARGE"


class InvalidJSON(WorkflowError):
    """400: the body is not parseable JSON (or is nested absurdly deep). Nothing is stored."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "parse_error"
    reason = "INVALID_JSON"

    def __init__(self, message):
        super().__init__(message)
