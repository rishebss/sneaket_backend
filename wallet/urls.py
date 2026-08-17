from django.urls import path
from .views import WalletView, AddMoneyView, VerifyAddMoneyView

urlpatterns = [
    path("", WalletView.as_view()),
    path("add/", AddMoneyView.as_view()),
    path("add/verify/", VerifyAddMoneyView.as_view()),
]
