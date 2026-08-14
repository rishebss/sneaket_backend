from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SneakerViewSet, FavoriteViewSet, CartViewSet

router = DefaultRouter(trailing_slash=r"/?")
router.register(r"sneakers", SneakerViewSet, basename="sneaker")
router.register(r"favorites", FavoriteViewSet, basename="favorite")
router.register(r"cart", CartViewSet, basename="cart")

urlpatterns = [
    path("", include(router.urls)),
]
