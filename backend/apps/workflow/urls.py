from django.urls import path

from .views import RecommendationDecisionView

urlpatterns = [
    path(
        "recommendations/<str:pk>/decision",
        RecommendationDecisionView.as_view(),
        name="recommendation-decision",
    ),
]
