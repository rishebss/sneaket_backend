import json
import os

import requests
from django.utils import timezone
from rest_framework import status, permissions, throttling
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import ChatMessage

MAX_HISTORY = 12
MAX_MESSAGE_LENGTH = 2000


class ChatView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = "ai_chat"

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------
    def _get_cart_context(self, user):
        from products.models import CartItem

        cart_items = CartItem.objects.filter(user=user).select_related("sneaker")
        items = [
            {
                "name": ci.sneaker.name,
                "brand": ci.sneaker.brand,
                "size": ci.size,
                "qty": ci.quantity,
                "price": str(ci.sneaker.price),
                "line_total": str(float(ci.sneaker.price) * ci.quantity),
            }
            for ci in cart_items
        ]
        total = sum(float(ci.sneaker.price) * ci.quantity for ci in cart_items)
        return {
            "cart_count": len(items),
            "cart_total": round(total, 2),
            "cart_items": items,
        }

    def _get_reward_context(self, user):
        from wallet.models import Wallet

        profile = getattr(user, "profile", None)
        wallet, _ = Wallet.objects.get_or_create(user=user)
        today = timezone.localdate()
        reward_claimed = bool(profile and profile.last_reward_date == today)
        return {
            "daily_reward_claimed": reward_claimed,
            "wallet_balance": str(wallet.balance),
        }

    def _get_order_context(self, user):
        from orders.models import Order

        orders = Order.objects.filter(user=user)[:3]
        data = [
            {
                "order_number": o.order_number,
                "status": o.status,
                "total": str(o.total),
                "items": o.items.count(),
                "created_at": o.created_at.strftime("%Y-%m-%d"),
            }
            for o in orders
        ]
        return {"recent_orders": data}

    def _get_new_products_context(self):
        from products.models import Sneaker

        sneakers = Sneaker.objects.filter(is_active=True).order_by("-created_at")[:5]
        data = [
            {
                "name": s.name,
                "brand": s.brand,
                "category": s.category,
                "price": str(s.price),
                "features": s.features,
            }
            for s in sneakers
        ]
        return {"new_products": data}

    def _build_context(self, user):
        return {
            "user_name": user.get_full_name() or user.username,
            **self._get_cart_context(user),
            **self._get_reward_context(user),
            **self._get_order_context(user),
            **self._get_new_products_context(),
        }

    # Keyword -> in-app route. Lets the drawer render an "Open page" button.
    REDIRECT_RULES = [
        (("order", "orders", "purchase", "purchase history"), {"label": "View Orders", "path": "/accounts?tab=orders"}),
        (("cart", "basket", "checkout"), {"label": "View Cart", "path": "/cart"}),
        (("wallet", "balance", "reward", "rewards", "daily reward", "cash"), {"label": "Open Wallet", "path": "/accounts?tab=wallet"}),
        (("product", "sneaker", "sneakers", "new arrival", "arrivals", "browse", "shop"), {"label": "Browse Products", "path": "/products"}),
        (("favorite", "wishlist", "saved"), {"label": "View Favorites", "path": "/favorites"}),
        (("profile", "account", "settings", "address"), {"label": "Open Account", "path": "/accounts?tab=settings"}),
    ]

    def _detect_redirect(self, text):
        text = (text or "").lower()
        for keywords, target in self.REDIRECT_RULES:
            if any(k in text for k in keywords):
                return target
        return None

    def _build_system_prompt(self, context):
        return (
            "You are SNEAKET AI, the official assistant for SNEAKET, a sneaker e-commerce app. "
            "Help users with product recommendations, cart inquiries, order status, daily login rewards, "
            "and general shopping help. Be concise, friendly, and use the provided context to personalize answers. "
            "When recommending products, prefer real items from the context. "
            "If you lack the data to answer something, say you can't check that right now.\n\n"
            "Formatting rules:\n"
            "- Keep replies short and scannable; avoid long preambles.\n"
            "- Use Markdown: **bold** for labels, '-' bullets for lists, numbered lists for steps.\n"
            "- For orders, show each as: '**#OrderNumber** — Date · Total · Status (N items)' on one line, one per bullet.\n"
            "- Never dump raw JSON to the user.\n\n"
            f"Live user context (JSON):\n{json.dumps(context, indent=2)}"
        )

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------
    def get(self, request):
        """Return recent chat history for the drawer to persist conversations."""
        history = ChatMessage.objects.filter(user=request.user).order_by("-created_at")[:30]
        history = reversed(list(history))
        data = [{"role": m.role, "content": m.content} for m in history]
        return Response({"messages": data})

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response(
                {"error": "Message is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(message) > MAX_MESSAGE_LENGTH:
            return Response(
                {"error": f"Message must be under {MAX_MESSAGE_LENGTH} characters"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Prior turns as conversation history (oldest -> newest)
        history = ChatMessage.objects.filter(user=request.user).order_by("-created_at")[
            :MAX_HISTORY
        ]
        history = reversed(list(history))
        messages = [{"role": m.role, "content": m.content} for m in history]
        messages.append({"role": "user", "content": message})

        ChatMessage.objects.create(user=request.user, role="user", content=message)

        context = self._build_context(request.user)
        system_prompt = self._build_system_prompt(context)

        ai_endpoint = "https://tes-ai.revetleafing123.workers.dev"
        if not ai_endpoint:
            reply = (
                "AI assistant is not configured. Please set CLOUDFLARE_AI_ENDPOINT "
                "in backend settings."
            )
        else:
            payload = {"systemPrompt": system_prompt, "messages": messages}
            model = os.environ.get("CLOUDFLARE_AI_MODEL")
            if model:
                payload["model"] = model

            reply = None
            # Retry transient upstream failures (Cloudflare AI intermittently
            # returns 5xx). A single retry smooths over the flakiness.
            for _ in range(2):
                try:
                    resp = requests.post(ai_endpoint, json=payload, timeout=30)
                    if resp.status_code >= 500:
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    if data.get("error"):
                        reply = (
                            "Sorry, the AI assistant is temporarily unavailable. "
                            "Please try again later."
                        )
                    else:
                        reply = data.get("reply") or "Sorry, I couldn't generate a response."
                    break
                except Exception:
                    continue

            if reply is None:
                reply = (
                    "Sorry, I encountered an error connecting to the assistant. "
                    "Please try again later."
                )

        ChatMessage.objects.create(user=request.user, role="assistant", content=reply)

        redirect = self._detect_redirect(message)

        return Response({"reply": reply, "context": context, "redirect": redirect})
