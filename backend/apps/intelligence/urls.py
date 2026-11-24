from django.urls import path

from .views import CaseWhatIfView, WhatIfView

urlpatterns = [
    path("what-if", WhatIfView.as_view(), name="what-if"),
    path("risk-cases/<str:case_id>/what-if", CaseWhatIfView.as_view(), name="case-what-if"),
]
