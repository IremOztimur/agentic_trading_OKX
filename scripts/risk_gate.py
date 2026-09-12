#!/usr/bin/env python3
"""Deterministic Regime Desk risk gate and Claude PreToolUse guard."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from journal import append_event, load_state, merge_state


RISK_PER_TRADE = 0.0035
COIN_HARD_CAP = 0.25
TOTAL_HARD_CAP = 0.50
SOFT_DRAWDOWN = -0.03
HARD_DRAWDOWN = -0.05
CONFIDENCE_FLOOR = 0.65
MARKET_STALE_SECONDS = 20
ACCOUNT_STALE_SECONDS = 30
FORBIDDEN_WRITE = re.compile(r"swap|future|option|earn|withdraw|transfer|leverage|margin|borrow|repay|loan", re.I)
WRITE_WORD = re.compile(r"(?:^|__|_)(?:place|create|amend|cancel|stop|close|buy|sell|set|redeem|purchase)(?:_|$)", re.I)
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


def agent_owned_exposure(state: dict[str, Any], symbol: str, price: float) -> float:
    baseline = state.get("session", {}).get("starting_inventory", {})
    base = symbol.split("-")[0]
    for position in state.get("account", {}).get("positions", []):
        if position.get("symbol") != symbol:
            continue
        exposure = max(0.0, float(position.get("exposure_usdt") or 0))
        if position.get("owner") == "AGENT":
            return exposure
        current = float(position.get("base_amount") or 0)
        owned_base = max(0.0, current - float(baseline.get(base, 0) or 0))
        return min(exposure, owned_base * max(price, 0.0))
    return 0.0


def symbol_features(state: dict[str, Any], symbol: str) -> dict[str, Any]:
    for item in state.get("symbols", []) or state.get("observation_symbols", []):
        if item.get("symbol") == symbol:
            return item.get("features") or {}
    return {}


def floor_step(value: float, step: Any) -> float:
    try:
        quantum = Decimal(str(step))
        if quantum <= 0:
            return value
        return float((Decimal(str(value)) / quantum).to_integral_value(rounding=ROUND_DOWN) * quantum)
    except Exception:
        return value


def freshness_age(explicit: Any, observed_at: str | None, now: datetime) -> float:
    parsed = parse_time(observed_at)
    if parsed:
        return max(0.0, (now - parsed).total_seconds())
    try:
        return float(explicit)
    except (TypeError, ValueError):
        return 9999.0


def evaluate(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
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
    now = now or datetime.now(timezone.utc)
    health = session.get("health") or {}
    reducing = action in REDUCE_ACTIONS
    market_age = freshness_age(proposal.get("market_age_seconds"), proposal.get("market_observed_at"), now)
    account_age = freshness_age(account.get("account_age_seconds"), account.get("account_observed_at"), now)
    safe_close = now.astimezone(ZoneInfo("Europe/Istanbul"))

    if drawdown <= HARD_DRAWDOWN:
        verdict, reasons = "HALT", ["HARD_DRAWDOWN"]
    elif (safe_close.hour, safe_close.minute) >= (19, 20) and not reducing:
        verdict, reasons = "HALT", ["SAFE_CLOSE"]
    elif session.get("mode") not in {"DRY_RUN", "LIVE"} and not reducing:
        verdict, reasons = "HOLD", ["MODE_BLOCKED"]
    elif not health.get("trade_ready") and not reducing:
        verdict, reasons = "HOLD", ["PREFLIGHT_INCOMPLETE"]
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
    elif market_age > MARKET_STALE_SECONDS and not reducing:
        verdict, reasons = "HOLD", ["STALE_MARKET"]
    elif account_age > ACCOUNT_STALE_SECONDS and not reducing:
        verdict, reasons = "HOLD", ["STALE_ACCOUNT"]
    elif nav <= 0:
        verdict, reasons = "HOLD", ["INVALID_NAV"]
    elif state.get("cycle", {}).get("execution", {}).get("run_id") == run_id and state.get("cycle", {}).get("execution", {}).get("status") in {"SUBMITTED", "FILLED", "SIMULATED"}:
        verdict, reasons = "HOLD", ["DUPLICATE_RUN"]
    else:
        if action in {"CANCEL", "STOP_GRID"}:
            allowed = 0.0
        elif action in {"REDUCE", "FLATTEN"}:
            owned = agent_owned_exposure(state, symbol, float(proposal.get("price") or 0))
            allowed = min(requested, owned)
            if allowed <= 0:
                verdict, reasons = "HOLD", ["NO_AGENT_OWNED_EXPOSURE"]
        else:
            stop = max(float(proposal.get("stop_distance_pct") or 0), 0.0001)
            risk_size = nav * RISK_PER_TRADE / stop
            coin_room = max(0.0, nav * COIN_HARD_CAP - position_exposure(state, symbol))
            total_room = max(0.0, nav * TOTAL_HARD_CAP - float(account.get("total_exposure_usdt") or 0))
            available = max(0.0, float(account.get("available_usdt") or 0))
            allowed = min(requested, risk_size, coin_room, total_room, available)
            features = symbol_features(state, symbol)
            if features.get("instrument_state") not in (None, "live"):
                verdict, reasons = "HOLD", ["INSTRUMENT_NOT_LIVE"]
            min_notional = float(features.get("minSz") or 0) * float(proposal.get("price") or 0)
            if verdict == "ALLOW" and min_notional > 0 and allowed < min_notional:
                verdict, reasons = "HOLD", ["BELOW_MIN_SIZE"]
        if action not in REDUCE_ACTIONS and allowed <= 0:
            verdict, reasons = "HOLD", ["NO_EXPOSURE_ROOM"]

    approved_call = None
    if verdict == "ALLOW":
        call = proposal.get("mcp_call") or {}
        if session.get("mode") == "DRY_RUN" and not call:
            return {
                "run_id": run_id, "verdict": "ALLOW", "allowed_notional_usdt": round(allowed, 8),
                "reason_codes": ["DRY_RUN_SEMANTIC_ALLOW"], "checked_at": now.isoformat(),
                "expires_at": proposal.get("expires_at"), "approved_call": None,
            }
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
            size_key = next((key for key in ("quoteSz", "notional", "notionalUsd", "investAmt", "sz") if key in arguments), None)
            if action not in REDUCE_ACTIONS and not size_key:
                verdict, reasons = "HOLD", ["UNSUPPORTED_SIZE_FIELD"]
            else:
                if size_key:
                    if action in {"REDUCE", "FLATTEN"} and size_key == "sz":
                        price = max(float(proposal.get("price") or 0), 0.00000001)
                        quantity = floor_step(allowed / price, symbol_features(state, symbol).get("lotSz"))
                        arguments[size_key] = str(round(quantity, 12))
                    else:
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
