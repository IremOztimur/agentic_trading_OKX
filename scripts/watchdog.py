#!/usr/bin/env python3
"""Fail-closed watchdog policy. MCP emergency calls are executed by `/desk watchdog`."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from journal import append_event, load_state, merge_state
from session_policy import safe_close_active


HEARTBEAT_STALE_SECONDS = 90
HARD_DRAWDOWN = -0.05


def age_seconds(value: str | None, now: datetime) -> float:
    if not value:
        return float("inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0.0, (now - parsed).total_seconds())
    except ValueError:
        return float("inf")


def agent_inventory(state: dict) -> list[dict]:
    baseline = state.get("session", {}).get("starting_inventory", {})
    actions = []
    for position in state.get("account", {}).get("positions", []):
        symbol = position.get("symbol", "")
        base = symbol.split("-")[0]
        current = float(position.get("base_amount") or 0)
        owned = max(0.0, current - float(baseline.get(base, 0) or 0))
        if owned > 0:
            actions.append({"action": "FLATTEN_AGENT_INVENTORY", "symbol": symbol, "base_amount": owned, "side": "sell"})
    return actions


def evaluate(state: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    session = state.get("session", {})
    mode = session.get("mode", "DISCONNECTED")
    drawdown = float(state.get("account", {}).get("drawdown_pct") or 0)
    heartbeat_age = age_seconds(state.get("session", {}).get("heartbeat_at"), now)
    flatten_requested = bool(session.get("flatten_requested"))
    result = {"status": "READY", "mode": mode, "reason": None, "actions": [], "checked_at": now.isoformat()}

    hard_stop = drawdown <= HARD_DRAWDOWN or safe_close_active(session, now)
    if flatten_requested or (mode == "LIVE" and hard_stop):
        reason = "USER_FLATTEN" if flatten_requested else ("HARD_DRAWDOWN" if drawdown <= HARD_DRAWDOWN else "SAFE_CLOSE")
        result.update(status="HALT_REQUIRED", mode="HALTED", reason=reason)
        result["actions"] = [{"action": "CANCEL_ENTRY_ORDERS"}, {"action": "STOP_GRIDS"}, *agent_inventory(state)]
    elif mode == "LIVE" and heartbeat_age > HEARTBEAT_STALE_SECONDS:
        result.update(status="PAUSE_REQUIRED", mode="PAUSED", reason="STALE_HEARTBEAT")
        result["actions"] = [{"action": "CANCEL_ENTRY_ORDERS"}, {"action": "STOP_GRIDS"}]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("once",))
    parser.parse_args()
    result = evaluate(load_state())
    merge_state({"session": {"mode": result["mode"]}, "watchdog": {"status": result["status"], "last_check_at": result["checked_at"], "last_action": result["reason"]}})
    append_event("WATCHDOG", "WARN" if result["actions"] else "INFO", result["reason"] or "Watchdog kontrolü temiz", {"actions": result["actions"]})
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result["status"] == "HALT_REQUIRED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
