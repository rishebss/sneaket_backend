# products/views.py
from rest_framework.pagination import PageNumberPagination
from rest_framework import viewsets
from .models import Sneaker
from .serializers import SneakerSerializer


class CustomPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class SneakerViewSet(viewsets.ModelViewSet):
    """
    Basic CRUD operations for Sneakers.

    ModelViewSet automatically provides:
    - GET /api/sneakers/          -> List all sneakers
    - POST /api/sneakers/         -> Create new sneaker
    - GET /api/sneakers/{id}/     -> Get single sneaker
    - PUT /api/sneakers/{id}/     -> Update entire sneaker
    - PATCH /api/sneakers/{id}/   -> Update partial sneaker
    - DELETE /api/sneakers/{id}/  -> Delete sneaker
    """

    queryset = Sneaker.objects.all()
    pagination_class = CustomPagination
    serializer_class = SneakerSerializer
