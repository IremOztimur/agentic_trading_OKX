#!/usr/bin/env python3
"""FastAPI service exposing the desk to Telegram.

Upsonic's TelegramInterface owns the transport: webhook route, secret token,
user allowlist, chat sessions, typing indicators, message splitting. The only
thing added here is the guard on the one financial action — the confirmation
token is matched before the agent is invoked, so no model sits between the
operator and a position being closed.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI
from upsonic.interfaces.telegram import TelegramInterface
from upsonic.interfaces.telegram.schemas import TelegramMessage

from atk import load_env
from desk_tools import FLATTEN_TOKEN, confirm_flatten, get_desk_status, pending_flatten
from hq_agent import build_agent
from journal import append_event

ENV = load_env()
PUBLIC_URL = ENV.get("HQ_PUBLIC_URL") or None


class DeskTelegram(TelegramInterface):
    """TelegramInterface with one message the agent never sees."""

    async def _process_text_message(self, message: TelegramMessage, user_id: int, chat_id: int) -> None:
        text = (message.text or "").strip()
        if text != FLATTEN_TOKEN:
            await super()._process_text_message(message, user_id, chat_id)
            return

        result = confirm_flatten(text)
        if result["executed"]:
            answer = (f"✅ Flatten runner'a iletildi. Kapatılan exposure: "
                      f"{result['exposure_usdt']:.4f} USDT.\nPozisyonlar bir sonraki döngüde kapanır.")
        elif result["reason"] == "NO_PENDING_REQUEST":
            answer = "Bekleyen bir flatten isteği yok. Önce 'flatten everything' de."
        else:
            answer = f"Flatten yürütülemedi: {result.get('detail') or result['reason']}"
        append_event("SYSTEM", "WARN", "HQ flatten token işlendi",
                     {"executed": result["executed"], "reason": result.get("reason")})
        await self.telegram_tools.asend_message(chat_id=chat_id, text=answer)


allowed = [int(ENV["TELEGRAM_CHAT_ID"])] if ENV.get("TELEGRAM_CHAT_ID") else None
telegram = DeskTelegram(
    agent=build_agent(),
    bot_token=ENV.get("TELEGRAM_BOT_TOKEN"),
    name="Regime Desk HQ",
    allowed_user_ids=allowed,
    webhook_secret=ENV.get("TELEGRAM_WEBHOOK_SECRET"),
    webhook_url=PUBLIC_URL,
    parse_mode=None,
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
    return {"ok": True, "desk": desk, "pending_confirmation": bool(pending_flatten())}
