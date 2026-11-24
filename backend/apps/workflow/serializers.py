from rest_framework import serializers

from apps.core.models import Approval

_NEEDS_COMMENTS = (Approval.Decision.REJECT, Approval.Decision.REQUEST_CHANGES)


class DecisionRequestSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=Approval.Decision.choices)
    comments = serializers.CharField(
        max_length=2000, allow_blank=True, required=False, default="", trim_whitespace=True
    )

    def validate(self, attrs):
        if attrs["decision"] in _NEEDS_COMMENTS and not attrs.get("comments", "").strip():
            raise serializers.ValidationError(
                {"comments": ["Comments are required for REJECT and REQUEST_CHANGES."]}
            )
        return attrs


class DecisionResponseSerializer(serializers.Serializer):
    approval_id = serializers.CharField()
    recommendation_id = serializers.CharField()
    decision = serializers.CharField()
    recommendation_status = serializers.CharField()
    risk_case_id = serializers.CharField()
    case_status = serializers.CharField()
    analysis_round = serializers.IntegerField()
    execution_id = serializers.CharField(allow_null=True)
    decided_by = serializers.CharField()
    decided_at = serializers.DateTimeField()
    correlation_id = serializers.CharField()
