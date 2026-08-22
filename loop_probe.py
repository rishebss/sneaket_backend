"""Probe: which tool-result message shapes does GLM accept for multi-turn loops?"""

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

SYS = "You are SNEAKET AI. Use tools, then reason over their JSON results to answer."
RESULTS = '[{"name":"Adidas Samba - Vegan","brand":"adidas","price":"1640","description":"chunky platform sole vegan leather sneaker"},{"name":"Nike Dunk Low Retro","brand":"nike","price":"1880","description":"low-top court classic, flat sole"}]'

TOOL_V2_ASSIST = {
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "search_products",
                "arguments": '{"search": "chunky platform"}',
            },
        }
    ],
}
TOOL_V2_RESULT = {
    "role": "tool",
    "tool_call_id": "call_1",
    "name": "search_products",
    "content": RESULTS,
}
TOOL_V1_ASSIST = {"role": "assistant", "content": "Searching chunky platform options."}
TOOL_V1_RESULT = {"role": "tool", "name": "search_products", "content": RESULTS}

VARIANTS = {
    "V1 plain-name   ": [SYS, TOOL_V1_ASSIST, TOOL_V1_RESULT],
    "V2 openai-ids   ": [SYS, TOOL_V2_ASSIST, TOOL_V2_RESULT],
    "V3 obj-args+ids ": [
        SYS,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_products",
                        "arguments": {"search": "chunky platform"},
                    },
                }
            ],
        },
        TOOL_V2_RESULT,
    ],
}


def post(label, payload):
    try:
        r = requests.post(URL, json=payload, headers=HDRS, timeout=60)
        print(f"{label}: HTTP {r.status_code} {r.text[:220]}".replace(chr(10), " "))
    except Exception as e:
        print(f"{label}: EXC {e}")


for label, msgs in VARIANTS.items():
    post(
        label,
        {
            "systemPrompt": SYS,
            "messages": [
                {
                    "role": "user",
                    "content": "recommend something chunky for a short guy",
                }
            ]
            + msgs,
            "tools": TOOLS,
            "tool_choice": "auto",
        },
    )
