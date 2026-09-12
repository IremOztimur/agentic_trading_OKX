#!/usr/bin/env python3
"""Point the Telegram bot at a public URL (usually an ngrok tunnel)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tg
from atk import load_env


def main() -> int:
    if len(sys.argv) < 2:
        print("kullanım: set_webhook.py https://<public-host>  [--delete]")
        return 1
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    secret = env.get("TELEGRAM_WEBHOOK_SECRET")
    if not token or not secret:
        print("ERROR: .env içinde TELEGRAM_BOT_TOKEN ve TELEGRAM_WEBHOOK_SECRET olmalı")
        return 1
    if "--delete" in sys.argv:
        print(tg.delete_webhook(token))
        return 0
    url = sys.argv[1].rstrip("/") + "/telegram/webhook"
    result = tg.set_webhook(token, url, secret)
    if not result.get("ok"):
        print(f"ERROR: {result.get('description')}")
        return 1
    info = tg.webhook_info(token).get("result", {})
    print(f"webhook: {info.get('url')}\npending: {info.get('pending_update_count')}\n"
          f"last error: {info.get('last_error_message') or 'yok'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
