# users/views.py
from decimal import Decimal
from django.utils import timezone
from django.db import transaction
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from wallet.models import Wallet, WalletTransaction
from .models import UserProfile, Address
from .serializers import (
    UserSerializer,
    UserUpdateSerializer,
    LoginSerializer,
    UserProfileSerializer,
    AddressSerializer,
)

DAILY_REWARD_AMOUNT = Decimal("25")


def grant_daily_reward(user):
    """Credit the user ₹25 once per local day. Returns True if rewarded."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    today = timezone.localdate()
    if profile.last_reward_date == today:
        return False
    with transaction.atomic():
        wallet, _ = Wallet.objects.get_or_create(user=user)
        wallet.balance = wallet.balance + DAILY_REWARD_AMOUNT
        wallet.save(update_fields=["balance", "updated_at"])
        WalletTransaction.objects.create(
            wallet=wallet,
            type="credit",
            amount=DAILY_REWARD_AMOUNT,
            reason="daily_reward",
            reference=f"daily-{today.isoformat()}",
        )
        profile.last_reward_date = today
        profile.save(update_fields=["last_reward_date", "updated_at"])
    return True


class RegisterView(APIView):
    """
    View for user registration (sign up)
    URL: POST /api/users/register/
    """

    permission_classes = [permissions.AllowAny]  # Anyone can register

    def post(self, request):
        # 1. Deserialize incoming data
        serializer = UserSerializer(data=request.data)

        # 2. Validate data
        if serializer.is_valid():
            # 3. Create user (serializer.create() is called automatically)
            user = serializer.save()

            # 4. Create auth token for API access
            token, created = Token.objects.get_or_create(user=user)

            # 5. Return success response
            return Response(
                {
                    "message": "User registered successfully",
                    "user": {
                        "id": user.id,
                        "username": user.username,
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                    },
                    "token": token.key,
                },
                status=status.HTTP_201_CREATED,
            )

        # 6. Return validation errors if data is invalid
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = serializer.validated_data["user"]
        login(request, user)
        token, created = Token.objects.get_or_create(user=user)

        return Response(
            {
                "message": "Login successful",
                "user": UserSerializer(user).data,
                "token": token.key,
            }
        )


class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        logout(request)
        try:
            request.user.auth_token.delete()
        except:
            pass

        return Response({"message": "Logout successful"})


class CurrentUserView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        profile = getattr(user, "profile", None)
        reward_claimed = bool(
            profile and profile.last_reward_date == timezone.localdate()
        )
        serializer = UserSerializer(user)
        return Response({**serializer.data, "daily_reward_claimed": reward_claimed})


class ClaimDailyRewardView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        claimed = grant_daily_reward(request.user)
        wallet, _ = Wallet.objects.get_or_create(user=request.user)
        return Response(
            {
                "claimed": claimed,
                "balance": str(wallet.balance),
                "already_claimed": not claimed,
            }
        )


class UpdateProfileView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request):
        user = request.user
        serializer = UserUpdateSerializer(user, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            # Keep the default (primary) address in sync with account details
            _sync_profile_to_default_address(user, user.profile)
            return Response(
                {
                    "message": "Profile updated successfully",
                    "user": UserSerializer(user).data,
                }
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")

        if not old_password or not user.check_password(old_password):
            return Response(
                {"old_password": ["Current password is incorrect."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(new_password, user)
        except Exception as e:
            return Response(
                {"new_password": list(e.messages)}, status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()

        return Response({"message": "Password changed successfully"})


class VerifyPasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        user = request.user
        old_password = request.data.get("old_password")

        if not old_password or not user.check_password(old_password):
            return Response(
                {"old_password": ["Current password is incorrect."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"verified": True})


def _sync_profile_to_default_address(user, profile):
    """Keep the user's default (primary) address in sync with account details."""
    recipient_name = f"{user.first_name} {user.last_name}".strip()
    addr_fields = {
        "recipient_name": recipient_name,
        "phone": profile.phone or "",
        "address": profile.address or "",
        "pincode": profile.pincode or "",
        "city": profile.city or "",
        "state": profile.state or "",
    }
    default_addr = Address.objects.filter(user=user, is_default=True).first()
    if default_addr:
        for k, v in addr_fields.items():
            setattr(default_addr, k, v)
        default_addr.save()
    else:
        Address.objects.create(user=user, label="Home", is_default=True, **addr_fields)


def _sync_address_to_profile(user, addr):
    """Write a default address's details back into the account profile."""
    profile = user.profile
    profile.phone = addr.phone or profile.phone
    profile.address = addr.address or profile.address
    profile.pincode = addr.pincode or profile.pincode
    profile.city = addr.city or profile.city
    profile.state = addr.state or profile.state
    profile.save(
        update_fields=["phone", "address", "pincode", "city", "state", "updated_at"]
    )
    name_parts = (addr.recipient_name or "").split()
    if name_parts:
        user.first_name = name_parts[0]
        user.last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
        user.save(update_fields=["first_name", "last_name"])


class AddressView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = Address.objects.filter(user=request.user)
        # Seed a default address from the legacy profile fields on first use
        if not qs.exists():
            profile = request.user.profile
            Address.objects.create(
                user=request.user,
                label="Home",
                recipient_name=(
                    f"{request.user.first_name} {request.user.last_name}"
                ).strip(),
                phone=profile.phone or "",
                address=profile.address or "",
                pincode=profile.pincode or "",
                city=profile.city or "",
                state=profile.state or "",
                is_default=True,
            )
            qs = Address.objects.filter(user=request.user)
        return Response(AddressSerializer(qs, many=True).data)

    def post(self, request):
        data = request.data
        make_default = bool(data.get("is_default"))
        if make_default:
            Address.objects.filter(user=request.user, is_default=True).update(
                is_default=False
            )
        addr = Address.objects.create(
            user=request.user,
            label=data.get("label", ""),
            recipient_name=data.get("recipient_name", ""),
            phone=data.get("phone", ""),
            address=data.get("address", ""),
            pincode=data.get("pincode", ""),
            city=data.get("city", ""),
            state=data.get("state", ""),
            is_default=make_default,
        )
        if addr.is_default:
            _sync_address_to_profile(request.user, addr)
        return Response(AddressSerializer(addr).data, status=status.HTTP_201_CREATED)


class AddressDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get(self, request, pk):
        return Address.objects.filter(pk=pk, user=request.user).first()

    def patch(self, request, pk):
        addr = self._get(request, pk)
        if not addr:
            return Response({"error": "Not found"}, status=404)
        data = request.data
        if "label" in data:
            addr.label = data["label"]
        if "recipient_name" in data:
            addr.recipient_name = data.get("recipient_name", "")
        if "phone" in data:
            addr.phone = data.get("phone", "")
        if "address" in data:
            addr.address = data.get("address", "")
        if "pincode" in data:
            addr.pincode = data.get("pincode", "")
        if "city" in data:
            addr.city = data.get("city", "")
        if "state" in data:
            addr.state = data.get("state", "")
        if data.get("is_default"):
            Address.objects.filter(user=request.user, is_default=True).update(
                is_default=False
            )
            addr.is_default = True
        addr.save()
        if addr.is_default:
            _sync_address_to_profile(request.user, addr)
        return Response(AddressSerializer(addr).data)

    def delete(self, request, pk):
        addr = self._get(request, pk)
        if not addr:
            return Response({"error": "Not found"}, status=404)
        was_default = addr.is_default
        addr.delete()
        if was_default:
            nxt = (
                Address.objects.filter(user=request.user)
                .order_by("-created_at")
                .first()
            )
            if nxt:
                nxt.is_default = True
                nxt.save()
        return Response({"success": True})
