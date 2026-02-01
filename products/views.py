from rest_framework.pagination import PageNumberPagination
from rest_framework import viewsets, filters
from django_filters.rest_framework import DjangoFilterBackend, FilterSet, CharFilter
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from .models import Sneaker
from .serializers import SneakerSerializer


class CustomPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class SneakerFilter(FilterSet):
    features = CharFilter(method='filter_features')
    brand = CharFilter(lookup_expr='iexact')
    category = CharFilter(lookup_expr='iexact')

    class Meta:
        model = Sneaker
        fields = ['brand', 'category']

    def filter_features(self, queryset, name, value):
        # Filter for Postgres JSONField containment
        return queryset.filter(features__contains=[value])


class SneakerViewSet(viewsets.ModelViewSet):
    """
    Basic CRUD operations for Sneakers with filtering, search, and ordering.

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

    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = SneakerFilter
    search_fields = ['name', 'brand', 'description']
    ordering_fields = ['price', 'rating', 'created_at']

    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
