"""Probe the Cloudflare Worker directly to isolate GLM tool-calling failures."""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sneaket_backend.settings")

import django

django.setup()

from django.conf import settings
import requests
from ai.tools import TOOLS

URL = settings.CLOUDFLARE_AI_ENDPOINT
HDRS = {"Content-Type": "application/json"}
secret = getattr(settings, "AI_WORKER_SECRET", "")
if secret:
    HDRS["X-Internal-Key"] = secret


def post(label, payload, tries=2, raw=False):
    for i in range(tries):
        try:
            r = requests.post(URL, json=payload, headers=HDRS, timeout=60)
            body = r.text if raw else r.text[:300].replace("\n", " ")
            print(f"{label} try{i + 1}: HTTP {r.status_code} {body}")
        except Exception as e:
            print(f"{label} try{i + 1}: EXC {e}")
        if i < tries - 1:
            import time

            time.sleep(3)


SIMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel a user order by order number",
            "parameters": {
                "type": "object",
                "properties": {"order_number": {"type": "string"}},
                "required": ["order_number"],
            },
        },
    }
]

MSG = [{"role": "user", "content": "cancel my order SNEK-123"}]

# A: single simple tool (the doc §5 quick test)
post(
    "A simple-tool default-model",
    {"messages": MSG, "tools": SIMPLE_TOOLS, "tool_choice": "auto"},
)

# B: FULL 14-tool catalog + short system prompt
post(
    "B full-TOOLS short-prompt ",
    {
        "systemPrompt": "You are SNEAKET AI.",
        "messages": MSG,
        "tools": TOOLS,
        "tool_choice": "auto",
    },
)

# C: explicit glm model id, simple tool
post(
    "C glm explicit simple    ",
    {
        "model": "@cf/zai-org/glm-4.7-flash",
        "messages": MSG,
        "tools": SIMPLE_TOOLS,
        "tool_choice": "auto",
    },
)

# D: explicit mistral + full TOOLS (control — known-good previously)
post(
    "D mistral full-TOOLS     ",
    {
        "model": "@cf/mistralai/mistral-small-3.1-24b-instruct",
        "messages": MSG,
        "tools": TOOLS,
        "tool_choice": "auto",
    },
)
