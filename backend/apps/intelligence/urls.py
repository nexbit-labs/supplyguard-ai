from django.urls import path

from .views import AnalysisView, CaseWhatIfView, WhatIfView

urlpatterns = [
    path("what-if", WhatIfView.as_view(), name="what-if"),
    path("risk-cases/<str:case_id>/what-if", CaseWhatIfView.as_view(), name="case-what-if"),
    path(
        "internal/risk-cases/<str:case_id>/analysis",
        AnalysisView.as_view(),
        name="internal-analysis",
    ),
]
