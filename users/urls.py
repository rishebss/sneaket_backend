from django.urls import path
from .views import (
    RegisterView,
    LoginView,
    LogoutView,
    CurrentUserView,
    UpdateProfileView,
    ChangePasswordView,
)

urlpatterns = [
    path("register", RegisterView.as_view(), name="register"),
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("me", CurrentUserView.as_view(), name="current-user"),
    path("profile", UpdateProfileView.as_view(), name="update-profile"),
    path("change-password", ChangePasswordView.as_view(), name="change-password"),
]
