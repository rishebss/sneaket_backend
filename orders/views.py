import razorpay
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Order, OrderItem
from .serializers import (
    OrderSerializer,
    CreateOrderSerializer,
    VerifyPaymentSerializer,
)
from products.models import Sneaker, CartItem

razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)


def generate_order_number():
    stamp = timezone.now().strftime("%Y%m%d")
    rand = timezone.now().strftime("%H%M%S")
    return f"SNEK-{stamp}-{rand}"


class OrdersView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        orders = (
            Order.objects.filter(user=request.user)
            .prefetch_related("items")
            .order_by("-created_at")
        )
        return Response(OrderSerializer(orders, many=True).data)

    def post(self, request):
        serializer = CreateOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Build the order only from the items the user marked for checkout
        cart_items = list(
            CartItem.objects.filter(user=request.user, is_selected=True).select_related(
                "sneaker"
            )
        )
        if not cart_items:
            return Response(
                {"error": "No items selected for checkout"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Re-validate stock against the live catalog (never trust client amounts)
        insufficient = []
        for ci in cart_items:
            if ci.sneaker.copies < ci.quantity:
                insufficient.append(
                    {
                        "sneaker": ci.sneaker.name,
                        "available": ci.sneaker.copies,
                        "requested": ci.quantity,
                    }
                )
        if insufficient:
            return Response(
                {
                    "error": "Insufficient stock for some items",
                    "items": insufficient,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Server-authoritative totals (snapshots of current price)
        subtotal = sum(
            (ci.sneaker.price * ci.quantity for ci in cart_items), Decimal("0")
        )
        shipping_fee = Decimal("0")
        total = subtotal + shipping_fee

        order_number = generate_order_number()

        # Create the Razorpay order (amount in paise)
        try:
            rzp_order = razorpay_client.order.create(
                {
                    "amount": int(total * 100),
                    "currency": "INR",
                    "payment_capture": 1,
                }
            )
            razorpay_order_id = rzp_order["id"]
        except Exception as e:
            return Response(
                {"error": f"Payment gateway error: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = Order.objects.create(
            order_number=order_number,
            user=request.user,
            recipient_name=data["recipient_name"],
            email=data["email"],
            phone=data.get("phone", ""),
            address=data["address"],
            pincode=data.get("pincode", ""),
            city=data.get("city", ""),
            state=data.get("state", ""),
            payment_method="online",
            payment_status="pending",
            status="pending_payment",
            subtotal=subtotal,
            shipping_fee=shipping_fee,
            total=total,
            razorpay_order_id=razorpay_order_id,
            cart_item_ids=[ci.id for ci in cart_items],
        )

        for ci in cart_items:
            OrderItem.objects.create(
                order=order,
                sneaker=ci.sneaker,
                sneaker_name=ci.sneaker.name,
                sneaker_image=ci.sneaker.image_list[0] if ci.sneaker.image_list else "",
                size=ci.size or "",
                quantity=ci.quantity,
                unit_price=ci.sneaker.price,
                line_total=ci.sneaker.price * ci.quantity,
            )

        return Response(
            {
                "order_number": order.order_number,
                "razorpay_order_id": order.razorpay_order_id,
                "razorpay_key": settings.RAZORPAY_KEY_ID,
                "amount": int(total * 100),
                "currency": "INR",
                "order": OrderSerializer(order).data,
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyPaymentView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifyPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            order = Order.objects.get(order_number=d["order_number"], user=request.user)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND
            )

        # Idempotency: already fulfilled
        if order.payment_status == "paid":
            return Response(
                {
                    "success": True,
                    "order_number": order.order_number,
                    "already_paid": True,
                }
            )

        # Mandatory: verify the signature server-side (browser values are forgeable)
        try:
            razorpay_client.utility.verify_payment_signature(
                {
                    "razorpay_order_id": d["razorpay_order_id"],
                    "razorpay_payment_id": d["razorpay_payment_id"],
                    "razorpay_signature": d["razorpay_signature"],
                }
            )
        except razorpay.errors.SignatureVerificationError:
            return Response(
                {"success": False, "error": "Payment signature verification failed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Re-fetch under lock for safe concurrent handling
            order = Order.objects.select_for_update().get(id=order.id)
            if order.payment_status == "paid":
                return Response(
                    {
                        "success": True,
                        "order_number": order.order_number,
                        "already_paid": True,
                    }
                )

            order.payment_status = "paid"
            order.status = "confirmed"
            order.razorpay_payment_id = d["razorpay_payment_id"]
            order.razorpay_signature = d["razorpay_signature"]
            order.save()

            # Decrement stock only after a verified payment
            for item in order.items.select_related("sneaker").all():
                sneaker = Sneaker.objects.select_for_update().get(id=item.sneaker_id)
                sneaker.copies = max(sneaker.copies - item.quantity, 0)
                sneaker.save(update_fields=["copies", "updated_at"])

            # Clear only the selected cart lines that went into this order
            if order.cart_item_ids:
                CartItem.objects.filter(
                    user=request.user, id__in=order.cart_item_ids
                ).delete()

        return Response({"success": True, "order_number": order.order_number})


class OrderDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, order_number):
        try:
            order = Order.objects.prefetch_related("items").get(
                order_number=order_number, user=request.user
            )
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(OrderSerializer(order).data)
