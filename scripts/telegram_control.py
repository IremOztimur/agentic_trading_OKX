"""Signed, short-lived Telegram callbacks for deterministic desk controls."""

from __future__ import annotations

import hashlib
import hmac
import time


LIVE_REQUESTS = {"/live", "go live", "let's go live", "lets go live", "canlı", "canli"}
FLATTEN_REQUESTS = {"/flatten", "flatten", "flatten all", "close everything", "hepsini kapat"}
CALLBACK_TTL_SECONDS = 300


def is_live_request(text: str) -> bool:
    return " ".join(str(text or "").strip().lower().split()) in LIVE_REQUESTS


def is_flatten_request(text: str) -> bool:
    return " ".join(str(text or "").strip().lower().split()) in FLATTEN_REQUESTS


def action_callback(action: str, secret: str, now: int | None = None) -> str:
    if action not in {"live", "flatten"}:
        raise ValueError("unsupported Telegram control action")
    timestamp = int(time.time() if now is None else now)
    payload = f"desk:{action}:{timestamp}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:12]
    return f"{payload}:{signature}"


def verify_action_callback(value: str, secret: str, now: int | None = None) -> str | None:
    parts = str(value or "").split(":")
    if len(parts) != 4 or parts[0] != "desk" or parts[1] not in {"live", "flatten"}:
        return None
    try:
        timestamp = int(parts[2])
    except ValueError:
        return None
    current = int(time.time() if now is None else now)
    if timestamp > current + 30 or current - timestamp > CALLBACK_TTL_SECONDS:
        return None
    expected = action_callback(parts[1], secret, timestamp).rsplit(":", 1)[1]
    return parts[1] if hmac.compare_digest(parts[3], expected) else None


def live_callback(secret: str, now: int | None = None) -> str:
    return action_callback("live", secret, now)


def verify_live_callback(value: str, secret: str, now: int | None = None) -> bool:
    return verify_action_callback(value, secret, now) == "live"
