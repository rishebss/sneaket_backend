import json
import os

import requests
from django.conf import settings
from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth.models import User
from django.contrib.auth import get_user_model

from .models import ChatMessage

User = get_user_model()


class ChatView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get_cart_context(self, user):
        from products.models import CartItem
        from products.serializers import CartItemSerializer

        cart_items = CartItem.objects.filter(user=user).select_related("sneaker")
        if not cart_items.exists():
            return {"cart_count": 0, "cart_items": []}
        serializer = CartItemSerializer(cart_items, many=True)
        return {
            "cart_count": cart_items.count(),
            "cart_items": serializer.data,
        }

    def _get_reward_context(self, user):
        from users.models import UserProfile
        from wallet.models import Wallet

        profile = getattr(user, "profile", None)
        wallet, _ = Wallet.objects.get_or_create(user=user)
        today = timezone.localdate()
        reward_claimed = bool(profile and profile.last_reward_date == today)
        return {
            "daily_reward_claimed": reward_claimed,
            "wallet_balance": str(wallet.balance),
        }

    def post(self, request):
        message = request.data.get("message", "").strip()
        if not message:
            return Response(
                {"error": "Message is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        context = request.data.get("context", {})
        if not context:
            context = {
                **self._get_cart_context(request.user),
                **self._get_reward_context(request.user),
                "user_name": request.user.get_full_name() or request.user.username,
            }

        ChatMessage.objects.create(user=request.user, role="user", content=message)

        ai_endpoint = os.environ.get(
            "CLOUDFLARE_AI_ENDPOINT",
            getattr(settings, "CLOUDFLARE_AI_ENDPOINT", ""),
        )
        if not ai_endpoint:
            reply = (
                "AI assistant is not configured. Please set CLOUDFLARE_AI_ENDPOINT in backend settings."
            )
        else:
            try:
                payload = {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a helpful assistant for SNEAKET, a sneaker e-commerce app. "
                                "You can help users with product recommendations, cart inquiries, "
                                "daily login rewards, and general shopping assistance. "
                                "Be concise, friendly, and use the provided context to personalize your answers."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"Context: {json.dumps(context)}\nUser message: {message}",
                        },
                    ]
                }
                resp = requests.post(ai_endpoint, json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                reply = data.get("reply") or data.get("message") or data.get("response") or str(data)
                if isinstance(reply, dict):
                    reply = reply.get("reply") or reply.get("message") or reply.get("response") or json.dumps(reply)
            except Exception as e:
                reply = f"Sorry, I encountered an error: {str(e)}"

        ChatMessage.objects.create(user=request.user, role="assistant", content=reply)

        return Response({"reply": reply, "context": context})
