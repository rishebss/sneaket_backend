from django.urls import path
from .views import (
    RegisterView,
    LoginView,
    LogoutView,
    CurrentUserView,
    UpdateProfileView,
    ChangePasswordView,
    ClaimDailyRewardView,
    AddressView,
    AddressDetailView,
)

urlpatterns = [
    path("register", RegisterView.as_view(), name="register"),
    path("login", LoginView.as_view(), name="login"),
    path("logout", LogoutView.as_view(), name="logout"),
    path("me", CurrentUserView.as_view(), name="current-user"),
    path(
        "claim-daily-reward", ClaimDailyRewardView.as_view(), name="claim-daily-reward"
    ),
    path("profile", UpdateProfileView.as_view(), name="update-profile"),
    path("change-password", ChangePasswordView.as_view(), name="change-password"),
    path("addresses", AddressView.as_view(), name="addresses"),
    path("addresses/<int:pk>", AddressDetailView.as_view(), name="address-detail"),
]
