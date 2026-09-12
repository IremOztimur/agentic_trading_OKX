#!/usr/bin/env python3
"""FastAPI service exposing the desk to Telegram.

Upsonic's TelegramInterface owns everything here: webhook route, secret token,
user allowlist, chat sessions, and the Confirm/Reject buttons for tools marked
`requires_confirmation`. Those tools pause before their body runs, so the
operator — never the model — authorizes anything that moves money.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI
from upsonic.interfaces.telegram import TelegramInterface

from atk import load_env
from desk_tools import get_desk_status
from hq_agent import build_agent

ENV = load_env()
PUBLIC_URL = ENV.get("HQ_PUBLIC_URL") or None


allowed = [int(ENV["TELEGRAM_CHAT_ID"])] if ENV.get("TELEGRAM_CHAT_ID") else None
telegram = TelegramInterface(
    agent=build_agent(),
    bot_token=ENV.get("TELEGRAM_BOT_TOKEN"),
    name="Regime Desk HQ",
    allowed_user_ids=allowed,
    webhook_secret=ENV.get("TELEGRAM_WEBHOOK_SECRET"),
    webhook_url=PUBLIC_URL,
    parse_mode="Markdown",
    typing_indicator=True,
)

app = FastAPI(title="Regime Desk HQ", version="2.0")
app.include_router(telegram.attach_routes())


@app.get("/health")
def health() -> dict:
    """Liveness plus a real read of the desk, so a green check means green."""
    try:
        status = get_desk_status()
        desk = {"mode": status.get("mode"), "nav_usdt": status.get("nav_usdt"),
                "exposure_usdt": status.get("total_exposure_usdt")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "desk": desk}
