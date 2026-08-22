import json
import re
import urllib.parse

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone
from rest_framework import status, permissions, throttling
from rest_framework.response import Response
from rest_framework.views import APIView
from products.models import Sneaker

from . import tools

MAX_HISTORY = 12
MAX_MESSAGE_LENGTH = 2000
MAX_TOOL_ITERATIONS = 4  # bound model<->tool round trips per chat turn
# (search -> refine -> act -> final summary)

# Signed, short-lived token carrying a pending (gated) action to its confirm step.
CONFIRM_SALT = "ai-confirm-action"


class ChatView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = "ai_chat"

    # ------------------------------------------------------------------
    # Context builders
    # ------------------------------------------------------------------
    def _get_cart_context(self, user):
        from products.models import CartItem

        today = timezone.localdate()
        cart_items = CartItem.objects.filter(user=user).select_related("sneaker")
        items = [
            {
                "name": ci.sneaker.name,
                "brand": ci.sneaker.brand,
                "size": ci.size,
                "qty": ci.quantity,
                "price": str(ci.sneaker.price),
                "line_total": str(float(ci.sneaker.price) * ci.quantity),
                "added_at": ci.created_at.strftime("%Y-%m-%d"),
                "days_in_cart": (today - ci.created_at.date()).days,
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

    # Keyword -> in-app route. Lets the drawer render a "Browse/Open" button.
    # Product intent is dynamic: the model extracts filter params and the
    # backend assembles + whitelists the URL (the model never builds raw URLs).
    REDIRECT_RULES = [
        (
            ("order", "orders", "purchase", "purchase history"),
            {"label": "View Orders", "path": "/accounts?tab=orders", "type": "static"},
        ),
        (
            ("cart", "basket"),
            {"label": "View Cart", "path": "/cart", "type": "static"},
        ),
        (
            ("wallet", "balance", "reward", "rewards", "daily reward", "cash"),
            {"label": "Open Wallet", "path": "/accounts?tab=wallet", "type": "static"},
        ),
        (
            (
                "product",
                "sneaker",
                "sneakers",
                "new arrival",
                "arrivals",
                "browse",
                "shop",
            ),
            {"label": "Browse Products", "type": "products"},
        ),
        (
            ("favorite", "wishlist", "saved"),
            {"label": "View Favorites", "path": "/favorites", "type": "static"},
        ),
        (
            ("profile", "account", "settings", "address"),
            {
                "label": "Open Account",
                "path": "/accounts?tab=settings",
                "type": "static",
            },
        ),
    ]

    # Allowed product-filter values (canonical form) for URL validation.
    _BRANDS = {b.lower(): b for b, _ in Sneaker.BRAND_CHOICES}
    _CATEGORIES = {c.lower(): c for c, _ in Sneaker.CATEGORY_CHOICES}
    _FEATURES = {f.lower(): f for f, _ in Sneaker.FEATURE_CHOICES}

    @staticmethod
    def _kw_match(text, keyword):
        # Word-boundary match so 'account' inside another word, or a passing
        # mention, doesn't fire a navigational button. Handles whitespace and
        # common punctuation around the keyword.
        return bool(
            re.search(r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])", text)
        )

    def _detect_redirect(self, message):
        text = (message or "").lower()
        for keywords, rule in self.REDIRECT_RULES:
            if any(self._kw_match(text, k) for k in keywords):
                if rule.get("type") == "products":
                    return self._build_product_redirect(message, rule["label"])
                return {"label": rule["label"], "path": rule["path"]}
        generic = (
            "shoe",
            "shoes",
            "sneaker",
            "sneakers",
            "kicks",
            "trainers",
        )
        if (
            self._mentions_value(text, self._BRANDS)
            or self._mentions_value(text, self._CATEGORIES)
            or self._mentions_value(text, self._FEATURES)
            or any(self._kw_match(text, w) for w in generic)
        ):
            return self._build_product_redirect(message, "Browse Products")
        return None

    @staticmethod
    def _mentions_value(text, mapping):
        for key in mapping:
            kw = key.replace("_", " ")
            if ChatView._kw_match(text, kw) or ChatView._kw_match(text, key):
                return True
        return False

    def _extract_product_params(self, message):
        text = (message or "").lower()
        params = {"brand": None, "category": None, "feature": None, "search": None}
        for key, val in self._BRANDS.items():
            if key.replace("_", " ") in text or key in text:
                params["brand"] = val
                break
        for key, val in self._CATEGORIES.items():
            if key.replace("_", " ") in text or key in text:
                params["category"] = val
                break
        for key, val in self._FEATURES.items():
            if key.replace("_", " ") in text or key in text:
                params["feature"] = val
                break
        return params

    def _build_product_redirect(self, message, label):
        params = self._extract_product_params(message)
        query = {}
        if params.get("brand"):
            b = str(params["brand"]).strip().lower().replace(" ", "_").replace("-", "_")
            if b in self._BRANDS:
                query["brand"] = self._BRANDS[b]
        if params.get("category"):
            c = (
                str(params["category"])
                .strip()
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
            )
            if c in self._CATEGORIES:
                query["category"] = self._CATEGORIES[c]
        if params.get("feature"):
            f = (
                str(params["feature"])
                .strip()
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
            )
            if f in self._FEATURES:
                query["feature"] = self._FEATURES[f]
        if params.get("search"):
            s = str(params["search"]).strip()[:50]
            if s:
                query["search"] = s
        if query:
            path = "/products?" + urllib.parse.urlencode(query)
            bits = [query.get("brand"), query.get("category"), query.get("feature")]
            bits = [b for b in bits if b]
            if bits:
                label = "Browse " + " ".join(b.capitalize() for b in bits)
        else:
            path = "/products"
        return {"label": label, "path": path}

    # ------------------------------------------------------------------
    # Tool-result loop helpers
    # ------------------------------------------------------------------
    def _slim_tool_payload(self, ui):
        """Compact JSON of a tool's ui payload, fed back to the model as a
        role:'tool' message so it can reason over (and refine) the result."""
        keep = {
            "id",
            "name",
            "brand",
            "category",
            "silhouette",
            "price",
            "original_price",
            "description",
            "features",
            "size",
            "quantity",
            "line_total",
            "total",
            "order_number",
            "status",
            "created_at",
            "delivery_date",
            "balance",
            "daily_reward_claimed",
        }

        def slim(d):
            if isinstance(d, list):
                return [slim(x) for x in d]
            if isinstance(d, dict):
                return {k: v for k, v in d.items() if k in keep}
            return d

        if not isinstance(ui, dict):
            return "{}"
        return json.dumps(
            {"type": ui.get("type"), "data": slim(ui.get("data"))}, default=str
        )

    def _build_system_prompt(self, context):
        return (
            "You are SNEAKET AI, the official assistant for SNEAKET, a sneaker e-commerce app. "
            "Help users with product recommendations, cart inquiries, order status, daily login rewards, "
            "and general shopping help. Be concise, friendly, and use the provided context to personalize answers. "
            "Each cart item in the context includes `added_at` and `days_in_cart` — use these to answer 'how long has X been in my cart' or similar questions rather than saying you don't know. "
            "You are SNEAKET AI, a friendly, CHATTY sneaker stylist — not a search box. "
            "Be conversational and personable: share a quick opinion or insight, then ASK a natural follow-up question "
            "to keep the dialogue going (e.g. 'want me to check our collection?', 'any brand you prefer?', 'what's your budget?'). "
            "You are also an AUTONOMOUS agent: think through the user's intent using your own sneaker knowledge, then use the "
            "tools to VERIFY against the live catalog before answering. Do not wait to be told how to interpret a request.\n\n"
            "CONVERSATION STYLE:\n"
            "- For open-ended or advice questions (e.g. 'best sneaker for a 5'7 guy', 'what suits me?'), do NOT immediately call a "
            "tool and dump a product list. First reply with a brief, friendly insight or opinion and ASK a follow-up question. "
            "Only search the catalog once the user shows interest or says yes — then present a curated set of 2-4 picks with a short "
            "reason each (e.g. 'these have thick soles that add height').\n"
            "- Keep replies short and human; end advice turns with a question so the user wants to continue.\n"
            "- Never force a single forced result — if you search, aim to show a small relevant set, and broaden the search term "
            "(e.g. 'Air Max', 'platform', high-top silhouette) rather than one specific product name.\n\n"
            "If you lack the data to answer something, say you can't check that right now.\n\n"
            "ACTION TOOLS: You can perform actions for the user by calling the provided tools. "
            "Use a tool whenever the user wants to: view their cart, view/list/track orders, see new products, "
            "check their wallet, claim the daily reward, add or remove a cart item, cancel an order, refund an "
            "order to their wallet, remove order(s) from their list, or checkout. "
            "Pass accurate arguments — use REAL order numbers from the context, never invent them. "
            "For 'cancel' or 'checkout' the system will ask the user to confirm before executing; you may still call the tool. "
            "When the user wants to CHECKOUT, PLACE, or CONFIRM their cart order — e.g. 'checkout', 'place my order', "
            "'confirm my order', 'buy my cart' — you MUST call the `checkout` tool (do not just describe the cart or only "
            "show a View Cart link). If they didn't specify a payment method, call `checkout` without payment_method "
            "(it defaults to online); the confirm step lets them cancel. "
            "For free-form questions with no clear action, just reply conversationally. "
            "NEVER reply with 'I don't have the necessary tools' or 'I can't assist with that' — always attempt a helpful answer using the tools and context you have.\n\n"
            "PRODUCT RECOMMENDATIONS:\n"
            "- When the user asks for sneaker suggestions, 'any other', 'more options', or alternatives, call "
            "`list_new_products` or `search_products` and present what they return — do NOT answer 'I can't check that right now'.\n"
            "- For any filtered request (brand, category, feature, 'low/mid/high top', in-stock, or text like "
            "'red nike'), use `search_products` with the relevant filters — never answer from memory.\n"
            "- Only recommend products returned by the tools or the live context; never invent products.\n"
            "- Do not assert attributes the catalog data does not contain. If the user filters by something the "
            "catalog can't express, say so AND show the available options instead — never refuse.\n"
            "- You have general sneaker knowledge (e.g. thick/Platform/chunky soles and high-tops add height, certain "
            "silhouettes suit certain uses). For subjective or physical requests the catalog can't directly filter "
            "(e.g. 'boost my height', 'make me look taller', 'best for wide feet'), DO NOT refuse. Instead: "
            "(1) reason about which styles fit from your own knowledge, then (2) SEARCH the catalog with "
            "`search_products` using concrete terms (product names like 'Run Star Hike', brands, 'platform', 'chunky', "
            "silhouette) to verify what's actually available, and (3) present only the matches with a short explanation "
            "of why each fits the user's intent. Never invent products — only show what the search returns.\n"
            "- HEIGHT ADVICE (get this right, do not contradict yourself): Thick/chunky/'Platform' soles and high-top "
            "silhouettes ADD visual height. Recommend them when the user wants to look TALLER or says they are on the "
            "SHORTER side. If the user is already TALL, or wants a sleek/streamlined look, recommend LOW-PROFILE, "
            "minimal-soled, low-top sneakers — NEVER suggest platform/chunky soles to someone who wants to avoid looking "
            "taller. Pair the silhouette advice with the height direction consistently.\n"
            "- When the user mentions a BUDGET ('budget 2k', 'under 2000', 'around 1.5k'), call `search_products` with "
            "`max_price` set to the numeric INR amount (convert '2k' -> 2000, '1.5k' -> 1500). Do NOT put the budget in the "
            "free-text `search` field — that matches nothing. Combine with brand/silhouette if the user gave those too.\n"
            "- When adding to cart, always pass `size` for multi-size products (the tool will ask if you omit it). "
            "For add/remove cart calls pass the EXACT full product name exactly as it appeared in search results or context (e.g. 'Blazer Mid 77 Red') — never paraphrase, shorten, or add model codes. "
            "COLORWAYS ARE SEPARATE PRODUCTS: each colorway is its own catalog row with the color IN its name ('Blazer Mid 77 Red' ≠ 'Nike mid - blazzers 77'). "
            "When the user asks for a specific colour, search_products with that colour term first, then use the matching product's exact name — NEVER add a differently-named product and claim it's the colour they asked for.\n\n"
            "TOOL RESULT LOOP: after you call a read/search tool you receive its raw JSON result as a 'tool' message — READ it and reason over it. "
            "Judge fit yourself from names/descriptions/features (the catalog has NO 'chunky'/'platform'/'height' field; silhouette is often empty, so "
            "infer from words like 'platform', 'chunky', 'thick sole', 'high-top' in product text). "
            "If results don't fit the request, call search_products AGAIN with broader/different terms (drop silhouette/category, shorten the term, keep brand/budget) instead of settling. "
            "If a search returns ZERO matches, do NOT tell the user you found nothing — retry ONCE with broader filters; only after the broadened retry also fails may you say so and offer alternatives. "
            "Your FINAL reply must reference the specific products you settled on by name and why each fits — grounded in tool results, never generic filler. "
            "SCREEN EVERY PICK: drop any returned product whose name/description contradicts what the user asked for (e.g. never include a 'platform'/'chunky' shoe in a low-profile request). "
            "If truly nothing fits, say so honestly and suggest the closest alternatives.\n"
            "GATED ACTIONS: for cancel_order / checkout / refund_order / remove_orders do NOT ask the user to confirm in your reply and don't summarize-and-wait — "
            "call the tool IMMEDIATELY; SNEAKET shows Confirm/Cancel buttons automatically.\n"
            "NEVER CLAIM AN ACTION HAPPENED unless a tool result message confirmed it: never say 'added to your cart', 'order placed', 'cancelled' etc. "
            "before the corresponding tool has actually run — if you still need info (like size), ask for it, then call the tool once you have it.\n"
            "ORDER LIFECYCLE (follow strictly):\n"
            "- 'cancellation_requested': tell the user the team is reviewing; nothing else to do yet.\n"
            "- 'cancellation_approved': proactively OFFER the refund — e.g. 'Good news, it's approved! Want me to refund ₹X to your wallet?' — and wait for their yes before calling refund_order.\n"
            "- 'refunded': only NOW may you offer remove_orders to tidy up the order list. NEVER remove an order before it has been refunded.\n"
            "To check any of these statuses use view_order or track_order with the order number first — never guess from memory.\n"
            "SEARCH TRIGGERS: whenever the user names any brand or budget — even tentatively ('maybe adidas', 'around 1.5k') — call search_products with those filters rather than answering from memory.\n"
            "QUERY HYGIENE (use your intelligence BEFORE calling tools): users type fast and messy ('check teh if nike midblazzer 77 mid red available'). "
            "Translate, don't copy: fix typos ('teh'->'the', 'midblazzer'->'blazer', 'comdy cush'->'comfy cush'), split the request into intent + attributes "
            "(brand='nike', model='blazer mid 77', colour='red', intent=availability), then build CLEAN tool arguments — the most distinctive 1-3 words as `search` "
            "(e.g. 'blazer red') plus structured filters (brand/category/silhouette/max_price). NEVER paste the raw user sentence into `search`. "
            "For 'is X available / do you have X' questions set in_stock=true and answer strictly from what the search returns — say it's unavailable if nothing matches.\n\n"
            "Formatting rules:\n"
            "- Keep replies short and scannable; avoid long preambles.\n"
            "- Use Markdown: **bold** for labels, '-' bullets for lists, numbered lists for steps.\n"
            "- Never dump raw JSON to the user.\n\n"
            f"Live user context (JSON):\n{json.dumps(context, indent=2)}"
        )

    # ------------------------------------------------------------------
    # Confirm-token helpers (for gated actions)
    # ------------------------------------------------------------------
    @staticmethod
    def _make_confirm_token(tool, args):
        return signing.dumps({"tool": tool, "args": args}, salt=CONFIRM_SALT)

    @staticmethod
    def _load_confirm_token(token):
        return signing.loads(token, salt=CONFIRM_SALT, max_age=300)

    def _confirm_prompt(self, name, args):
        if name == "add_to_cart":
            size = args.get("size")
            qty = args.get("quantity") or 1
            extra = (
                f" (US {size}, x{qty})"
                if size
                else (" (x1)" if qty == 1 else f" (x{qty})")
            )
            return (
                f"Add **{args.get('product_query')}**{extra} to your cart? "
                "Tap Confirm to proceed."
            )
        if name == "remove_from_cart":
            return f"Remove **{args.get('product_query')}** from your cart? Tap Confirm to proceed."
        if name == "claim_daily_reward":
            return "Claim your **₹25 daily reward**? Tap Confirm to proceed."
        if name == "cancel_order":
            return f"Are you sure you want to cancel order **{args.get('order_number')}**? Tap Confirm to proceed (you can tap Cancel to stop)."
        if name == "checkout":
            pm = args.get("payment_method", "online")
            return f"Confirm checkout and pay via **{pm}**? Tap Confirm to place your order."
        if name == "refund_order":
            return f"Refund order **{args.get('order_number')}** to your wallet? Tap Confirm to credit the amount."
        if name == "remove_orders":
            on = args.get("order_number")
            if on and on.lower() != "all":
                return (
                    f"Remove order **{on}** from your list? Tap Confirm to delete it."
                )
            return (
                "Remove all your refunded/cancelled orders? Tap Confirm to delete them."
            )
        return "Are you sure you want to proceed?"

    # ------------------------------------------------------------------
    # Worker call (returns full JSON dict or None)
    # ------------------------------------------------------------------
    def _call_worker(self, system_prompt, messages, model=None, tool_list=None):
        ai_endpoint = getattr(settings, "CLOUDFLARE_AI_ENDPOINT", "")
        if not ai_endpoint:
            return None
        payload = {"systemPrompt": system_prompt, "messages": messages}
        if model:
            payload["model"] = model
        if tool_list:
            payload["tools"] = tool_list
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        # Shared-secret guard: only attach the header if configured. The worker
        # enforces X-Internal-Key only when AI_WORKER_SECRET is set on its env,
        # so both sides must carry the same value to activate the guard.
        secret = getattr(settings, "AI_WORKER_SECRET", "")
        if secret:
            headers["X-Internal-Key"] = secret
        # Retry transient upstream failures (Cloudflare AI intermittently
        # returns 5xx). A single retry smooths over the flakiness.
        for _ in range(2):
            try:
                resp = requests.post(
                    ai_endpoint, json=payload, headers=headers, timeout=30
                )
                if resp.status_code >= 500:
                    continue
                resp.raise_for_status()
                data = resp.json()
                if data.get("error"):
                    return None
                return data
            except Exception:
                continue
        return None

    @staticmethod
    def _sanitize_history(history):
        turns = []
        if isinstance(history, list):
            for m in history:
                if (
                    isinstance(m, dict)
                    and m.get("role") in ("user", "assistant")
                    and m.get("content")
                ):
                    turns.append({"role": m["role"], "content": str(m["content"])})
        return turns

    def post(self, request):
        # ---- Confirm step (gated action executed after user approval) ----
        confirm_token = request.data.get("confirm_token")
        if confirm_token:
            try:
                payload = self._load_confirm_token(confirm_token)
            except Exception:
                return Response(
                    {
                        "error": "Confirmation expired or invalid. Please repeat your request."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            result = tools.execute_tool(payload["tool"], payload["args"], request.user)
            return Response(
                {
                    "reply": result["reply"],
                    "ui": result["ui"],
                    "redirect": result["redirect"],
                }
            )

        # ---- Summarization mode (no DB, no `message` required) ----
        if request.data.get("summarize"):
            turns = self._sanitize_history(request.data.get("history") or [])
            if not turns:
                return Response({"summary": ""})
            summary_prompt = (
                "You are a conversation summarizer for a sneaker e-commerce assistant. "
                "Compress the following conversation into at most 5 concise bullet "
                "points capturing durable facts, user preferences, and any open "
                "questions. Output only the bullets, no preamble."
            )
            data = self._call_worker(summary_prompt, turns)
            summary = (data.get("reply") if isinstance(data, dict) else None) or ""
            return Response({"summary": summary})

        # ---- Normal chat / agent mode ----
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

        turns = self._sanitize_history(request.data.get("history") or [])
        messages = turns[-MAX_HISTORY:] + [{"role": "user", "content": message}]

        context = self._build_context(request.user)
        system_prompt = self._build_system_prompt(context)

        # Agent loop: let the model call tools, execute non-gated ones, and
        # feed their raw results back so it can reason over / refine them.
        convo = list(messages)
        last_result = None  # last executed tool result {reply, ui, redirect}
        reply = None

        for _ in range(MAX_TOOL_ITERATIONS):
            data = self._call_worker(system_prompt, convo, None, tools.TOOLS)
            if data is None:
                break
            tool_calls = data.get("tool_calls")
            if not tool_calls:
                reply = data.get("reply")
                break
            tc = tool_calls[0]
            name = tc.get("name")
            args = tc.get("arguments") or {}
            if name in tools.GATED_TOOLS:
                # Gated actions never loop — the confirm-token flow returns now.
                # Checkout without a chosen payment method: offer both Wallet
                # and Online as separate confirm tokens so the user picks at
                # the confirmation step instead of defaulting blindly.
                if name == "checkout" and not args.get("payment_method"):
                    tok_wallet = self._make_confirm_token(
                        name, {**args, "payment_method": "wallet"}
                    )
                    tok_online = self._make_confirm_token(
                        name, {**args, "payment_method": "online"}
                    )
                    return Response(
                        {
                            "reply": "How would you like to pay for this order?",
                            "action": {
                                "type": "checkout_choice",
                                "options": [
                                    {
                                        "label": "Pay with Wallet",
                                        "confirm_token": tok_wallet,
                                    },
                                    {
                                        "label": "Razorpay",
                                        "icon": "/Razorpay.svg",
                                        "confirm_token": tok_online,
                                    },
                                ],
                            },
                            "ui": None,
                            "redirect": None,
                        }
                    )
                token = self._make_confirm_token(name, args)
                return Response(
                    {
                        "reply": self._confirm_prompt(name, args),
                        "action": {"type": name, "args": args, "confirm_token": token},
                        "ui": None,
                        "redirect": None,
                    }
                )
            result = tools.execute_tool(name, args, request.user)
            last_result = result
            # Feed the raw result back; GLM accepts bare role:"tool" messages
            # (assistant tool_calls echoes are rejected by its schema).
            convo.append(
                {
                    "role": "tool",
                    "name": name,
                    "content": self._slim_tool_payload(result.get("ui")),
                }
            )

        if not reply and last_result:
            # Iteration cap or mid-loop worker failure: fall back to the last
            # executed tool result (old single-shot behaviour).
            return Response(
                {
                    "reply": last_result["reply"],
                    "ui": last_result["ui"],
                    "redirect": last_result["redirect"],
                }
            )
        if not reply:
            return Response(
                {
                    "reply": "Sorry, I encountered an error connecting to the assistant. Please try again later.",
                    "ui": None,
                    "redirect": None,
                }
            )

        # Final grounded answer: the card reflects the result the model's
        # reasoning settled on (possibly a refined search), not necessarily
        # the first call. Legacy keyword redirect only fires on pure free-form
        # turns — executors own their redirects after any tool ran.
        redirect = (
            last_result["redirect"] if last_result else self._detect_redirect(message)
        )
        payload = {"reply": reply, "redirect": redirect}
        if last_result:
            ui = last_result["ui"]
            # Don't ship an empty products card alongside an honest "nothing
            # fits" reply — the raw card would render as a blank grid.
            if (
                isinstance(ui, dict)
                and ui.get("type") == "products"
                and not (ui.get("data") or [])
            ):
                ui = None
            payload["ui"] = ui
        else:
            payload["context"] = context
        return Response(payload)
