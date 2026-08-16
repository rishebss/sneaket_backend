from django.urls import path
from .views import OrdersView, VerifyPaymentView, OrderDetailView

urlpatterns = [
    path("", OrdersView.as_view()),
    path("verify/", VerifyPaymentView.as_view()),
    path("<str:order_number>/", OrderDetailView.as_view()),
]
