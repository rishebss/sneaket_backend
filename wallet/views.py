import razorpay
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import Wallet, WalletTransaction, WalletTopUp
from .serializers import (
    WalletSerializer,
    AddMoneySerializer,
    VerifyAddMoneySerializer,
)

razorpay_client = razorpay.Client(
    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
)


def get_wallet(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


class WalletView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        wallet = get_wallet(request.user)
        return Response(WalletSerializer(wallet).data)


class AddMoneyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AddMoneySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        amount = serializer.validated_data["amount"]
        amount_paise = int(amount * 100)

        try:
            rzp_order = razorpay_client.order.create(
                {
                    "amount": amount_paise,
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

        WalletTopUp.objects.create(
            razorpay_order_id=razorpay_order_id,
            user=request.user,
            amount=amount_paise,
        )

        return Response(
            {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_key": settings.RAZORPAY_KEY_ID,
                "amount": amount_paise,
                "currency": "INR",
            },
            status=status.HTTP_201_CREATED,
        )


class VerifyAddMoneyView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = VerifyAddMoneySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            pending = WalletTopUp.objects.get(
                razorpay_order_id=d["razorpay_order_id"], user=request.user
            )
        except WalletTopUp.DoesNotExist:
            return Response(
                {"error": "Top-up session not found"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Idempotency: same payment already credited
        existing = WalletTransaction.objects.filter(
            reference=d["razorpay_payment_id"], type="credit"
        ).first()
        if existing:
            return Response({"success": True, "already_credited": True})

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
            wallet = get_wallet(request.user)
            wallet = Wallet.objects.select_for_update().get(id=wallet.id)
            wallet.balance = wallet.balance + Decimal(pending.amount) / 100
            wallet.save(update_fields=["balance", "updated_at"])

            WalletTransaction.objects.create(
                wallet=wallet,
                type="credit",
                amount=Decimal(pending.amount) / 100,
                reason="add_money",
                reference=d["razorpay_payment_id"],
            )
            pending.delete()

        return Response({"success": True})
