from rest_framework import serializers
from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = [
            "id",
            "sneaker",
            "sneaker_name",
            "sneaker_image",
            "size",
            "quantity",
            "unit_price",
            "line_total",
        ]


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "id",
            "order_number",
            "recipient_name",
            "email",
            "phone",
            "address",
            "pincode",
            "city",
            "state",
            "payment_method",
            "payment_status",
            "status",
            "subtotal",
            "shipping_fee",
            "total",
            "razorpay_order_id",
            "created_at",
            "items",
        ]


class CreateOrderSerializer(serializers.Serializer):
    recipient_name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    address = serializers.CharField()
    pincode = serializers.CharField(max_length=10, required=False, allow_blank=True)
    city = serializers.CharField(max_length=100, required=False, allow_blank=True)
    state = serializers.CharField(max_length=100, required=False, allow_blank=True)
    payment_method = serializers.CharField(required=False, default="online")


class VerifyPaymentSerializer(serializers.Serializer):
    razorpay_order_id = serializers.CharField()
    razorpay_payment_id = serializers.CharField()
    razorpay_signature = serializers.CharField()
