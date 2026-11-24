from django.urls import path

from .views import (
    EventIntakeView,
    ExecutionResultView,
    IntegrationStatusView,
    RiskCaseCallbackView,
    SimulateDisruptionView,
)

urlpatterns = [
    path("events/disruptions", EventIntakeView.as_view(), name="events-disruptions"),
    path("demo/simulate-disruption", SimulateDisruptionView.as_view(), name="demo-simulate"),
    path("integrations/status", IntegrationStatusView.as_view(), name="integrations-status"),
    path("internal/risk-cases", RiskCaseCallbackView.as_view(), name="internal-risk-cases"),
    path(
        "internal/executions/<str:execution_id>/result",
        ExecutionResultView.as_view(),
        name="internal-execution-result",
    ),
]
