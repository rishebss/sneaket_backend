"""
SNEAKET AI — tool definitions + executors.

The model (via the Cloudflare Worker) decides WHICH tool + arguments.
Django EXECUTES the tool against the database, reusing the same logic the
UI uses (OrdersView, RequestCancelView, grant_daily_reward, cart queries).
All committal actions are gated: they return a confirm_token instead of
running, and only execute when ChatView confirms the token.

Tool result shape returned to ChatView:
    { "reply": str, "ui": dict | None, "redirect": dict | None }
"""

import re
import razorpay
from decimal import Decimal
from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.db import transaction

from products.models import Sneaker, CartItem
from orders.models import Order, OrderItem, PendingCheckout
from wallet.models import Wallet, WalletTransaction
from users.models import Address
from users.views import grant_daily_reward
from orders.serializers import OrderSerializer
from orders.views import generate_order_number, REMOVABLE_STATUSES

# Order statuses from which a cancellation request may be raised.
CANCELABLE_STATUSES = ("confirmed", "processing", "shipped")

# Tools whose execution is deferred behind a confirmation step.
GATED_TOOLS = {"cancel_order", "checkout", "refund_order", "remove_orders"}

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI-style; Django sends these to the Worker)
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "view_cart",
            "description": "Show the user's current shopping cart: items, sizes, quantities, and total.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_orders",
            "description": "List the user's recent orders with status and totals.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max orders to return (default 5)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_order",
            "description": "Show full details of one order by its order number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {
                        "type": "string",
                        "description": "The order number, e.g. SNEK-20250820-12345",
                    }
                },
                "required": ["order_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "track_order",
            "description": "Show the shipping/delivery status of one order by order number.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {
                        "type": "string",
                        "description": "The order number, e.g. SNEK-20250820-12345",
                    }
                },
                "required": ["order_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_new_products",
            "description": "List the newest sneakers available on SNEAKET.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max products to return (default 5)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_products",
            "description": "Search and filter the SNEAKET catalog by free-text, brand, category, feature tag, silhouette (low/mid/high top), or PRICE. Use this for product recommendations and filtered requests like 'low flat sneakers', 'red nike', 'basketball shoes', or budget limits like 'under 2k' / 'budget 2000'. Always prefer this over answering from memory. For a budget, use max_price (e.g. 2000 for '2k' or 'under 2k').",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {
                        "type": "string",
                        "description": "Free-text query matched against name, brand, and description.",
                    },
                    "brand": {
                        "type": "string",
                        "description": "Brand slug, e.g. 'nike', 'adidas', 'reebok'.",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category slug, e.g. 'lifestyle', 'basketball', 'running'.",
                    },
                    "feature": {
                        "type": "string",
                        "description": "Feature tag slug, e.g. 'new_arrival', 'best_seller', 'value_for_money'.",
                    },
                    "silhouette": {
                        "type": "string",
                        "enum": ["low", "mid", "high"],
                        "description": "Silhouette: 'low' (low top), 'mid' (mid top), 'high' (high top).",
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum price in INR. For '2k' / 'under 2k' pass 2000. Filters out products above this.",
                    },
                    "min_price": {
                        "type": "number",
                        "description": "Minimum price in INR.",
                    },
                    "in_stock": {
                        "type": "boolean",
                        "description": "If true, only return products with stock.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max products to return (default 8).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_wallet",
            "description": "Show wallet balance and whether today's daily reward is claimed.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claim_daily_reward",
            "description": "Claim the user's daily login reward (₹25), if not already claimed today.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_cart",
            "description": "Add a product to the user's cart. Resolve the product from a name/brand query. IMPORTANT: always pass `size` when the product has multiple sizes. If `size` is omitted for a product with several sizes, the tool will ask the user which size to pick instead of guessing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_query": {
                        "type": "string",
                        "description": "Product name or brand, e.g. 'nike air zoom'",
                    },
                    "size": {
                        "type": "string",
                        "description": "US size, e.g. '9'. Required when the product has multiple sizes; omit only for single-size products.",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Quantity (default 1)",
                    },
                },
                "required": ["product_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_cart",
            "description": "Remove a product from the user's cart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_query": {
                        "type": "string",
                        "description": "Product name or brand to remove",
                    }
                },
                "required": ["product_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Request cancellation of an order. REQUIRES user confirmation before executing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {
                        "type": "string",
                        "description": "The order number to cancel",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional cancellation reason",
                    },
                },
                "required": ["order_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "checkout",
            "description": "Checkout the selected cart items and place the order. REQUIRES user confirmation (the system shows a Confirm/Cancel button before any charge). payment_method is 'wallet' or 'online' — if the user didn't say, omit it and the system defaults to 'online' (Razorpay).",
            "parameters": {
                "type": "object",
                "properties": {
                    "payment_method": {
                        "type": "string",
                        "enum": ["wallet", "online"],
                        "description": "How to pay: 'wallet' (instant, charged from balance) or 'online' (Razorpay). Optional — defaults to 'online'.",
                    },
                    "address_id": {
                        "type": "integer",
                        "description": "Saved address id to ship to (optional)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refund_order",
            "description": "Refund an order's paid amount back to the user's wallet whenever the user asks to refund an order. REQUIRES user confirmation before executing. The system verifies the order is eligible and reports back if it isn't ready for a refund yet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {
                        "type": "string",
                        "description": "The order number to refund, e.g. SNEK-20250820-12345",
                    }
                },
                "required": ["order_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_orders",
            "description": "Remove order(s) from the user's order list. Use when the user asks to delete/remove an order after a refund or cancellation — either a specific order by order number, or ALL refunded/cancelled orders when they say 'remove all my orders'. Only refunded or cancelled orders can be removed. REQUIRES user confirmation before executing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {
                        "type": "string",
                        "description": "Specific order number to remove. Omit or pass 'all' to remove every refunded/cancelled order.",
                    }
                },
                "required": [],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_sneaker(query):
    """Best-effort product resolution from a free-text query."""
    if not query:
        return None
    q = query.strip()
    return (
        Sneaker.objects.filter(is_active=True)
        .filter(Q(name__icontains=q) | Q(brand__icontains=q))
        .order_by("-created_at")
        .first()
    )


def _wallet_state(user):
    wallet, _ = Wallet.objects.get_or_create(user=user)
    profile = getattr(user, "profile", None)
    today = timezone.localdate()
    claimed = bool(profile and profile.last_reward_date == today)
    return wallet, claimed


# ---------------------------------------------------------------------------
# Executors  (each returns {reply, ui, redirect})
# ---------------------------------------------------------------------------
def view_cart(user, args):
    items = CartItem.objects.filter(user=user).select_related("sneaker")
    data = [
        {
            "id": ci.sneaker.id,
            "name": ci.sneaker.name,
            "brand": ci.sneaker.brand,
            "size": ci.size or "",
            "quantity": ci.quantity,
            "price": str(ci.sneaker.price),
            "line_total": str(float(ci.sneaker.price) * ci.quantity),
            "img": ci.sneaker.image_list[0] if ci.sneaker.image_list else "",
        }
        for ci in items
    ]
    total = sum(float(ci.sneaker.price) * ci.quantity for ci in items)
    if not data:
        return {
            "reply": "Your cart is empty right now.",
            "ui": {"type": "cart", "data": {"items": [], "total": "0.00"}},
            "redirect": None,
        }
    return {
        "reply": f"You have {len(data)} item(s) in your cart, totalling ₹{round(total, 2):,.2f}.",
        "ui": {
            "type": "cart",
            "data": {"items": data, "total": f"{round(total, 2):.2f}"},
        },
        "redirect": {"label": "View Cart", "path": "/cart"},
    }


def view_orders(user, args):
    limit = min(int(args.get("limit") or 5), 20)
    orders = (
        Order.objects.filter(user=user)
        .prefetch_related("items")
        .order_by("-created_at")[:limit]
    )
    data = OrderSerializer(orders, many=True).data
    if not data:
        return {
            "reply": "You don't have any orders yet.",
            "ui": {"type": "orders", "data": []},
            "redirect": {"label": "View Orders", "path": "/accounts?tab=orders"},
        }
    return {
        "reply": f"Here are your {len(data)} most recent order(s).",
        "ui": {"type": "orders", "data": data},
        "redirect": {"label": "View Orders", "path": "/accounts?tab=orders"},
    }


def _order_detail(user, order_number):
    order = (
        Order.objects.filter(order_number=order_number, user=user)
        .prefetch_related("items")
        .first()
    )
    if not order:
        return None
    return OrderSerializer(order).data


def view_order(user, args):
    data = _order_detail(user, args.get("order_number", ""))
    if not data:
        return {
            "reply": "I couldn't find that order. Double-check the order number?",
            "ui": None,
            "redirect": None,
        }
    return {
        "reply": f"Here are the details for order **{data['order_number']}**.",
        "ui": {"type": "order_detail", "data": data},
        "redirect": None,
    }


def track_order(user, args):
    data = _order_detail(user, args.get("order_number", ""))
    if not data:
        return {
            "reply": "I couldn't find that order. Double-check the order number?",
            "ui": None,
            "redirect": None,
        }
    status = data.get("status")
    delivery = data.get("delivery_date")
    reply = f"Order **{data['order_number']}** is currently **{status}**."
    if delivery:
        reply += f" Estimated delivery: {delivery}."
    return {
        "reply": reply,
        "ui": {"type": "order_detail", "data": data},
        "redirect": None,
    }


def list_new_products(user, args):
    limit = min(int(args.get("limit") or 5), 20)
    sneakers = Sneaker.objects.filter(is_active=True).order_by("-created_at")[:limit]
    data = [_product_card(s) for s in sneakers]
    if not data:
        return {
            "reply": "No products are available right now.",
            "ui": {"type": "products", "data": []},
            "redirect": {"label": "Browse Products", "path": "/products"},
        }
    return {
        "reply": "Here are the latest drops on SNEAKET.",
        "ui": {"type": "products", "data": data},
        "redirect": {"label": "Browse Products", "path": "/products"},
    }


def _product_card(s):
    return {
        "id": s.id,
        "name": s.name,
        "brand": s.brand,
        "category": s.category,
        "silhouette": s.silhouette or "",
        "price": str(s.price),
        "original_price": str(s.original_price) if s.original_price else "",
        "img": s.image_list[0] if s.image_list else "",
        "features": s.features,
    }


# Conversational filler the model may leak into brand/search args.
_FILLER = {
    "may",
    "be",
    "maybe",
    "i",
    "think",
    "prefer",
    "want",
    "like",
    "some",
    "or",
    "and",
    "a",
    "an",
    "the",
    "to",
    "for",
    "of",
    "with",
    "from",
    "any",
    "could",
    "would",
    "please",
    "show",
    "me",
    "get",
    "need",
    "looking",
}


def _split_multi(val):
    """Split a user-style multi-value ('adidas or nike', 'red, black',
    'may be adidas or nike') into clean parts, dropping filler words."""
    if not val:
        return []
    parts = re.split(r",|/| or | and | or|and", str(val), flags=re.IGNORECASE)
    out = []
    for p in parts:
        words = [w for w in re.split(r"\s+", p.strip()) if w.lower() not in _FILLER]
        cleaned = " ".join(words).strip()
        if cleaned:
            out.append(cleaned)
    return out


def _parse_price(val):
    """Parse a price arg that may be a number, '2k', '2.5k', or '2000'."""
    if val is None:
        return None
    s = str(val).strip().lower().replace(",", "").replace("₹", "").replace("rs", "")
    if not s:
        return None
    mult = 1
    if s.endswith("k"):
        mult = 1000
        s = s[:-1].strip()
    if s.endswith("l") or s.endswith("lac"):
        mult = 100000
        s = s[:-1].replace("ac", "").strip()
    try:
        return float(s) * mult
    except ValueError:
        return None


def search_products(user, args):
    search = (args.get("search") or "").strip()
    brand = (args.get("brand") or "").strip()
    category = (args.get("category") or "").strip().lower() or None
    feature = (args.get("feature") or "").strip().lower() or None
    silhouette = (args.get("silhouette") or "").strip().lower() or None
    in_stock = bool(args.get("in_stock"))
    max_price = _parse_price(args.get("max_price"))
    min_price = _parse_price(args.get("min_price"))

    qs = Sneaker.objects.filter(is_active=True)
    search_terms = _split_multi(search)
    brand_terms = _split_multi(brand)
    if search_terms:
        q = Q()
        for t in search_terms:
            q |= (
                Q(name__icontains=t)
                | Q(brand__icontains=t)
                | Q(description__icontains=t)
            )
        qs = qs.filter(q)
    if brand_terms:
        qb = Q()
        for b in brand_terms:
            qb |= Q(brand__iexact=b) | Q(brand__icontains=b)
        qs = qs.filter(qb)
    if category:
        qs = qs.filter(category=category)
    if feature:
        qs = qs.filter(features__contains=[feature])
    if silhouette:
        qs = qs.filter(silhouette=silhouette)
    if min_price is not None:
        qs = qs.filter(price__gte=min_price)
    if max_price is not None:
        qs = qs.filter(price__lte=max_price)
    if in_stock:
        qs = qs.filter(copies__gt=0)
    sneakers = qs.order_by("-created_at")[: min(int(args.get("limit") or 8), 20)]
    data = [_product_card(s) for s in sneakers]
    if not data:
        return {
            "reply": "I couldn't find any products matching that. Try a different brand, category, or feature?",
            "ui": {"type": "products", "data": []},
            "redirect": {"label": "Browse Products", "path": "/products"},
        }
    return {
        "reply": f"Found {len(data)} matching product(s).",
        "ui": {"type": "products", "data": data},
        "redirect": {"label": "Browse Products", "path": "/products"},
    }


def view_wallet(user, args):
    wallet, claimed = _wallet_state(user)
    return {
        "reply": f"Your wallet balance is ₹{wallet.balance:,.2f}. Daily reward: {'already claimed' if claimed else 'available — say "claim my reward"'}.",
        "ui": {
            "type": "wallet",
            "data": {"balance": str(wallet.balance), "daily_reward_claimed": claimed},
        },
        "redirect": {"label": "Open Wallet", "path": "/accounts?tab=wallet"},
    }


def claim_daily_reward(user, args):
    claimed = grant_daily_reward(user)
    wallet, _ = Wallet.objects.get_or_create(user=user)
    if not claimed:
        return {
            "reply": "You've already claimed today's reward. Come back tomorrow!",
            "ui": {
                "type": "wallet",
                "data": {"balance": str(wallet.balance), "daily_reward_claimed": True},
            },
            "redirect": None,
        }
    return {
        "reply": f"Claimed! ₹25 added. Your new balance is ₹{wallet.balance:,.2f}.",
        "ui": {
            "type": "wallet",
            "data": {"balance": str(wallet.balance), "daily_reward_claimed": True},
        },
        "redirect": None,
    }


def add_to_cart(user, args):
    sneaker = _resolve_sneaker(args.get("product_query", ""))
    if not sneaker:
        return {
            "reply": "I couldn't find a product matching that. Try a brand or model name?",
            "ui": None,
            "redirect": {"label": "Browse Products", "path": "/products"},
        }
    requested_size = args.get("size")
    available = list(sneaker.available_sizes or [])
    if not requested_size:
        # Never guess a size when several are available — ask the user instead.
        if len(available) > 1:
            return {
                "reply": (
                    f"**{sneaker.name}** is available in US sizes "
                    f"{', '.join(str(s) for s in available)}. Which size would you like?"
                ),
                "ui": None,
                "redirect": {
                    "label": "View Product",
                    "path": f"/products/{sneaker.id}",
                },
            }
        size = available[0] if available else None
    else:
        size = requested_size
    qty = max(int(args.get("quantity") or 1), 1)
    if sneaker.copies < qty:
        return {
            "reply": f"Only {sneaker.copies} unit(s) of **{sneaker.name}** are in stock.",
            "ui": None,
            "redirect": None,
        }
    ci, created = CartItem.objects.update_or_create(
        user=user,
        sneaker=sneaker,
        size=size,
        defaults={"quantity": qty, "is_selected": True},
    )
    if not created:
        ci.quantity = qty
        ci.is_selected = True
        ci.save(update_fields=["quantity", "is_selected"])
    return {
        "reply": f"Added **{sneaker.name}** (US {size or 'OS'}) x{qty} to your cart.",
        "ui": {
            "type": "cart_added",
            "data": {
                "name": sneaker.name,
                "size": size,
                "quantity": qty,
                "price": str(sneaker.price),
                "img": sneaker.image_list[0] if sneaker.image_list else "",
            },
        },
        "redirect": {"label": "View Cart", "path": "/cart"},
    }


def remove_from_cart(user, args):
    sneaker = _resolve_sneaker(args.get("product_query", ""))
    if not sneaker:
        return {
            "reply": "I couldn't find that product in your cart to remove.",
            "ui": None,
            "redirect": None,
        }
    deleted, _ = CartItem.objects.filter(user=user, sneaker=sneaker).delete()
    if deleted:
        return {
            "reply": f"Removed **{sneaker.name}** from your cart.",
            "ui": None,
            "redirect": {"label": "View Cart", "path": "/cart"},
        }
    return {
        "reply": f"**{sneaker.name}** wasn't in your cart.",
        "ui": None,
        "redirect": None,
    }


def cancel_order(user, args):
    order_number = args.get("order_number", "")
    order = Order.objects.filter(order_number=order_number, user=user).first()
    if not order:
        return {
            "reply": "I couldn't find that order under your account.",
            "ui": None,
            "redirect": None,
        }
    if order.status not in CANCELABLE_STATUSES:
        return {
            "reply": f"Order **{order_number}** is currently **{order.status}** and can't be cancelled at this stage.",
            "ui": {"type": "order_detail", "data": OrderSerializer(order).data},
            "redirect": None,
        }
    reason = (args.get("reason") or "").strip()
    order.status = "cancellation_requested"
    order.cancellation_requested_at = timezone.now()
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
    return {
        "reply": f"Done — cancellation requested for order **{order_number}**. Our team will review it shortly.",
        "ui": {"type": "order_detail", "data": OrderSerializer(order).data},
        "redirect": {"label": "View Orders", "path": "/accounts?tab=orders"},
    }


def refund_order(user, args):
    """Refund an approved cancellation back to the user's wallet.

    Reuses the same credit logic as RefundToWalletView: only an order whose
    cancellation has been approved can be refunded, and the amount is credited
    once (idempotent on 'refunded' status)."""
    order_number = args.get("order_number", "")
    order = Order.objects.filter(order_number=order_number, user=user).first()
    if not order:
        return {
            "reply": "I couldn't find that order under your account.",
            "ui": None,
            "redirect": None,
        }
    if order.status == "refunded":
        return {
            "reply": f"Order **{order_number}** has already been refunded to your wallet.",
            "ui": {"type": "order_detail", "data": OrderSerializer(order).data},
            "redirect": {"label": "View Wallet", "path": "/accounts?tab=wallet"},
        }
    if order.status != "cancellation_approved":
        return {
            "reply": f"Order **{order_number}** isn't approved for refund yet (current status: **{order.status}**). Cancel it and wait for approval first.",
            "ui": {"type": "order_detail", "data": OrderSerializer(order).data},
            "redirect": None,
        }
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
    return {
        "reply": f"Refunded **₹{order.total}** from order **{order_number}** to your wallet.",
        "ui": {"type": "order_detail", "data": OrderSerializer(order).data},
        "redirect": {"label": "View Wallet", "path": "/accounts?tab=wallet"},
    }


def _remaining_orders(user):
    qs = (
        Order.objects.filter(user=user)
        .prefetch_related("items")
        .order_by("-created_at")
    )
    return OrderSerializer(qs, many=True).data


def remove_orders(user, args):
    """Remove order(s) from the user's list (mirrors RemoveOrderView). Only
    refunded/cancelled orders are removable. With no order_number (or 'all')
    every removable order is removed; otherwise a single specific order."""
    order_number = (args.get("order_number") or "").strip()
    if not order_number or order_number.lower() == "all":
        qs = Order.objects.filter(user=user, status__in=REMOVABLE_STATUSES)
        count = qs.count()
        qs.delete()
        if not count:
            return {
                "reply": "You don't have any refunded or cancelled orders to remove.",
                "ui": {"type": "orders", "data": _remaining_orders(user)},
                "redirect": {"label": "View Orders", "path": "/accounts?tab=orders"},
            }
        return {
            "reply": f"Removed {count} order(s) from your list.",
            "ui": {"type": "orders", "data": _remaining_orders(user)},
            "redirect": {"label": "View Orders", "path": "/accounts?tab=orders"},
        }

    order = Order.objects.filter(order_number=order_number, user=user).first()
    if not order:
        return {
            "reply": "I couldn't find that order under your account.",
            "ui": None,
            "redirect": None,
        }
    if order.status not in REMOVABLE_STATUSES:
        return {
            "reply": f"Order **{order_number}** can't be removed yet (status: **{order.status}**). Only refunded or cancelled orders can be removed.",
            "ui": {"type": "order_detail", "data": OrderSerializer(order).data},
            "redirect": None,
        }
    order.delete()
    return {
        "reply": f"Removed order **{order_number}** from your list.",
        "ui": {"type": "orders", "data": _remaining_orders(user)},
        "redirect": {"label": "View Orders", "path": "/accounts?tab=orders"},
    }


def checkout(user, args):
    """Executed only after confirmation. Builds the order from selected cart
    items, mirroring OrdersView, but never re-implements business rules."""
    payment_method = (args.get("payment_method") or "online").lower()
    address_id = args.get("address_id")

    address = None
    if address_id:
        address = Address.objects.filter(pk=address_id, user=user).first()
    if not address:
        address = Address.objects.filter(user=user, is_default=True).first()
    if not address:
        address = Address.objects.filter(user=user).first()
    if not address:
        return {
            "reply": "I need a shipping address first. Add one in Account -> Settings, then try checkout.",
            "ui": None,
            "redirect": {"label": "Add Address", "path": "/accounts?tab=settings"},
        }

    cart_items = list(
        CartItem.objects.filter(user=user, is_selected=True).select_related("sneaker")
    )
    if not cart_items:
        return {
            "reply": "Your cart has no items selected for checkout.",
            "ui": None,
            "redirect": {"label": "View Cart", "path": "/cart"},
        }

    insufficient = [
        {
            "sneaker": ci.sneaker.name,
            "available": ci.sneaker.copies,
            "requested": ci.quantity,
        }
        for ci in cart_items
        if ci.sneaker.copies < ci.quantity
    ]
    if insufficient:
        return {
            "reply": "Some items are out of stock for the quantity selected.",
            "ui": {"type": "stock_error", "data": insufficient},
            "redirect": {"label": "View Cart", "path": "/cart"},
        }

    subtotal = sum((ci.sneaker.price * ci.quantity for ci in cart_items), Decimal("0"))
    total = subtotal
    items_snapshot = []
    cart_item_ids = []
    for ci in cart_items:
        items_snapshot.append(
            {
                "sneaker_id": ci.sneaker_id,
                "sneaker_name": ci.sneaker.name,
                "sneaker_image": ci.sneaker.image_list[0]
                if ci.sneaker.image_list
                else "",
                "size": ci.size or "",
                "quantity": ci.quantity,
                "unit_price": str(ci.sneaker.price),
                "line_total": str(ci.sneaker.price * ci.quantity),
            }
        )
        cart_item_ids.append(ci.id)

    snapshot = {
        "recipient_name": address.recipient_name
        or f"{user.first_name} {user.last_name}".strip(),
        "email": user.email,
        "phone": address.phone,
        "address": address.address,
        "pincode": address.pincode,
        "city": address.city,
        "state": address.state,
        "subtotal": str(subtotal),
        "shipping_fee": "0",
        "total": str(total),
        "items": items_snapshot,
        "cart_item_ids": cart_item_ids,
    }

    if payment_method == "wallet":
        wallet, _ = Wallet.objects.get_or_create(user=user)
        if wallet.balance < total:
            return {
                "reply": f"Insufficient wallet balance (₹{wallet.balance:,.2f}). Pick online payment instead?",
                "ui": {
                    "type": "wallet",
                    "data": {
                        "balance": str(wallet.balance),
                        "daily_reward_claimed": False,
                    },
                },
                "redirect": None,
            }
        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(id=wallet.id)
            order = Order.objects.create(
                order_number=generate_order_number(),
                user=user,
                recipient_name=snapshot["recipient_name"],
                email=snapshot["email"],
                phone=snapshot["phone"],
                address=snapshot["address"],
                pincode=snapshot["pincode"],
                city=snapshot["city"],
                state=snapshot["state"],
                payment_method="wallet",
                payment_status="paid",
                status="confirmed",
                subtotal=subtotal,
                shipping_fee=Decimal("0"),
                total=total,
                cart_item_ids=cart_item_ids,
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
            wallet.balance = wallet.balance - total
            wallet.save(update_fields=["balance", "updated_at"])
            WalletTransaction.objects.create(
                wallet=wallet,
                type="debit",
                amount=total,
                reason="purchase",
                reference=order.order_number,
            )
            for it in items_snapshot:
                sneaker = Sneaker.objects.select_for_update().get(id=it["sneaker_id"])
                sneaker.copies = max(sneaker.copies - it["quantity"], 0)
                sneaker.save(update_fields=["copies", "updated_at"])
            CartItem.objects.filter(user=user, id__in=cart_item_ids).delete()
        return {
            "reply": f"Your order **{order.order_number}** is confirmed and paid from your wallet (₹{total:,.2f}).",
            "ui": {
                "type": "checkout_result",
                "data": {
                    "order_number": order.order_number,
                    "total": str(total),
                    "payment": "wallet",
                },
            },
            "redirect": {"label": "View Orders", "path": "/accounts?tab=orders"},
        }

    # Online (Razorpay) — create the order, hand the modal off to the frontend
    try:
        rzp = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )
        rzp_order = rzp.order.create(
            {"amount": int(total * 100), "currency": "INR", "payment_capture": 1}
        )
        razorpay_order_id = rzp_order["id"]
    except Exception as e:
        return {
            "reply": f"Payment gateway error: {str(e)}",
            "ui": None,
            "redirect": None,
        }
    PendingCheckout.objects.create(
        razorpay_order_id=razorpay_order_id,
        user=user,
        amount=int(total * 100),
        snapshot=snapshot,
    )
    return {
        "reply": f"Ready to pay ₹{total:,.2f} online. Complete the payment to confirm your order.",
        "ui": {
            "type": "razorpay",
            "data": {
                "razorpay_order_id": razorpay_order_id,
                "razorpay_key": settings.RAZORPAY_KEY_ID,
                "amount": int(total * 100),
                "currency": "INR",
            },
        },
        "redirect": None,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------
def execute_tool(name, args, user):
    args = args or {}
    handlers = {
        "view_cart": view_cart,
        "view_orders": view_orders,
        "view_order": view_order,
        "track_order": track_order,
        "list_new_products": list_new_products,
        "search_products": search_products,
        "view_wallet": view_wallet,
        "claim_daily_reward": claim_daily_reward,
        "add_to_cart": add_to_cart,
        "remove_from_cart": remove_from_cart,
        "cancel_order": cancel_order,
        "checkout": checkout,
        "refund_order": refund_order,
        "remove_orders": remove_orders,
    }
    fn = handlers.get(name)
    if not fn:
        return {
            "reply": "I don't know how to do that yet.",
            "ui": None,
            "redirect": None,
        }
    try:
        return fn(user, args)
    except Exception:
        return {
            "reply": "Something went wrong performing that action. Please try again.",
            "ui": None,
            "redirect": None,
        }
