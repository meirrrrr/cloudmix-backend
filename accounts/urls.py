from django.urls import path

from . import views

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/token/refresh/", views.TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("users/me/", views.MeView.as_view(), name="users-me"),
    path("users/search/", views.UserSearchView.as_view(), name="users-search"),
]
