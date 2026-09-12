#!/usr/bin/env python3
"""Minimal Telegram Bot API client (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20


def call(token: str, method: str, **params: Any) -> dict[str, Any]:
    payload = {key: value for key, value in params.items() if value is not None}
    data = urllib.parse.urlencode(payload).encode()
    request = urllib.request.Request(API.format(token=token, method=method), data=data)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok": False, "description": f"HTTP {exc.code}: {exc.read().decode('utf-8')[:200]}"}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "description": str(exc)}


def send(token: str, chat_id: str | int, text: str) -> dict[str, Any]:
    return call(token, "sendMessage", chat_id=chat_id, text=text[:3900])


def set_webhook(token: str, url: str, secret: str) -> dict[str, Any]:
    return call(token, "setWebhook", url=url, secret_token=secret,
                allowed_updates=json.dumps(["message"]), drop_pending_updates="true")


def delete_webhook(token: str) -> dict[str, Any]:
    return call(token, "deleteWebhook", drop_pending_updates="true")


def webhook_info(token: str) -> dict[str, Any]:
    return call(token, "getWebhookInfo")
