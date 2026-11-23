from rest_framework import serializers

from .models import AuditEvent


class AuditEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditEvent
        fields = [
            "id",
            "timestamp",
            "actor",
            "action",
            "entity_type",
            "entity_id",
            "correlation_id",
            "data",
        ]
        read_only_fields = fields
