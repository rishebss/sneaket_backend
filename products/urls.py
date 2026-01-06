from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SneakerViewSet

router = DefaultRouter()
router.register(r'sneakers', SneakerViewSet, basename='sneaker')

urlpatterns = [
    path('', include(router.urls)),
    
]