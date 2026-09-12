#!/usr/bin/env python3
"""Execution layer: turns a risk-gate ALLOW into a real OKX spot order.

The gate owns the size. This module only maps an approved semantic action onto
the exact ATK MCP write call, and never resends an order it cannot first prove
is missing.
"""

from __future__ import annotations

import re
from typing import Any

from datetime import datetime, timedelta, timezone

from journal import append_event, load_state, merge_state, utc_now
from perception import number
from risk_gate import evaluate as evaluate_risk

# Hard ceiling while the desk is young; NAV on the competition sub-account is ~30 USDT.
ABSOLUTE_MAX_NOTIONAL = 10.0
MAX_NAV_FRACTION = 0.05


def client_order_id(run_id: str) -> str:
    """OKX allows 32 alphanumeric characters; the run id is our idempotency key."""
    return ("rd" + re.sub(r"[^A-Za-z0-9]", "", str(run_id)))[:32]


def notional_cap(nav: float) -> float:
    return min(ABSOLUTE_MAX_NOTIONAL, nav * MAX_NAV_FRACTION)


def build_call(proposal: dict[str, Any]) -> dict[str, Any] | None:
    """Semantic action → ATK MCP tool call. Size is a placeholder; the gate sets it."""
    symbol = proposal.get("symbol")
    action = proposal.get("action")
    if not symbol or action not in {"BUY", "REDUCE"}:
        return None
    common = {"instId": symbol, "tdMode": "cash", "ordType": "market",
              "clOrdId": client_order_id(proposal.get("run_id", ""))}
    if action == "BUY":
        # Market buy sizes in the quote currency, so `sz` is USDT.
        return {"tool": "spot_place_order", "arguments": {**common, "side": "buy", "tgtCcy": "quote_ccy", "sz": "0"}}
    return {"tool": "spot_place_order", "arguments": {**common, "side": "sell", "tgtCcy": "base_ccy", "sz": "0"}}


def already_sent(client, symbol: str, clord_id: str) -> dict[str, Any] | None:
    """Never blind-retry: look the client order id up before sending anything."""
    try:
        rows = client.call("spot_get_order", instId=symbol, clOrdId=clord_id)
    except Exception:
        return None
    row = (rows or [{}])[0] if isinstance(rows, list) else rows
    return row if isinstance(row, dict) and row.get("ordId") else None


def execute(client, state: dict[str, Any]) -> dict[str, Any]:
    """Place the approved order. Returns the execution block written to state."""
    proposal = dict(state.get("cycle", {}).get("proposal") or {})
    run_id = proposal.get("run_id")
    call = build_call(proposal)
    if not call:
        return {"run_id": run_id, "status": "NOT_SENT", "tool": None, "client_order_id": None, "order_id": None}

    # Attach the concrete call, then let the gate re-price and approve it verbatim.
    nav = number(state.get("account", {}).get("nav"))
    proposal["mcp_call"] = call
    merge_state({"cycle": {"proposal": proposal}})
    gate = evaluate_risk({**state, "cycle": {**state.get("cycle", {}), "proposal": proposal}})
    if gate["verdict"] != "ALLOW" or not gate.get("approved_call"):
        merge_state({"cycle": {"gate": gate}})
        append_event("GATE", "WARN", f"Execution öncesi gate {gate['verdict']}", {"reason_codes": gate["reason_codes"]}, run_id)
        return {"run_id": run_id, "status": "NOT_SENT", "tool": None, "client_order_id": None, "order_id": None}

    approved = gate["approved_call"]
    arguments = dict(approved["arguments"])
    cap = notional_cap(nav)
    if approved["arguments"].get("side") == "buy" and number(arguments.get("sz")) > cap:
        arguments["sz"] = str(round(cap, 8))
    clord_id = arguments.get("clOrdId")
    symbol = arguments.get("instId")

    existing = already_sent(client, symbol, clord_id)
    if existing:
        append_event("MCP_WRITE", "WARN", "Aynı client order ID zaten mevcut; tekrar gönderilmedi",
                     {"client_order_id": clord_id, "order_id": existing.get("ordId")}, run_id)
        return {"run_id": run_id, "status": existing.get("state", "SUBMITTED").upper(), "tool": approved["tool"],
                "client_order_id": clord_id, "order_id": existing.get("ordId")}

    append_event("MCP_WRITE", "WARN", "OKX spot emri gönderiliyor",
                 {"tool": approved["tool"], "arguments": arguments}, run_id)
    try:
        rows = client.call(approved["tool"], **arguments)
    except Exception as exc:
        append_event("MCP_WRITE", "ERROR", "Spot emir çağrısı başarısız; durum sorgulanacak",
                     {"error": str(exc)[:300], "client_order_id": clord_id}, run_id)
        found = already_sent(client, symbol, clord_id)
        return {"run_id": run_id, "status": "UNKNOWN" if not found else "SUBMITTED", "tool": approved["tool"],
                "client_order_id": clord_id, "order_id": (found or {}).get("ordId")}

    row = (rows or [{}])[0] if isinstance(rows, list) else rows
    order_id = (row or {}).get("ordId")
    lookup = already_sent(client, symbol, clord_id) or {}
    execution = {"run_id": run_id, "status": (lookup.get("state") or "SUBMITTED").upper(), "tool": approved["tool"],
                 "client_order_id": clord_id, "order_id": order_id or lookup.get("ordId"),
                 "filled_notional_usdt": number(lookup.get("fillNotionalUsd") or lookup.get("accFillSz")),
                 "average_price": number(lookup.get("avgPx")), "sent_at": utc_now()}
    append_event("MCP_WRITE", "WARN", "OKX spot emri gerçekleşti",
                 {"order_id": execution["order_id"], "state": execution["status"],
                  "avg_price": execution["average_price"], "sz": arguments.get("sz")}, run_id)
    merge_state({"cycle": {"gate": gate, "execution": execution}})
    return execution


def flatten(client, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Sell every desk-owned position, one symbol at a time, through the gate.

    The watchdog decides that a flatten is required; this turns that decision
    into orders. It is deterministic on purpose — no model is consulted, and
    the gate still clamps each sell to the inventory the desk actually owns.
    """
    from watchdog import evaluate as evaluate_watchdog

    state = state or load_state()
    actions = [item for item in evaluate_watchdog(state).get("actions", [])
               if item.get("action") == "FLATTEN_AGENT_INVENTORY"]
    results = []
    for action in actions:
        symbol = action.get("symbol")
        position = next((p for p in state.get("account", {}).get("positions", [])
                         if p.get("symbol") == symbol), {})
        exposure = number(position.get("exposure_usdt"))
        price = exposure / number(action.get("base_amount")) if number(action.get("base_amount")) else 0.0
        if exposure <= 0:
            continue
        run_id = f"flatten-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{symbol.split('-')[0].lower()}"
        proposal = {
            "run_id": run_id, "symbol": symbol, "price": price, "action": "FLATTEN",
            "requested_notional_usdt": exposure, "confidence": 1.0, "smart_money_veto": False,
            "market_observed_at": utc_now(), "market_age_seconds": 0,
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
            "rationale_tr": "Kullanıcı flatten onayı; desk envanteri kapatılıyor.",
            "mcp_call": build_call({"run_id": run_id, "symbol": symbol, "action": "REDUCE"}),
        }
        merge_state({"cycle": {"run_id": run_id, "proposal": proposal,
                               "execution": {"run_id": run_id, "status": "NOT_SENT", "tool": None,
                                             "client_order_id": None, "order_id": None}}})
        execution = execute(client, load_state())
        results.append({"symbol": symbol, "exposure_usdt": exposure, **execution})
        state = load_state()
    if results:
        append_event("MCP_WRITE", "WARN", "Flatten emirleri gönderildi",
                     {"count": len(results), "symbols": [r["symbol"] for r in results]})
    return results
