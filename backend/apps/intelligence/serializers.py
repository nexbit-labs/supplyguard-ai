from rest_framework import serializers

from .whatif import EVENT_TYPES, MAX_DAYS, MIN_DAYS


class WhatIfRequestSerializer(serializers.Serializer):
    supplier_id = serializers.RegexField(r"^SUP-[0-9]+$", max_length=40)
    duration_days = serializers.IntegerField(min_value=MIN_DAYS, max_value=MAX_DAYS)
    event_type = serializers.ChoiceField(choices=EVENT_TYPES)
    start = serializers.DateTimeField(required=False)  # default: now


class CaseWhatIfRequestSerializer(serializers.Serializer):
    duration_days = serializers.IntegerField(min_value=MIN_DAYS, max_value=MAX_DAYS, required=False)
    start = serializers.DateTimeField(required=False)  # default: the case event's time


class AnalysisRequestSerializer(serializers.Serializer):
    """POST /internal/risk-cases/{id}/analysis. `payload` is the agent output as received (any
    JSON, stored raw even when it is garbage); `round` is the
    case's analysis_round the agent worked on (required)."""

    kind = serializers.ChoiceField(choices=["RISK_ANALYSIS", "RECOVERY_PLAN"])
    payload = serializers.JSONField()
    # required: a delayed round-1 plan must never be applied as round 2
    round = serializers.IntegerField(min_value=1)


class AnalysisResponseSerializer(serializers.Serializer):
    agent_run_id = serializers.CharField()
    valid = serializers.BooleanField()
    actionable_count = serializers.IntegerField()
    risk_case_id = serializers.CharField()
    case_status = serializers.CharField()
    analysis_round = serializers.IntegerField()
    recommendation_ids = serializers.ListField(child=serializers.CharField())
