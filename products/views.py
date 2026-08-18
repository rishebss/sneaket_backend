from rest_framework.pagination import PageNumberPagination
from rest_framework import viewsets, filters, status, serializers
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Sum
from django_filters.rest_framework import (
    DjangoFilterBackend,
    FilterSet,
    CharFilter,
    BooleanFilter,
)
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from .models import Sneaker, Favorite, CartItem, Review
from .serializers import (
    SneakerSerializer,
    FavoriteSerializer,
    CartItemSerializer,
    ReviewSerializer,
)
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied


class CustomPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class SneakerFilter(FilterSet):
    features = CharFilter(method="filter_features")
    brand = CharFilter(lookup_expr="iexact")
    category = CharFilter(lookup_expr="iexact")
    favorited = BooleanFilter(method="filter_favorited")

    class Meta:
        model = Sneaker
        fields = ["brand", "category"]

    def filter_features(self, queryset, name, value):
        # Filter for Postgres JSONField containment
        return queryset.filter(features__contains=[value])

    def filter_favorited(self, queryset, name, value):
        if value and self.request.user.is_authenticated:
            return queryset.filter(favorited_by__user=self.request.user)
        return queryset


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
    serializer_class = SneakerSerializer

    @property
    def pagination_class(self):
        if self.request.query_params.get("favorited") == "true":
            return None
        return CustomPagination

    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_class = SneakerFilter
    search_fields = ["name", "brand", "description"]
    ordering_fields = ["price", "rating", "created_at"]


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

    @action(detail=False, methods=["post"])
    def toggle(self, request):
        """
        Simple toggle favorite - add if not exists, remove if exists
        """
        sneaker_id = request.data.get("sneaker_id")

        if not sneaker_id:
            return Response(
                {"error": "sneaker_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        # Check if sneaker exists
        sneaker = get_object_or_404(Sneaker, id=sneaker_id)

        # Try to get existing favorite
        favorite = Favorite.objects.filter(user=request.user, sneaker=sneaker).first()

        if favorite:
            # Remove if exists
            favorite.delete()
            return Response(
                {"is_favorited": False, "message": "Removed from favorites"}
            )
        else:
            # Add if doesn't exist
            favorite = Favorite.objects.create(user=request.user, sneaker=sneaker)
            serializer = self.get_serializer(favorite)
            return Response(
                {"is_favorited": True, "favorite": serializer.data},
                status=status.HTTP_201_CREATED,
            )

    @action(detail=False, methods=["get"])
    def check(self, request):
        """
        Check if a sneaker is favorited
        """
        sneaker_id = request.query_params.get("sneaker_id")

        if not sneaker_id:
            return Response(
                {"error": "sneaker_id is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        is_favorited = Favorite.objects.filter(
            user=request.user, sneaker_id=sneaker_id
        ).exists()

        return Response({"sneaker_id": sneaker_id, "is_favorited": is_favorited})

    @action(detail=False, methods=["post"])
    def bulk_check(self, request):
        """
        Check favorite status for multiple sneakers
        """
        sneaker_ids = request.data.get("sneaker_ids", [])

        if not sneaker_ids:
            return Response(
                {"error": "sneaker_ids list is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_favorites = set(
            self.get_queryset()
            .filter(sneaker_id__in=sneaker_ids)
            .values_list("sneaker_id", flat=True)
        )

        result = {
            str(sneaker_id): sneaker_id in user_favorites for sneaker_id in sneaker_ids
        }

        return Response(result)


class CartViewSet(viewsets.ModelViewSet):
    """
    Cart API - mirrors the favorites workflow but tracks quantity + size.
    Endpoints:
      GET    /api/cart/            -> list current user's cart items
      POST   /api/cart/            -> add item (increments qty if same line exists)
      GET    /api/cart/{id}/       -> retrieve a cart line
      PATCH  /api/cart/{id}/       -> update quantity of a line
      DELETE /api/cart/{id}/       -> remove a line
      POST   /api/cart/add/        -> add item by sneaker_id (+ optional size/quantity)
      POST   /api/cart/remove/     -> remove line by sneaker_id (+ size)
      GET    /api/cart/count/      -> total quantity across all lines (for navbar badge)
    """

    serializer_class = CartItemSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ["sneaker__name", "sneaker__brand"]

    @property
    def pagination_class(self):
        # Carts are small - return all items unpaginated
        return None

    def get_queryset(self):
        return CartItem.objects.filter(user=self.request.user).select_related("sneaker")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def _resolve_line(self, sneaker_id, size):
        """Find an existing cart line for this user+sneaker+size."""
        return CartItem.objects.filter(
            user=self.request.user, sneaker_id=sneaker_id, size=size
        ).first()

    def _available_for(self, sneaker, line_id=None):
        """
        Max quantity still addable for a sneaker, accounting for stock and
        what is already reserved in the user's cart (across all sizes).

        `line_id` excludes a line being edited so its own qty isn't double
        counted. Returns 0 when sold out / fully reserved.
        """
        reserved = CartItem.objects.filter(user=self.request.user, sneaker=sneaker)
        if line_id is not None:
            reserved = reserved.exclude(id=line_id)
        reserved = reserved.aggregate(total=Sum("quantity"))["total"] or 0
        return max(sneaker.copies - reserved, 0)

    def _check_stock(self, sneaker, requested, line_id=None):
        """Raise a 400 ValidationError if `requested` exceeds available stock."""
        available = self._available_for(sneaker, line_id=line_id)
        if requested > available:
            raise serializers.ValidationError(
                {
                    "error": "Not enough stock",
                    "sneaker_id": sneaker.id,
                    "requested": requested,
                    "available": available,
                }
            )

    def create(self, request, *args, **kwargs):
        """
        Create a cart line. If the same user+sneaker+size already exists,
        increment its quantity instead of creating a duplicate.
        """
        sneaker_id = request.data.get("sneaker")
        size = request.data.get("size")
        quantity = int(request.data.get("quantity", 1) or 1)

        existing = self._resolve_line(sneaker_id, size)
        sneaker = get_object_or_404(Sneaker, id=sneaker_id)
        # `existing` is included in reserved stock, so check added amount
        self._check_stock(sneaker, quantity)

        if existing:
            existing.quantity += quantity
            existing.save()
            serializer = self.get_serializer(existing)
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(
            serializer.data, status=status.HTTP_201_CREATED, headers=headers
        )

    @action(detail=False, methods=["post"])
    def add(self, request):
        """
        Add a sneaker to the cart. Increments quantity if the line exists.
        Accepts sneaker_id, optional size and quantity.
        """
        sneaker_id = request.data.get("sneaker_id")
        size = request.data.get("size")
        quantity = int(request.data.get("quantity", 1) or 1)

        if not sneaker_id:
            return Response(
                {"error": "sneaker_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        sneaker = get_object_or_404(Sneaker, id=sneaker_id)

        existing = self._resolve_line(sneaker_id, size)
        # `existing` is included in reserved stock, so check the added amount
        self._check_stock(sneaker, quantity)

        if existing:
            existing.quantity += quantity
            existing.save()
            item, created = existing, False
        else:
            item = CartItem.objects.create(
                user=request.user, sneaker=sneaker, size=size, quantity=quantity
            )
            created = True

        serializer = self.get_serializer(item)
        return Response(
            {
                "item": serializer.data,
                "cart_count": self._total_quantity(),
                "created": created,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def partial_update(self, request, *args, **kwargs):
        """
        PATCH a cart line. Validate the new absolute quantity against stock
        (excluding this line's own reserved qty).
        """
        line = self.get_object()
        if "quantity" in request.data:
            try:
                new_quantity = int(request.data.get("quantity"))
            except (TypeError, ValueError):
                return Response(
                    {"error": "quantity must be an integer"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if new_quantity < 1:
                return Response(
                    {"error": "quantity must be at least 1"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            self._check_stock(line.sneaker, new_quantity, line_id=line.id)
        return super().partial_update(request, *args, **kwargs)

    @action(detail=False, methods=["post"])
    def remove(self, request):
        """
        Remove a cart line by sneaker_id (+ optional size).
        """
        sneaker_id = request.data.get("sneaker_id")
        size = request.data.get("size")

        if not sneaker_id:
            return Response(
                {"error": "sneaker_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        deleted, _ = CartItem.objects.filter(
            user=request.user, sneaker_id=sneaker_id, size=size
        ).delete()

        return Response({"removed": deleted > 0, "cart_count": self._total_quantity()})

    @action(detail=False, methods=["get"])
    def count(self, request):
        """Total quantity across the user's cart (for navbar badge)."""
        return Response({"count": self._total_quantity()})

    def _total_quantity(self):
        return self.get_queryset().aggregate(total=Sum("quantity"))["total"] or 0


class ReviewViewSet(viewsets.ModelViewSet):
    """
    Product reviews.
      GET    /api/reviews/?sneaker=<id>  -> list reviews for a sneaker
      POST   /api/reviews/                -> create / update own review (upsert)
      GET    /api/reviews/<id>/           -> retrieve a review
      PATCH  /api/reviews/<id>/           -> edit own review
      DELETE /api/reviews/<id>/           -> delete own review
    """

    serializer_class = ReviewSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAuthenticated()]
        return [AllowAny()]

    def get_queryset(self):
        queryset = Review.objects.select_related("user", "sneaker").all()
        sneaker_id = self.request.query_params.get("sneaker")
        if sneaker_id:
            queryset = queryset.filter(sneaker_id=sneaker_id)
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        if serializer.instance.user != self.request.user:
            raise PermissionDenied("You can only edit your own review.")
        serializer.save()

    def perform_destroy(self, instance):
        if instance.user != self.request.user and not self.request.user.is_staff:
            raise PermissionDenied("You can only delete your own review.")
        instance.delete()

    def create(self, request, *args, **kwargs):
        """
        Upsert: if the user already reviewed this sneaker, update it instead
        of creating a duplicate.
        """
        sneaker_id = request.data.get("sneaker")
        if not sneaker_id:
            return Response(
                {"error": "sneaker is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        sneaker = get_object_or_404(Sneaker, id=sneaker_id)
        existing = Review.objects.filter(user=request.user, sneaker=sneaker).first()
        if existing:
            serializer = self.get_serializer(existing, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)
