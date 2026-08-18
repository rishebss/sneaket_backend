import razorpay
from decimal import Decimal
from django.conf import settings
from django.utils import timezone
from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from .models import Order, OrderItem, PendingCheckout
from .serializers import (
    OrderSerializer,
    CreateOrderSerializer,
    VerifyPaymentSerializer,
)
from products.models import Sneaker, CartItem
from wallet.models import Wallet, WalletTransaction
from users.models import Address

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

        # Prefer a saved address when selected (server-authoritative)
        address_id = request.data.get("address_id")
        if address_id:
            selected = Address.objects.filter(pk=address_id, user=request.user).first()
            if selected:
                data["recipient_name"] = (
                    selected.recipient_name
                    or f"{request.user.first_name} {request.user.last_name}".strip()
                )
                data["email"] = request.user.email
                data["phone"] = selected.phone
                data["address"] = selected.address
                data["pincode"] = selected.pincode
                data["city"] = selected.city
                data["state"] = selected.state

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

        payment_method = (request.data.get("payment_method") or "online").lower()

        # Build the item/price snapshot (shared by both payment paths)
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

        # ---- Wallet payment: pay immediately from wallet balance ----
        if payment_method == "wallet":
            wallet, _ = Wallet.objects.get_or_create(user=request.user)

            with transaction.atomic():
                wallet = Wallet.objects.select_for_update().get(id=wallet.id)
                if wallet.balance < total:
                    return Response(
                        {
                            "error": "Insufficient wallet balance",
                            "balance": str(wallet.balance),
                            "required": str(total),
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                order = Order.objects.create(
                    order_number=generate_order_number(),
                    user=request.user,
                    recipient_name=data["recipient_name"],
                    email=data["email"],
                    phone=data.get("phone", ""),
                    address=data["address"],
                    pincode=data.get("pincode", ""),
                    city=data.get("city", ""),
                    state=data.get("state", ""),
                    payment_method="wallet",
                    payment_status="paid",
                    status="confirmed",
                    subtotal=Decimal(snapshot["subtotal"]),
                    shipping_fee=Decimal(snapshot["shipping_fee"]),
                    total=Decimal(snapshot["total"]),
                    cart_item_ids=snapshot["cart_item_ids"],
                )

                for it in items_snapshot:
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

                # Deduct the wallet balance
                wallet.balance = wallet.balance - total
                wallet.save(update_fields=["balance", "updated_at"])
                WalletTransaction.objects.create(
                    wallet=wallet,
                    type="debit",
                    amount=total,
                    reason="purchase",
                    reference=order.order_number,
                )

                # Decrement stock only after a confirmed wallet payment
                for it in items_snapshot:
                    sneaker = Sneaker.objects.select_for_update().get(
                        id=it["sneaker_id"]
                    )
                    sneaker.copies = max(sneaker.copies - it["quantity"], 0)
                    sneaker.save(update_fields=["copies", "updated_at"])

                # Clear only the selected cart lines
                if cart_item_ids:
                    CartItem.objects.filter(
                        user=request.user, id__in=cart_item_ids
                    ).delete()

            return Response(
                {"order_number": order.order_number, "success": True},
                status=status.HTTP_201_CREATED,
            )

        # ---- Online (Razorpay) payment: deferred until signature verified ----
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


# Statuses from which a user may raise a cancellation request
CANCELABLE_STATUSES = ("confirmed", "processing", "shipped")


class RequestCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if order.status not in CANCELABLE_STATUSES:
            return Response(
                {
                    "error": "This order cannot be cancelled at its current stage",
                    "status": order.status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = "cancellation_requested"
        order.cancellation_requested_at = timezone.now()
        reason = (request.data.get("reason") or "").strip()
        if reason:
            order.cancellation_reason = reason
        order.save(
            update_fields=[
                "status",
                "cancellation_requested_at",
                "cancellation_reason",
                "updated_at",
            ]
        )
        return Response({"success": True, "status": order.status})


class ApproveCancelView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if order.status != "cancellation_requested":
            return Response(
                {"error": "No pending cancellation request for this order"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            # Restock the sold inventory exactly once
            for item in order.items.select_related("sneaker").select_for_update():
                sneaker = Sneaker.objects.select_for_update().get(id=item.sneaker_id)
                sneaker.copies = sneaker.copies + item.quantity
                sneaker.save(update_fields=["copies", "updated_at"])

            order.status = "cancellation_approved"
            order.cancellation_approved_at = timezone.now()

            order.save(
                update_fields=[
                    "status",
                    "cancellation_approved_at",
                    "updated_at",
                ]
            )
        return Response({"success": True, "status": order.status})


class DenyCancelView(APIView):
    permission_classes = [IsAdminUser]

    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if order.status != "cancellation_requested":
            return Response(
                {"error": "No pending cancellation request for this order"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = "confirmed"
        order.cancellation_reason = ""
        order.save(update_fields=["status", "cancellation_reason", "updated_at"])
        return Response({"success": True, "status": order.status})


class RefundToWalletView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number, user=request.user)
        except Order.DoesNotExist:
            return Response(
                {"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND
            )

        if order.status == "refunded":
            return Response({"success": True, "already_refunded": True})

        if order.status != "cancellation_approved":
            return Response(
                {"error": "This order is not approved for a refund yet"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            wallet, _ = Wallet.objects.get_or_create(user=order.user)
            wallet = Wallet.objects.select_for_update().get(id=wallet.id)
            wallet.balance = wallet.balance + order.total
            wallet.save(update_fields=["balance", "updated_at"])

            WalletTransaction.objects.create(
                wallet=wallet,
                type="credit",
                amount=order.total,
                reason="refund",
                reference=order.order_number,
            )

            order.status = "refunded"
            order.payment_status = "refunded"
            order.save(update_fields=["status", "payment_status", "updated_at"])

        return Response({"success": True, "amount": str(order.total)})
