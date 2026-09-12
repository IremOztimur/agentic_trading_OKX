#!/usr/bin/env python3
"""Deterministic Regime Desk risk gate and Claude PreToolUse guard."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from journal import append_event, load_state, merge_state


RISK_PER_TRADE = 0.0035
COIN_HARD_CAP = 0.25
TOTAL_HARD_CAP = 0.50
SOFT_DRAWDOWN = -0.03
HARD_DRAWDOWN = -0.05
CONFIDENCE_FLOOR = 0.65
MARKET_STALE_SECONDS = 20
ACCOUNT_STALE_SECONDS = 30
FORBIDDEN_WRITE = re.compile(r"swap|future|option|earn|withdraw|transfer|leverage", re.I)
WRITE_WORD = re.compile(r"place|create|amend|cancel|stop|close|buy|sell|set_|redeem|purchase", re.I)
ENTRY_WRITE = re.compile(r"(spot|grid).*(place|create)|(?:place|create).*(spot|grid)", re.I)
REDUCE_ACTIONS = {"REDUCE", "FLATTEN", "CANCEL", "STOP_GRID"}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def position_exposure(state: dict[str, Any], symbol: str) -> float:
    for position in state.get("account", {}).get("positions", []):
        if position.get("symbol") == symbol:
            return float(position.get("exposure_usdt", 0) or 0)
    return 0.0


def evaluate(state: dict[str, Any]) -> dict[str, Any]:
    proposal = state.get("cycle", {}).get("proposal") or {}
    account = state.get("account", {})
    session = state.get("session", {})
    run_id = proposal.get("run_id") or state.get("cycle", {}).get("run_id")
    reasons: list[str] = []
    verdict = "ALLOW"
    allowed = 0.0

    nav = float(account.get("nav") or 0)
    drawdown = float(account.get("drawdown_pct") or 0)
    action = str(proposal.get("action") or "HOLD")
    requested = max(0.0, float(proposal.get("requested_notional_usdt") or 0))
    symbol = str(proposal.get("symbol") or "")
    confidence = float(proposal.get("confidence") or 0)
    expires = parse_time(proposal.get("expires_at"))
    now = datetime.now(timezone.utc)

    if drawdown <= HARD_DRAWDOWN:
        verdict, reasons = "HALT", ["HARD_DRAWDOWN"]
    elif action in {"HOLD", ""}:
        verdict, reasons = "HOLD", ["NO_ACTION"]
    elif drawdown <= SOFT_DRAWDOWN and action not in REDUCE_ACTIONS:
        verdict, reasons = "HOLD", ["SOFT_DRAWDOWN"]
    elif proposal.get("smart_money_veto") and action not in REDUCE_ACTIONS:
        verdict, reasons = "HOLD", ["SMART_MONEY_VETO"]
    elif confidence < CONFIDENCE_FLOOR and action not in REDUCE_ACTIONS:
        verdict, reasons = "HOLD", ["LOW_CONFIDENCE"]
    elif not expires or expires <= now:
        verdict, reasons = "HOLD", ["EXPIRED_DECISION"]
    elif float(proposal.get("market_age_seconds", 9999)) > MARKET_STALE_SECONDS:
        verdict, reasons = "HOLD", ["STALE_MARKET"]
    elif float(account.get("account_age_seconds", 9999)) > ACCOUNT_STALE_SECONDS:
        verdict, reasons = "HOLD", ["STALE_ACCOUNT"]
    elif nav <= 0:
        verdict, reasons = "HOLD", ["INVALID_NAV"]
    elif state.get("cycle", {}).get("execution", {}).get("run_id") == run_id and state.get("cycle", {}).get("execution", {}).get("status") in {"SUBMITTED", "FILLED", "SIMULATED"}:
        verdict, reasons = "HOLD", ["DUPLICATE_RUN"]
    else:
        stop = max(float(proposal.get("stop_distance_pct") or 0), 0.0001)
        risk_size = nav * RISK_PER_TRADE / stop
        coin_room = max(0.0, nav * COIN_HARD_CAP - position_exposure(state, symbol))
        total_room = max(0.0, nav * TOTAL_HARD_CAP - float(account.get("total_exposure_usdt") or 0))
        available = max(0.0, float(account.get("available_usdt") or 0))
        allowed = min(requested, risk_size, coin_room, total_room, available)
        if action not in REDUCE_ACTIONS and allowed <= 0:
            verdict, reasons = "HOLD", ["NO_EXPOSURE_ROOM"]

    approved_call = None
    if verdict == "ALLOW":
        call = proposal.get("mcp_call") or {}
        tool = str(call.get("tool") or "")
        arguments = dict(call.get("arguments") or {})
        if FORBIDDEN_WRITE.search(tool):
            verdict, reasons = "HOLD", ["FORBIDDEN_TOOL"]
        elif action in {"REDUCE", "FLATTEN"} and arguments.get("side") != "sell":
            verdict, reasons = "HOLD", ["NON_REDUCING_SIDE"]
        elif action == "CANCEL" and "cancel" not in tool.lower():
            verdict, reasons = "HOLD", ["ACTION_TOOL_MISMATCH"]
        elif action == "STOP_GRID" and "stop" not in tool.lower():
            verdict, reasons = "HOLD", ["ACTION_TOOL_MISMATCH"]
        elif action not in REDUCE_ACTIONS and not ENTRY_WRITE.search(tool):
            verdict, reasons = "HOLD", ["UNSUPPORTED_WRITE_TOOL"]
        else:
            size_key = next((key for key in ("quoteSz", "notional", "notionalUsd") if key in arguments), None)
            if action not in REDUCE_ACTIONS and not size_key:
                verdict, reasons = "HOLD", ["UNSUPPORTED_SIZE_FIELD"]
            else:
                if size_key:
                    arguments[size_key] = str(round(allowed, 8))
                approved_call = {"tool": tool, "arguments": arguments}

    return {
        "run_id": run_id,
        "verdict": verdict,
        "allowed_notional_usdt": round(allowed if verdict == "ALLOW" else 0.0, 8),
        "reason_codes": reasons or ["WITHIN_LIMITS"],
        "checked_at": now.isoformat(),
        "expires_at": proposal.get("expires_at"),
        "approved_call": approved_call,
    }


def deny(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": f"Regime Desk: {reason}"}}


def hook(payload: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    tool = str(payload.get("tool_name") or "")
    if not WRITE_WORD.search(tool):
        return None
    if FORBIDDEN_WRITE.search(tool):
        return deny("yasak OKX write kategorisi")
    if state.get("session", {}).get("mode") != "LIVE":
        return deny("MCP write için mode LIVE olmalı")
    fresh_gate = evaluate(state)
    if fresh_gate["verdict"] != "ALLOW":
        return deny(", ".join(fresh_gate["reason_codes"]))
    approved = fresh_gate.get("approved_call") or {}
    if approved.get("tool") != tool:
        return deny("tool güncel gate ile eşleşmiyor")
    actual_args = payload.get("tool_input") or {}
    if actual_args != approved.get("arguments"):
        return deny("tool input güncel gate ile birebir eşleşmiyor")
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": "Regime Desk deterministic gate ALLOW"}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "hook"))
    args = parser.parse_args()
    state = load_state()
    if args.mode == "check":
        gate = evaluate(state)
        merge_state({"cycle": {"gate": gate}})
        append_event("GATE", "ERROR" if gate["verdict"] == "HALT" else "INFO", f"Risk gate: {gate['verdict']}", {"reason_codes": gate["reason_codes"], "allowed_notional_usdt": gate["allowed_notional_usdt"]}, gate.get("run_id"))
        print(json.dumps(gate, ensure_ascii=False))
        return 2 if gate["verdict"] == "HALT" else 0
    result = hook(json.load(sys.stdin), state)
    if result:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
