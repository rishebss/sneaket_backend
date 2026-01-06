# products/views.py
from rest_framework import viewsets
from .models import Sneaker
from .serializers import SneakerSerializer

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
    serializer_class = SneakerSerializer