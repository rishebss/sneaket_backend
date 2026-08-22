"""GLM-4.7-flash regression harness for the SNEAKET AI agent.

Runs real chat turns through /api/ai/chat (Django test client -> live Worker)
using throwaway users, covering Hurdles H1-H10 + persona (§1b).
Run: backend/env/Scripts/python.exe glm_retest.py  (cwd = backend/sneaket_backend)
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sneaket_backend.settings")

import django

django.setup()

import json
import time
import traceback
from datetime import timedelta

from django.utils import timezone
from django.test import Client

from django.contrib.auth import get_user_model
from products.models import Sneaker, CartItem
from orders.models import Order, OrderItem
from orders.views import generate_order_number
from wallet.models import Wallet

User = get_user_model()
OUT = []


def log(s=""):
    print(s)
    OUT.append(s)


def mkuser(i):
    uname = f"glm_retest_{i}_{int(time.time())}"
    u = User.objects.create_user(
        username=uname,
        email=f"{uname}@test.local",
        password="x",
        first_name="GLM",
        last_name=f"Tester{i}",
    )
    Wallet.objects.get_or_create(user=u)
    return u


class Chat:
    def __init__(self, user):
        self.user = user
        self.c = Client()
        self.token_key = None
        from rest_framework.authtoken.models import Token

        self.token_key, _ = Token.objects.get_or_create(user=user)
        self.history = []

    def send(self, msg):
        payload = {"message": msg}
        if self.history:
            payload["history"] = self.history
        r = self.c.post(
            "/api/ai/chat",
            json.dumps(payload),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token_key}",
        )
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.content.decode(errors="replace")[:300]}
        if isinstance(data, dict) and data.get("reply"):
            self.history.append({"role": "user", "content": msg})
            self.history.append(
                {"role": "assistant", "content": str(data.get("reply"))[:500]}
            )
        return data

    def confirm(self, token):
        r = self.c.post(
            "/api/ai/chat",
            json.dumps({"confirm_token": token}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {self.token_key}",
        )
        try:
            return r.json()
        except Exception:
            return {"raw": r.content.decode(errors="replace")[:300]}


def brief(d):
    if not isinstance(d, dict):
        return str(d)[:200]
    bits = []
    if d.get("error"):
        bits.append(f"ERROR={d['error']}")
    reply = d.get("reply")
    if reply:
        bits.append(f"reply={str(reply)[:220]!r}")
    ui = d.get("ui")
    if ui:
        t = ui.get("type")
        n = len(ui.get("data") or []) if isinstance(ui.get("data"), list) else "?"
        extra = ""
        if t == "products" and isinstance(ui.get("data"), list):
            brands = sorted({(p.get("brand") or "").lower() for p in ui.get("data")})
            prices = [float(p.get("price") or 0) for p in ui.get("data")]
            names = [p.get("name") for p in ui.get("data")]
            extra = f" brands={brands} maxprice={max(prices) if prices else '-'} names={names}"
        bits.append(f"ui={t}(n={n}){extra}")
    act = d.get("action")
    if act:
        bits.append(
            f"action={act.get('type')} args={act.get('args')} options={len(act.get('options') or [])}"
        )
    red = d.get("redirect")
    if red:
        bits.append(f"redirect={red}")
    ctx = "context" in d
    if ctx:
        bits.append("(context key present)")
    return " | ".join(bits) or json.dumps(d)[:200]


# ---------------------------------------------------------------- seed data
sneakers = list(Sneaker.objects.filter(is_active=True).order_by("-created_at"))
log(f"### catalog rows: {len(sneakers)}; brands: {sorted({s.brand for s in sneakers})}")

users = [mkuser(i) for i in range(5)]
chats = [Chat(u) for u in users]

seed_prod = next((s for s in sneakers if s.copies and s.copies > 0), None)


def backdated_cart(chat, days=5, sneaker=None):
    s = sneaker or seed_prod
    ci = CartItem.objects.create(
        user=chat.user,
        sneaker=s,
        size=(s.available_sizes or [None])[0],
        quantity=1,
        is_selected=True,
    )
    CartItem.objects.filter(pk=ci.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )
    ci.refresh_from_db()
    return s, ci


def seed_address(u):
    from users.models import Address

    return Address.objects.create(
        user=u,
        recipient_name="GLM Tester",
        phone="9999999999",
        address="1 Test St",
        pincode="560001",
        city="Bangalore",
        state="KA",
        is_default=True,
    )


def seed_order(u, s, status="confirmed"):
    o = Order.objects.create(
        order_number=generate_order_number(),
        user=u,
        recipient_name="GLM Tester",
        email=u.email,
        phone="9999999999",
        address="1 Test St",
        pincode="560001",
        city="Bangalore",
        state="KA",
        payment_method="wallet",
        payment_status="paid",
        status=status,
        subtotal=s.price,
        shipping_fee=0,
        total=s.price,
        cart_item_ids=[],
    )
    OrderItem.objects.create(
        order=o,
        sneaker_id=s.id,
        sneaker_name=s.name,
        sneaker_image="",
        size="",
        quantity=1,
        unit_price=s.price,
        line_total=s.price,
    )
    return o


def section(name):
    def deco(fn):
        def wrapper():
            log(f"\n=== {name} ===")
            try:
                fn()
            except Exception:
                log(f"!!! {name} exception:\n{traceback.format_exc()}")

        return wrapper

    return deco


@section("H1/H2/H4: multi-brand search (3 samples)")
def t_multibrand():
    for label, msg in [
        ("H1", "show me adidas or nike shoes"),
        ("H4", "maybe adidas or nike"),
    ]:
        for i in range(3):
            ch = Chat(mkuser(100 + i))
            d = ch.send(msg)
            called = bool(d.get("ui"))
            log(f"{label} try{i + 1} tool_called={called} -> {brief(d)}")


@section("H3: budget parsing")
def t_budget():
    for label, msg in [
        ("H3a 'budget 2k'", "suggest sneakers, my budget is 2k"),
        ("H3b 'around 1.5k'", "ok now show me options around 1.5k"),
    ]:
        ch = Chat(mkuser(200))
        d = ch.send(msg)
        log(f"{label} -> {brief(d)}")


@section("persona §1b + H5: advice-then-ask, height guardrail")
def t_persona_height():
    c2 = Chat(mkuser(300))
    d = c2.send("best sneaker for a short guy?")
    jumped = bool(d.get("ui")) or bool(d.get("action"))
    log(f"P1 open-ended first turn asked_first={not jumped} -> {brief(d)}")
    d = c2.send("yes, show me what you've got")
    log(f"P2 after yes -> {brief(d)}")
    d = c2.send("I'm 5'4 and I want to look taller btw")
    log(f"H5a short user -> {brief(d)}")

    c3 = Chat(mkuser(301))
    d = c3.send(
        "I'm 6'2 and don't want to look even taller — sleek minimal look. Any picks?"
    )
    log(f"H5b tall user -> {brief(d)}")


@section("H9: how long in cart")
def t_cart_age():
    if not seed_prod:
        log("SKIPPED - no stock product")
        return
    s9, _ = backdated_cart(chats[4], days=5, sneaker=seed_prod)
    d = chats[4].send(f"how long has my {seed_prod.name} been in my cart?")
    log(f"H9 '{seed_prod.name}' seeded 5d ago -> {brief(d)}")


@section("H7/H8: checkout without payment method")
def t_checkout_choice():
    if not seed_prod:
        log("SKIPPED - no stock product")
        return
    backdated_cart(chats[0], days=1, sneaker=seed_prod)
    seed_address(users[0])
    d = chats[0].send("checkout please")
    ok = (d.get("action") or {}).get("type") == "checkout_choice"
    log(f"H7 checkout_choice={ok} -> {brief(d)}")


@section("H10: gated cancel via pending token execution")
def t_cancel_gate():
    if not seed_prod:
        log("SKIPPED - no stock product")
        return
    o10 = seed_order(users[1], seed_prod, status="confirmed")
    d = chats[1].send(f"cancel my order {o10.order_number}")
    log(f"H10a gate -> {brief(d)}")
    tok = (d.get("action") or {}).get("confirm_token")
    if tok:
        d2 = chats[1].confirm(tok)
        o10.refresh_from_db()
        log(f"H10b executed db_status={o10.status} -> {brief(d2)}")
    else:
        log("H10b FAILED - no confirm_token in response")


for fn in (
    t_multibrand,
    t_budget,
    t_persona_height,
    t_cart_age,
    t_checkout_choice,
    t_cancel_gate,
):
    fn()

log("\n### done")
