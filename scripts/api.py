#!/usr/bin/env python3
"""FastAPI service exposing the desk to Telegram.

Every message is forwarded to the HQ agent, with one exception that never
reaches the model: the literal confirmation token. Financial actions are
settled by deterministic Python, not by whatever the agent decided to say.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

import tg
from atk import load_env
from desk_tools import FLATTEN_TOKEN, confirm_flatten, get_desk_status, pending_flatten
from journal import append_event

ENV = load_env()
TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT = str(ENV.get("TELEGRAM_CHAT_ID") or "").strip()
WEBHOOK_SECRET = ENV.get("TELEGRAM_WEBHOOK_SECRET", "")

HELP = (
    "Regime Desk HQ. Normal cümlelerle sor:\n"
    "· How am I doing?\n"
    "· Why is ETH losing?\n"
    "· What changed in the last 20 minutes?\n"
    "· Flatten everything.  (kapatma için FLATTEN ile onaylarsın)"
)

app = FastAPI(title="Regime Desk HQ", version="1.0")


@app.get("/health")
def health() -> dict:
    """Liveness plus a real read of the desk, so a green check means green."""
    try:
        status = get_desk_status()
        desk = {"mode": status.get("mode"), "nav_usdt": status.get("nav_usdt"),
                "exposure_usdt": status.get("total_exposure_usdt")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {"ok": True, "desk": desk, "pending_confirmation": bool(pending_flatten()),
            "telegram_configured": bool(TOKEN and WEBHOOK_SECRET)}


def reply(chat_id: str, text: str) -> None:
    tg.send(TOKEN, chat_id, text)


def answer_with_agent(chat_id: str, question: str) -> None:
    """Runs after the webhook has already returned 200 to Telegram."""
    from hq_agent import ask

    try:
        text = ask(question)
    except Exception as exc:
        append_event("SYSTEM", "ERROR", "HQ agent hatası", {"error": str(exc)[:300]})
        text = f"HQ şu an cevap veremedi: {str(exc)[:200]}"
    reply(chat_id, text)


def handle(chat_id: str, text: str, background: BackgroundTasks) -> str:
    """Route one message. The confirmation token never reaches the agent."""
    message = text.strip()

    if message == FLATTEN_TOKEN:
        result = confirm_flatten(message)
        if result["executed"]:
            return (f"✅ Flatten runner'a iletildi. Kapatılan exposure: "
                    f"{result['exposure_usdt']:.4f} USDT.\nPozisyonlar bir sonraki döngüde kapanır.")
        if result["reason"] == "NO_PENDING_REQUEST":
            return "Bekleyen bir flatten isteği yok. Önce 'flatten everything' de."
        return f"Flatten yürütülemedi: {result.get('detail') or result['reason']}"

    if message.lower().lstrip("/") in {"start", "help", "yardim", "yardım"}:
        return HELP

    background.add_task(answer_with_agent, chat_id, message)
    return ""


@app.post("/telegram/webhook")
async def webhook(request: Request, background: BackgroundTasks,
                  x_telegram_bot_api_secret_token: str = Header(default="")) -> dict:
    if not WEBHOOK_SECRET or x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret token")
    update = await request.json()
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    text = message.get("text") or ""
    if not chat_id or not text:
        return {"ok": True}
    if ALLOWED_CHAT and chat_id != ALLOWED_CHAT:
        append_event("SYSTEM", "WARN", "HQ yetkisiz chat reddedildi", {"chat_id": chat_id})
        return {"ok": True}

    append_event("SYSTEM", "INFO", "HQ mesajı", {"chat_id": chat_id, "text": text[:200]})
    immediate = handle(chat_id, text, background)
    if immediate:
        reply(chat_id, immediate)
    return {"ok": True}
