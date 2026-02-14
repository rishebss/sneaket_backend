from rest_framework.pagination import PageNumberPagination
from rest_framework import viewsets, filters, status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend, FilterSet, CharFilter
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from .models import Sneaker, Favorite
from .serializers import SneakerSerializer, FavoriteSerializer
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action




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


class FavoriteViewSet(viewsets.ModelViewSet):
    """
    Simple ViewSet for favorites
    """
    serializer_class = FavoriteSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Return only current user's favorites"""
        return Favorite.objects.filter(user=self.request.user)
    
    def perform_create(self, serializer):
        """Auto-assign user when creating"""
        serializer.save(user=self.request.user)
    
    @action(detail=False, methods=['post'])
    def toggle(self, request):
        """
        Simple toggle favorite - add if not exists, remove if exists
        """
        sneaker_id = request.data.get('sneaker_id')
        
        if not sneaker_id:
            return Response(
                {'error': 'sneaker_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if sneaker exists
        sneaker = get_object_or_404(Sneaker, id=sneaker_id)
        
        # Try to get existing favorite
        favorite = Favorite.objects.filter(
            user=request.user,
            sneaker=sneaker
        ).first()
        
        if favorite:
            # Remove if exists
            favorite.delete()
            return Response({
                'is_favorited': False,
                'message': 'Removed from favorites'
            })
        else:
            # Add if doesn't exist
            favorite = Favorite.objects.create(
                user=request.user,
                sneaker=sneaker
            )
            serializer = self.get_serializer(favorite)
            return Response({
                'is_favorited': True,
                'favorite': serializer.data
            }, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'])
    def check(self, request):
        """
        Check if a sneaker is favorited
        """
        sneaker_id = request.query_params.get('sneaker_id')
        
        if not sneaker_id:
            return Response(
                {'error': 'sneaker_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        is_favorited = Favorite.objects.filter(
            user=request.user,
            sneaker_id=sneaker_id
        ).exists()
        
        return Response({
            'sneaker_id': sneaker_id,
            'is_favorited': is_favorited
        })
    
    @action(detail=False, methods=['post'])
    def bulk_check(self, request):
        """
        Check favorite status for multiple sneakers
        """
        sneaker_ids = request.data.get('sneaker_ids', [])
        
        if not sneaker_ids:
            return Response(
                {'error': 'sneaker_ids list is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user_favorites = set(self.get_queryset().filter(
            sneaker_id__in=sneaker_ids
        ).values_list('sneaker_id', flat=True))
        
        result = {
            str(sneaker_id): sneaker_id in user_favorites
            for sneaker_id in sneaker_ids
        }
        
        return Response(result)


