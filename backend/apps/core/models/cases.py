"""Risk cases and everything that hangs off one (docs/CONTRACTS.md s10)."""
import contextvars
from contextlib import contextmanager

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .base import TenantOwned
from .catalog import Supplier
from .events import DisruptionEvent

# --- RiskCase.status guard --------------------------------------------------------------------
_status_change_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "risk_case_status_change_depth", default=0
)
_UNSET = object()


class StatusChangeNotAllowed(RuntimeError):
    """RiskCase.status was changed outside apps.workflow.services.transition()."""


class ApprovalImmutable(RuntimeError):
    """An Approval is a recorded decision; it cannot be changed after it is inserted."""


class AgentRunAppendOnly(RuntimeError):
    """An AgentRun is a raw record of one agent call; it cannot be changed after insert."""


@contextmanager
def status_change_allowed():
    """Allow `RiskCase.status` to be changed and saved inside the `with` block.

    ONLY `apps.workflow.services.transition()` may use this (CLAUDE.md s6: RiskCase state
    changes only via transition()). Intended use inside transition():

        with transaction.atomic():
            case = RiskCase.objects.select_for_update().get(pk=case.pk, tenant=tenant)
            # ...validate the move against the state machine (CONTRACTS s7)...
            with status_change_allowed():
                case.status = to_state
                case.save(update_fields=["status", "updated_at"])
            # ...write the AuditEvent in the same transaction...

    `QuerySet.update(status=...)` is rejected even inside the block; use save().
    Do not spawn asyncio tasks inside the block: tasks created there inherit the contextvar
    and would be allowed to change the status too.
    """
    token = _status_change_depth.set(_status_change_depth.get() + 1)
    try:
        yield
    finally:
        _status_change_depth.reset(token)


class RiskCaseQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if "status" in kwargs:
            raise StatusChangeNotAllowed(
                "RiskCase.status cannot be bulk-updated; use workflow.services.transition()."
            )
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        if "status" in fields:
            raise StatusChangeNotAllowed(
                "RiskCase.status cannot be bulk-updated; use workflow.services.transition()."
            )
        return super().bulk_update(objs, fields, *args, **kwargs)


class RiskCase(TenantOwned):
    class Status(models.TextChoices):
        RECEIVED = "RECEIVED"
        ANALYZING = "ANALYZING"
        RECOMMENDATION_READY = "RECOMMENDATION_READY"
        PENDING_APPROVAL = "PENDING_APPROVAL"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        EXECUTING = "EXECUTING"
        EXECUTION_FAILED = "EXECUTION_FAILED"
        RECOVERED = "RECOVERED"
        CLOSED = "CLOSED"

    id_prefix = "RC"
    tenant_parent = "event"

    id = models.CharField(primary_key=True, max_length=20, editable=False)
    event = models.OneToOneField(
        DisruptionEvent, on_delete=models.PROTECT, related_name="risk_case"
    )
    supplier = models.ForeignKey(
        Supplier, null=True, blank=True, on_delete=models.PROTECT, related_name="risk_cases"
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.RECEIVED)
    risk_level = models.CharField(max_length=10, choices=DisruptionEvent.Severity.choices)
    risk_score = models.PositiveSmallIntegerField()  # 0-100, the impact engine's value
    title = models.CharField(max_length=300, blank=True)
    impact_summary = models.TextField(blank=True)
    exposure_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    analysis_round = models.PositiveIntegerField(default=1)
    snapshot = models.JSONField(default=dict, blank=True)
    snapshot_captured_at = models.DateTimeField(null=True, blank=True)
    impact = models.JSONField(default=dict, blank=True)
    data_quality_flags = models.JSONField(default=list, blank=True)
    analysis = models.JSONField(default=dict, blank=True)
    no_viable_option_reason = models.TextField(null=True, blank=True)  # noqa: DJ001
    recovered_at = models.DateTimeField(null=True, blank=True)
    correlation_id = models.UUIDField(db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = RiskCaseQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(risk_score__lte=100), name="riskcase_risk_score_max_100"
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"], name="riskcase_tenant_status_idx"),
            models.Index(fields=["tenant", "created_at"], name="riskcase_tenant_created_idx"),
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded_status = _UNSET

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._loaded_status = instance.__dict__.get("status", _UNSET)
        return instance

    def refresh_from_db(self, using=None, fields=None, **kwargs):
        super().refresh_from_db(using=using, fields=fields, **kwargs)
        if fields is None or "status" in fields:
            self._loaded_status = self.__dict__.get("status", _UNSET)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        saving_status = update_fields is None or "status" in update_fields
        if saving_status and not self._state.adding and _status_change_depth.get() == 0:
            baseline = self._loaded_status
            if baseline is _UNSET:
                # Deferred / never loaded: compare with what is stored.
                baseline = (
                    type(self)
                    ._base_manager.filter(pk=self.pk)
                    .values_list("status", flat=True)
                    .first()
                )
            if baseline is not None and self.status != baseline:
                raise StatusChangeNotAllowed(
                    f"RiskCase.status {baseline} -> {self.status} outside "
                    "workflow.services.transition()."
                )
        super().save(*args, **kwargs)
        if saving_status:
            self._loaded_status = self.__dict__.get("status", _UNSET)

    def __str__(self):
        return self.id


class AgentRun(TenantOwned):
    """One row per agent call, never overwritten (AC-07: raw output stored)."""

    class Agent(models.TextChoices):
        RISK_ANALYST = "RISK_ANALYST"
        RECOVERY_PLANNER = "RECOVERY_PLANNER"

    tenant_parent = "risk_case"

    risk_case = models.ForeignKey(RiskCase, on_delete=models.PROTECT, related_name="agent_runs")
    agent = models.CharField(max_length=20, choices=Agent.choices)
    round = models.PositiveIntegerField(default=1)
    raw = models.JSONField(default=dict, blank=True)
    valid = models.BooleanField(default=False)
    validation_errors = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["risk_case", "created_at", "id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AgentRunAppendOnly("AgentRun rows cannot be modified.")
        super().save(*args, **kwargs)


class AffectedPO(TenantOwned):
    """One row per PO line; the snapshot on the case is the source of truth."""

    tenant_parent = "risk_case"

    risk_case = models.ForeignKey(RiskCase, on_delete=models.CASCADE, related_name="affected_pos")
    po_id = models.CharField(max_length=40)
    item_id = models.CharField(max_length=40)
    item_name = models.CharField(max_length=200, blank=True)
    affected_qty = models.PositiveIntegerField()
    estimated_delay_days = models.DecimalField(max_digits=7, decimal_places=2)
    exposure_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)
    due_date = models.DateField()
    inventory_coverage_days = models.DecimalField(
        max_digits=9, decimal_places=2, null=True, blank=True
    )
    shortage = models.BooleanField(default=False)

    class Meta:
        ordering = ["risk_case", "po_id", "item_id"]
        verbose_name = "affected PO"
        verbose_name_plural = "affected POs"
        constraints = [
            models.UniqueConstraint(
                fields=["risk_case", "po_id", "item_id"], name="uniq_affectedpo_case_po_item"
            ),
        ]


class Recommendation(TenantOwned):
    class OptionType(models.TextChoices):
        ALTERNATE_SUPPLIER = "ALTERNATE_SUPPLIER"
        INVENTORY_TRANSFER = "INVENTORY_TRANSFER"
        REVISED_SCHEDULE = "REVISED_SCHEDULE"
        SPLIT_ORDER = "SPLIT_ORDER"

    class Status(models.TextChoices):
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"
        CHANGES_REQUESTED = "CHANGES_REQUESTED"

    id_prefix = "REC"
    tenant_parent = "risk_case"

    id = models.CharField(primary_key=True, max_length=20, editable=False)
    risk_case = models.ForeignKey(
        RiskCase, on_delete=models.PROTECT, related_name="recommendations"
    )
    round = models.PositiveIntegerField(default=1)
    rank = models.PositiveIntegerField()
    option_type = models.CharField(max_length=20, choices=OptionType.choices)
    proposed_action = models.JSONField(default=dict, blank=True)
    action_steps = models.JSONField(default=list, blank=True)
    trade_offs = models.JSONField(default=list, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    assumptions = models.JSONField(default=list, blank=True)
    data_gaps = models.JSONField(default=list, blank=True)
    estimated_lead_time_days = models.DecimalField(
        max_digits=7, decimal_places=2, null=True, blank=True
    )
    estimated_cost_delta = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=3, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    unsupported = models.BooleanField(default=False)
    unsupported_reasons = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["risk_case", "round", "rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["risk_case", "round", "rank"], name="uniq_recommendation_case_round_rank"
            ),
            models.CheckConstraint(
                condition=~Q(unsupported=True, status="APPROVED"),
                name="recommendation_unsupported_not_approved",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "status"], name="rec_tenant_status_idx")]

    def __str__(self):
        return self.id


class Approval(TenantOwned):
    class Decision(models.TextChoices):
        APPROVE = "APPROVE"
        REJECT = "REJECT"
        REQUEST_CHANGES = "REQUEST_CHANGES"

    id_prefix = "APR"
    tenant_parent = "risk_case"

    id = models.CharField(primary_key=True, max_length=20, editable=False)
    risk_case = models.ForeignKey(RiskCase, on_delete=models.PROTECT, related_name="approvals")
    # No default: left unset, save() copies it from the recommendation.
    round = models.PositiveIntegerField(blank=True)
    recommendation = models.ForeignKey(
        Recommendation, on_delete=models.PROTECT, related_name="approvals"
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    decision = models.CharField(max_length=20, choices=Decision.choices)
    comments = models.TextField(blank=True)
    decided_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-decided_at", "-id"]
        constraints = [
            # At most one APPROVE per round: no double ERP write.
            models.UniqueConstraint(
                fields=["risk_case", "round"],
                condition=Q(decision="APPROVE"),
                name="uniq_approval_one_approve_per_round",
            ),
        ]

    def __str__(self):
        return self.id

    def check_integrity(self):
        if self.recommendation_id is None:
            return
        rec = self.recommendation
        if self.round is None:
            self.round = rec.round
        if rec.risk_case_id != self.risk_case_id:
            raise ValidationError("Approval.risk_case must match recommendation.risk_case.")
        if rec.round != self.round:
            raise ValidationError("Approval.round must match recommendation.round.")
        if self.decision == self.Decision.APPROVE and rec.unsupported:
            raise ValidationError("An unsupported recommendation cannot be approved.")

    def clean(self):
        super().clean()
        self.check_integrity()

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ApprovalImmutable("An Approval cannot be modified after it is recorded.")
        self.check_integrity()
        super().save(*args, **kwargs)


class Execution(TenantOwned):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        RUNNING = "RUNNING"
        SUCCEEDED = "SUCCEEDED"
        FAILED = "FAILED"

    class ErrorClass(models.TextChoices):
        VALIDATION = "VALIDATION"
        AUTH = "AUTH"
        BUSINESS = "BUSINESS"
        TRANSIENT = "TRANSIENT"
        DOWNSTREAM = "DOWNSTREAM"

    RETRYABLE_ERROR_CLASSES = (ErrorClass.TRANSIENT, ErrorClass.DOWNSTREAM)

    id_prefix = "EXE"  # the id is also the ERP Idempotency-Key
    tenant_parent = "approval"

    id = models.CharField(primary_key=True, max_length=20, editable=False)
    approval = models.OneToOneField(Approval, on_delete=models.PROTECT, related_name="execution")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    retry_count = models.PositiveIntegerField(default=0)  # user-requested retries only
    error_class = models.CharField(  # noqa: DJ001
        max_length=12, choices=ErrorClass.choices, null=True, blank=True
    )
    error = models.TextField(null=True, blank=True)  # noqa: DJ001  # sanitised
    target_system = models.CharField(max_length=100, blank=True)
    request_payload = models.JSONField(default=dict, blank=True)
    response_status = models.PositiveSmallIntegerField(null=True, blank=True)
    response_payload = models.JSONField(null=True, blank=True)  # sanitised
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "status"], name="exec_tenant_status_idx")]

    def check_approval(self):
        # No ERP write without a recorded approval (CLAUDE.md s6).
        if self.approval_id is not None and self.approval.decision != Approval.Decision.APPROVE:
            raise ValidationError("An Execution requires an Approval with decision APPROVE.")

    def clean(self):
        super().clean()
        self.check_approval()

    def save(self, *args, **kwargs):
        self.check_approval()
        super().save(*args, **kwargs)

    @property
    def retryable(self) -> bool:
        return (
            self.status == self.Status.FAILED
            and self.error_class in self.RETRYABLE_ERROR_CLASSES
            and self.retry_count < settings.EXECUTION_MAX_RETRIES
        )

    def __str__(self):
        return self.id
