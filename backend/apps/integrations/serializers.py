"""Request validation for the event intake (docs/CONTRACTS.md s3 canonical event)."""
from datetime import UTC

from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.core.models import DisruptionEvent

EVENT_ID_PATTERN = r"^EVT-[0-9]+$"
SCENARIO_PATTERN = r"^[a-z0-9_]+$"
MIN_YEAR, MAX_YEAR = 2000, 2100  # event/snapshot times outside are a 400
DESCRIPTION_MAX = 4000  # the model column is unbounded text; capped here


@extend_schema_field({"type": "string", "format": "date-time"})
class AwareDateTimeField(serializers.Field):
    """ISO-8601 string with an explicit offset (Z or +hh:mm). A naive time is a 400: the
    server must never guess the zone."""

    default_error_messages = {
        "invalid": "Enter an ISO 8601 date and time with a time zone, e.g. 2026-12-02T10:15:00Z.",
        "naive": "The time must include a time zone (Z or +hh:mm).",
        "range": f"The year must be between {MIN_YEAR} and {MAX_YEAR}.",
    }

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        try:
            value = parse_datetime(data.strip())
        except ValueError:
            value = None
        if value is None:
            self.fail("invalid")
        if value.tzinfo is None or value.utcoffset() is None:
            self.fail("naive")
        if not MIN_YEAR <= value.year <= MAX_YEAR:
            self.fail("range")
        try:
            value.astimezone(UTC)  # can overflow near year 1 / 9999
        except (OverflowError, ValueError):
            self.fail("range")
        return value

    def to_representation(self, value):
        return value.isoformat()


class DisruptionEventInputSerializer(serializers.Serializer):
    event_id = serializers.RegexField(EVENT_ID_PATTERN, max_length=80)
    supplier_id = serializers.CharField(
        max_length=DisruptionEvent._meta.get_field("supplier_erp_id").max_length
    )
    event_type = serializers.ChoiceField(choices=DisruptionEvent.EventType.choices)
    severity = serializers.ChoiceField(choices=DisruptionEvent.Severity.choices)
    event_time = AwareDateTimeField()
    expected_duration_hours = serializers.DecimalField(
        max_digits=8, decimal_places=2, min_value=0, required=False, allow_null=True
    )
    description = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=DESCRIPTION_MAX
    )
    source_system = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=DisruptionEvent._meta.get_field("source_system").max_length,
    )
    shipment_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=DisruptionEvent._meta.get_field("shipment_id").max_length,
    )
    po_id = serializers.CharField(
        required=False,
        allow_blank=True,
        allow_null=True,
        max_length=DisruptionEvent._meta.get_field("po_id").max_length,
    )
    correlation_id = serializers.UUIDField(required=False, allow_null=True)
    # Informational only: compared with the credential's tenant, never used as the tenant.
    tenant_id = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class EventAcceptedSerializer(serializers.Serializer):
    """Response body of POST /events/disruptions (202 new, 200 duplicate)."""

    event_id = serializers.CharField()
    correlation_id = serializers.UUIDField()
    status = serializers.CharField()
    risk_case_id = serializers.CharField(allow_null=True)


class SimulateRequestSerializer(serializers.Serializer):
    scenario = serializers.RegexField(SCENARIO_PATTERN, max_length=80)
    event_time = AwareDateTimeField(required=False, allow_null=True)
    fresh_event_id = serializers.BooleanField(required=False, default=True)


class SimulateSentSerializer(EventAcceptedSerializer):
    http_status = serializers.IntegerField()


class SimulateResponseSerializer(serializers.Serializer):
    scenario = serializers.CharField()
    event_id = serializers.CharField()
    sent = SimulateSentSerializer(many=True)


class BoomiStatusSerializer(serializers.Serializer):
    status = serializers.CharField()
    configured = serializers.BooleanField()
    auth_mode = serializers.CharField()
    host = serializers.CharField(allow_blank=True)
    reachable = serializers.BooleanField(allow_null=True)
    latency_ms = serializers.IntegerField(allow_null=True)
    checked_at = serializers.DateTimeField(allow_null=True)


class Events24hSerializer(serializers.Serializer):
    RECEIVED = serializers.IntegerField()
    PUBLISHED = serializers.IntegerField()
    PUBLISH_FAILED = serializers.IntegerField()
    needs_data_correction = serializers.IntegerField()


class IntegrationStatusSerializer(serializers.Serializer):
    boomi = BoomiStatusSerializer()
    events_24h = Events24hSerializer()
