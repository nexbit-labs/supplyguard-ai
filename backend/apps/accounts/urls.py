from django.urls import path

from .views import MeView, SettingsView, TokenObtainView, TokenRefreshView, UserListCreateView

urlpatterns = [
    path("auth/token", TokenObtainView.as_view(), name="auth-token"),
    path("auth/refresh", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me", MeView.as_view(), name="me"),
    path("settings", SettingsView.as_view(), name="settings"),
    path("users", UserListCreateView.as_view(), name="users"),
]
