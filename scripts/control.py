#!/usr/bin/env python3
"""Control plane for runner mode changes. Never talks to OKX directly."""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import datetime, timezone

from journal import append_event, load_state, merge_state
from session_policy import live_override_date


def age(value: str | None) -> float:
    if not value:
        return float("inf")
    try:
        return max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())
    except ValueError:
        return float("inf")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("status", "preflight", "live", "pause", "resume", "flatten"))
    parser.add_argument("confirmation", nargs="?")
    args = parser.parse_args()
    state = load_state()
    if args.command == "status":
        print(json.dumps({"mode": state["session"]["mode"], "health": state["session"]["health"], "watchdog": state["watchdog"]}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "preflight":
        patch, message = {"session": {"private_preflight_requested": True}}, "Custom MCP private preflight runner kuyruğuna alındı"
    elif args.command == "live":
        health = state["session"]["health"]
        if args.confirmation != "CANLI":
            raise SystemExit("LIVE için tam olarak CANLI yazılmalı")
        if any(health.get(key) != "READY" for key in ("mcp", "account", "market", "watchdog")) or not health.get("trade_ready"):
            raise SystemExit("LIVE reddedildi: preflight hazır değil")
        if age(state["session"].get("heartbeat_at")) > 30:
            raise SystemExit("LIVE reddedildi: runner heartbeat stale")
        override_date = live_override_date()
        patch = {"session": {"mode": "LIVE", "safe_close_override_date": override_date}}
        message = ("LIVE modu kullanıcı onayıyla açıldı; bugünkü safe-close açıkça override edildi"
                   if override_date else "LIVE modu kullanıcı onayıyla açıldı")
    elif args.command == "pause":
        patch, message = {"session": {"mode": "PAUSED"}}, "Desk kullanıcı tarafından PAUSED yapıldı"
    elif args.command == "resume":
        patch = {"session": {"mode": "DRY_RUN", "flatten_requested": False,
                             "flatten_request_id": None, "safe_close_override_date": None}}
        message = "Desk DRY_RUN modunda devam ediyor"
    else:
        if args.confirmation != "FLATTEN":
            raise SystemExit("Flatten için tam olarak FLATTEN yazılmalı")
        request_id = f"fl{datetime.now(timezone.utc).strftime('%y%m%d%H%M%S')}{secrets.token_hex(3)}"
        patch = {"session": {"mode": "HALTED", "flatten_requested": True,
                             "flatten_request_id": request_id}}
        message = "Emergency flatten runner kuyruğuna alındı"
    merge_state(patch)
    append_event("SYSTEM", "WARN" if args.command in {"pause", "flatten"} else "INFO", message)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
