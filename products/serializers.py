from rest_framework import serializers
from .models import Sneaker, Favorite, CartItem
from django.conf import settings


class SneakerSerializer(serializers.ModelSerializer):
    # Override the image fields to return URLs directly
    img1 = serializers.SerializerMethodField()
    img2 = serializers.SerializerMethodField()
    img3 = serializers.SerializerMethodField()

    class Meta:
        model = Sneaker
        fields = "__all__"
        read_only_fields = ["created_at", "updated_at"]

    def get_img1(self, obj):
        return obj.img1.url if obj.img1 else None

    def get_img2(self, obj):
        return obj.img2.url if obj.img2 else None

    def get_img3(self, obj):
        return obj.img3.url if obj.img3 else None


class FavoriteSerializer(serializers.ModelSerializer):
    """
    Simple favorite serializer
    """

    sneaker_name = serializers.CharField(source="sneaker.name", read_only=True)
    sneaker_brand = serializers.CharField(source="sneaker.brand", read_only=True)
    sneaker_price = serializers.DecimalField(
        source="sneaker.price", max_digits=10, decimal_places=2, read_only=True
    )
    sneaker_image = serializers.CharField(source="sneaker.img1.url", read_only=True)

    class Meta:
        model = Favorite
        fields = [
            "id",
            "sneaker",
            "sneaker_name",
            "sneaker_brand",
            "sneaker_price",
            "sneaker_image",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class CartItemSerializer(serializers.ModelSerializer):
    """
    Cart item serializer with denormalized sneaker details for easy UI rendering.
    """

    sneaker_name = serializers.CharField(source="sneaker.name", read_only=True)
    sneaker_brand = serializers.CharField(source="sneaker.brand", read_only=True)
    sneaker_price = serializers.DecimalField(
        source="sneaker.price", max_digits=10, decimal_places=2, read_only=True
    )
    sneaker_original_price = serializers.DecimalField(
        source="sneaker.original_price", max_digits=10, decimal_places=2, read_only=True
    )
    sneaker_image = serializers.CharField(source="sneaker.img1.url", read_only=True)
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "sneaker",
            "sneaker_name",
            "sneaker_brand",
            "sneaker_price",
            "sneaker_original_price",
            "sneaker_image",
            "size",
            "quantity",
            "line_total",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_line_total(self, obj):
        return float(obj.sneaker.price) * obj.quantity
