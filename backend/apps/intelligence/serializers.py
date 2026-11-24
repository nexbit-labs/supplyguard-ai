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
