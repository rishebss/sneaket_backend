import razorpay
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Order, OrderItem, PendingCheckout
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

        # Hold a transient snapshot — NO Order is registered yet.
        items_snapshot = []
        cart_item_ids = []
        for ci in cart_items:
            items_snapshot.append(
                {
                    "sneaker_id": ci.sneaker_id,
                    "sneaker_name": ci.sneaker.name,
                    "sneaker_image": (
                        ci.sneaker.image_list[0] if ci.sneaker.image_list else ""
                    ),
                    "size": ci.size or "",
                    "quantity": ci.quantity,
                    "unit_price": str(ci.sneaker.price),
                    "line_total": str(ci.sneaker.price * ci.quantity),
                }
            )
            cart_item_ids.append(ci.id)

        snapshot = {
            "recipient_name": data["recipient_name"],
            "email": data["email"],
            "phone": data.get("phone", ""),
            "address": data["address"],
            "pincode": data.get("pincode", ""),
            "city": data.get("city", ""),
            "state": data.get("state", ""),
            "subtotal": str(subtotal),
            "shipping_fee": str(shipping_fee),
            "total": str(total),
            "items": items_snapshot,
            "cart_item_ids": cart_item_ids,
        }

        PendingCheckout.objects.create(
            razorpay_order_id=razorpay_order_id,
            user=request.user,
            amount=int(total * 100),
            snapshot=snapshot,
        )

        return Response(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_key": settings.RAZORPAY_KEY_ID,
                "amount": int(total * 100),
                "currency": "INR",
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
            pending = PendingCheckout.objects.get(
                razorpay_order_id=d["razorpay_order_id"], user=request.user
            )
        except PendingCheckout.DoesNotExist:
            return Response(
                {"error": "Checkout session not found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Idempotency: if this payment was already fulfilled, return success
        existing = Order.objects.filter(
            razorpay_payment_id=d["razorpay_payment_id"]
        ).first()
        if existing:
            return Response(
                {
                    "success": True,
                    "order_number": existing.order_number,
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

        # Only NOW register the real Order (paid + confirmed)
        snap = pending.snapshot
        with transaction.atomic():
            order = Order.objects.create(
                order_number=generate_order_number(),
                user=request.user,
                recipient_name=snap["recipient_name"],
                email=snap["email"],
                phone=snap.get("phone", ""),
                address=snap["address"],
                pincode=snap.get("pincode", ""),
                city=snap.get("city", ""),
                state=snap.get("state", ""),
                payment_method="online",
                payment_status="paid",
                status="confirmed",
                subtotal=Decimal(snap["subtotal"]),
                shipping_fee=Decimal(snap["shipping_fee"]),
                total=Decimal(snap["total"]),
                razorpay_order_id=pending.razorpay_order_id,
                razorpay_payment_id=d["razorpay_payment_id"],
                razorpay_signature=d["razorpay_signature"],
                cart_item_ids=snap.get("cart_item_ids", []),
            )

            for it in snap["items"]:
                OrderItem.objects.create(
                    order=order,
                    sneaker_id=it["sneaker_id"],
                    sneaker_name=it["sneaker_name"],
                    sneaker_image=it.get("sneaker_image", ""),
                    size=it.get("size", ""),
                    quantity=it["quantity"],
                    unit_price=Decimal(it["unit_price"]),
                    line_total=Decimal(it["line_total"]),
                )

            # Decrement stock only after a verified payment
            for it in snap["items"]:
                sneaker = Sneaker.objects.select_for_update().get(id=it["sneaker_id"])
                sneaker.copies = max(sneaker.copies - it["quantity"], 0)
                sneaker.save(update_fields=["copies", "updated_at"])

            # Clear only the selected cart lines that went into this order
            if snap.get("cart_item_ids"):
                CartItem.objects.filter(
                    user=request.user, id__in=snap["cart_item_ids"]
                ).delete()

            # The pending snapshot is consumed
            pending.delete()

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
