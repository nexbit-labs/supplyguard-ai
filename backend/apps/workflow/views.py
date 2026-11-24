from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from apps.accounts.ids import format_user_id
from apps.accounts.permissions import RolePermission
from apps.core.models import Recommendation
from apps.core.tenancy import TenantScopedMixin

from .serializers import DecisionRequestSerializer, DecisionResponseSerializer
from .services import record_decision


class RecommendationDecisionView(TenantScopedMixin, GenericAPIView):
    """POST /recommendations/{id}/decision - the human approval gate.

    Only a PROCUREMENT_MANAGER user (JWT) may decide; a SYSTEM API key gets 403 (Q1).
    Only APPROVE releases the ERP-write step, and only after the approval is committed.
    """

    queryset = Recommendation.objects.all()
    lookup_field = "pk"
    permission_classes = [RolePermission]
    permission_key = "recommendations.decision"
    serializer_class = DecisionRequestSerializer

    @extend_schema(
        request=DecisionRequestSerializer,
        responses={201: DecisionResponseSerializer},
        summary="Approve, reject or request changes on a recommendation",
    )
    def post(self, request, pk):
        body = DecisionRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        recommendation = self.get_object()  # tenant-scoped: 404 for missing and foreign ids
        result = record_decision(
            recommendation,
            body.validated_data["decision"],
            body.validated_data["comments"],
            user=request.user,
        )
        approval, case = result.approval, result.case
        payload = {
            "approval_id": approval.pk,
            "recommendation_id": result.recommendation.pk,
            "decision": approval.decision,
            "recommendation_status": result.recommendation.status,
            "risk_case_id": case.pk,
            "case_status": case.status,
            "analysis_round": case.analysis_round,
            "execution_id": result.execution.pk if result.execution else None,
            "decided_by": format_user_id(approval.decided_by_id),
            "decided_at": approval.decided_at,
            "correlation_id": str(case.correlation_id),
        }
        return Response(
            DecisionResponseSerializer(payload).data, status=status.HTTP_201_CREATED
        )
