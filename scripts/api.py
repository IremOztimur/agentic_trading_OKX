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
from desk_tools import get_desk_status, run_control
from hq_agent import build_agent
from journal import append_event
from telegram_control import action_callback, is_flatten_request, is_live_request, verify_action_callback

ENV = load_env()
PUBLIC_URL = ENV.get("HQ_PUBLIC_URL") or None


allowed = [int(ENV["TELEGRAM_CHAT_ID"])] if ENV.get("TELEGRAM_CHAT_ID") else None


class DeskTelegramInterface(TelegramInterface):
    """Keep safety-critical mode controls deterministic and responsive."""

    async def _auto_set_webhook(self):
        if self._webhook_url and not self._webhook_set:
            full_url = f"{self._webhook_url.rstrip('/')}/telegram/webhook"
            self._webhook_set = await self.telegram_tools.aset_webhook(
                url=full_url,
                secret_token=self.webhook_secret,
                allowed_updates=["message", "callback_query"],
            )

    async def _process_task_mode(self, text, user_id, chat_id, message):
        action = "live" if is_live_request(text) else "flatten" if is_flatten_request(text) else None
        if not action:
            return await super()._process_task_mode(text, user_id, chat_id, message)
        callback = action_callback(action, ENV.get("TELEGRAM_WEBHOOK_SECRET", ""))
        flatten = action == "flatten"
        append_event("SYSTEM", "WARN" if flatten else "INFO",
                     f"Telegram {action.upper()} HITL onayı bekleniyor", {"user_id": user_id})
        await self.telegram_tools.asend_message(
            chat_id=chat_id,
            text=("Flatten every desk-owned position and halt new risk?"
                  if flatten else "Enable LIVE execution? Real spot orders remain subject to the deterministic risk gate."),
            reply_markup={"inline_keyboard": [[
                {"text": "Confirm FLATTEN" if flatten else "Go LIVE", "callback_data": callback},
                {"text": "Cancel", "callback_data": "desk:cancel"},
            ]]},
            message_thread_id=message.message_thread_id,
        )

    async def _process_callback_query(self, callback_query):
        callback = callback_query.data or ""
        if not callback.startswith("desk:"):
            return await super()._process_callback_query(callback_query)
        await self.telegram_tools.aanswer_callback_query(callback_query.id)
        chat_id = callback_query.message.chat.id if callback_query.message else None
        if not chat_id or not self.is_user_allowed(callback_query.from_user.id):
            return
        if callback == "desk:cancel":
            await self.telegram_tools.asend_message(chat_id=chat_id, text="Control request cancelled.")
            return
        secret = ENV.get("TELEGRAM_WEBHOOK_SECRET", "")
        action = verify_action_callback(callback, secret)
        if not action:
            await self.telegram_tools.asend_message(chat_id=chat_id, text="Confirmation expired. Send the command again.")
            return
        command = ("live", "CANLI") if action == "live" else ("flatten", "FLATTEN")
        ok, detail = run_control(*command)
        append_event("SYSTEM", "WARN" if action == "flatten" or not ok else "INFO",
                     f"Telegram {action.upper()} HITL {'onaylandı' if ok else 'başarısız'}", {"detail": detail})
        await self.telegram_tools.asend_message(
            chat_id=chat_id,
            text=(("LIVE enabled. The dashboard will update within 5 seconds."
                   if action == "live" else "FLATTEN queued. The runner will close desk-owned positions and remain halted.")
                  if ok else f"{action.upper()} could not be completed: {detail}"),
        )


telegram = DeskTelegramInterface(
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
