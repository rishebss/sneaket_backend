from django.urls import path
from .views import (
    OrdersView,
    VerifyPaymentView,
    OrderDetailView,
    RequestCancelView,
    ApproveCancelView,
    DenyCancelView,
    RefundToWalletView,
    RemoveOrderView,
)

urlpatterns = [
    path("", OrdersView.as_view()),
    path("verify/", VerifyPaymentView.as_view()),
    path("<str:order_number>/", OrderDetailView.as_view()),
    path("<str:order_number>/request-cancel/", RequestCancelView.as_view()),
    path("<str:order_number>/approve-cancel/", ApproveCancelView.as_view()),
    path("<str:order_number>/deny-cancel/", DenyCancelView.as_view()),
    path("<str:order_number>/refund-to-wallet/", RefundToWalletView.as_view()),
    path("<str:order_number>/remove/", RemoveOrderView.as_view()),
]
