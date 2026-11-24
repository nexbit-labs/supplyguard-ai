from django.urls import path

from .views import AuditEventList

urlpatterns = [
    path("audit", AuditEventList.as_view(), name="audit-list"),
]
