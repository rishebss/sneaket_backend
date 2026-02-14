from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SneakerViewSet, FavoriteViewSet

router = DefaultRouter(trailing_slash=r'/?')
router.register(r'sneakers', SneakerViewSet, basename='sneaker')
router.register(r'favorites', FavoriteViewSet, basename='favorite')

urlpatterns = [
    path('', include(router.urls)),
    
]
