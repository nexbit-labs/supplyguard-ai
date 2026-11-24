"""Request/response shapes of the Boomi callbacks (U-06): risk cases and execution results."""
from rest_framework import serializers

from .serializers import AwareDateTimeField

MAX_SNAPSHOT_ROWS = 5000
SNAPSHOT_KEYS = frozenset(
    {
        "captured_at",
        "supplier",
        "purchase_orders",
        "inventory",
        "alternate_suppliers",
        "shipments",
        "supplier_history",
    }
)
EXECUTION_TERMINAL = ("SUCCEEDED", "FAILED")
ERROR_CLASSES = ("VALIDATION", "AUTH", "BUSINESS", "TRANSIENT", "DOWNSTREAM")


def _row_list():
    return serializers.ListField(
        child=serializers.DictField(), max_length=MAX_SNAPSHOT_ROWS, required=False
    )


class SnapshotSerializer(serializers.Serializer):
    """Structure only (types, sizes, no unknown keys). Content checks are the impact engine's."""

    captured_at = AwareDateTimeField()
    supplier = serializers.DictField(required=False, allow_null=True)
    purchase_orders = _row_list()
    inventory = _row_list()
    alternate_suppliers = _row_list()
    shipments = _row_list()
    supplier_history = _row_list()

    def validate_purchase_orders(self, rows):
        ids = [r.get("po_id") for r in rows if isinstance(r.get("po_id"), str)]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Duplicate po_id in purchase_orders.")
        return rows

    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - SNAPSHOT_KEYS)
            if unknown:
                raise serializers.ValidationError(
                    {key: ["Unknown snapshot field."] for key in unknown}
                )
        return super().to_internal_value(data)


class RiskCaseRequestSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=80)
    snapshot = SnapshotSerializer()


class ExposureSerializer(serializers.Serializer):
    amount = serializers.CharField()
    currency = serializers.CharField()


class RiskCaseCreatedSerializer(serializers.Serializer):
    risk_case_id = serializers.CharField(allow_null=True)
    status = serializers.CharField(required=False)
    risk_level = serializers.CharField(required=False)
    risk_score = serializers.IntegerField(required=False)
    exposure = ExposureSerializer(required=False)
    affected_po_count = serializers.IntegerField(required=False)
    data_quality_flags = serializers.ListField(child=serializers.CharField(), required=False)
    correlation_id = serializers.UUIDField(required=False)
    flagged = serializers.BooleanField(required=False)
    no_impact = serializers.BooleanField(required=False)


class ExecutionResultRequestSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=EXECUTION_TERMINAL)
    attempt = serializers.IntegerField(min_value=1)  # ExecutionRequest.attempt, echoed by Boomi
    verified = serializers.BooleanField(required=False, default=False)
    response_status = serializers.IntegerField(
        required=False, allow_null=True, min_value=100, max_value=599
    )
    response_payload = serializers.DictField(required=False, allow_null=True)
    error = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=2000
    )
    error_class = serializers.ChoiceField(choices=ERROR_CLASSES, required=False, allow_null=True)

    def validate(self, attrs):
        # SUCCEEDED without verified=true is NOT an error here: the service turns it into
        # FAILED / BUSINESS so the execution is never stranded (see executions.py).
        if attrs["status"] == "FAILED":
            errors = {}
            if not attrs.get("error_class"):
                errors["error_class"] = ["FAILED requires error_class."]
            if not (attrs.get("error") or "").strip():
                errors["error"] = ["FAILED requires an error message."]
            if errors:
                raise serializers.ValidationError(errors)
        return attrs


class ExecutionResultResponseSerializer(serializers.Serializer):
    execution_id = serializers.CharField()
    status = serializers.CharField()
    error_class = serializers.CharField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    case_status = serializers.CharField()
    retryable = serializers.BooleanField()
