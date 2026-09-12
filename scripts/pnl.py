#!/usr/bin/env python3
"""Position and portfolio P&L computed from real OKX fills.

Average entry comes from the fill history, not from state, so the numbers
survive a runner restart and match what the exchange actually charged.
"""

from __future__ import annotations

from typing import Any

from perception import number

FEE_NOTE = "OKX taker fee is charged per fill; entry and exit each cost roughly 0.1%."


def fills_for(client, symbol: str, limit: str = "100") -> list[dict[str, Any]]:
    try:
        rows = client.call("spot_get_fills", instId=symbol, limit=limit)
    except Exception:
        return []
    return [row for row in (rows or []) if isinstance(row, dict)]


def last_price(client, symbol: str) -> float:
    try:
        rows = client.call("market_get_ticker", instId=symbol)
    except Exception:
        return 0.0
    return number((rows or [{}])[0].get("last")) if rows else 0.0


def summarize_fills(fills: list[dict[str, Any]]) -> dict[str, float]:
    """Aggregate buys, sells and fees. Fees are normalized into USDT."""
    bought = bought_quote = sold = sold_quote = fees_usdt = 0.0
    for fill in fills:
        size, price = number(fill.get("fillSz")), number(fill.get("fillPx"))
        if size <= 0 or price <= 0:
            continue
        if str(fill.get("side")).lower() == "buy":
            bought += size
            bought_quote += size * price
        else:
            sold += size
            sold_quote += size * price
        fee = abs(number(fill.get("fee")))
        # A base-currency fee (the usual case on a buy) is worth fee * price in USDT.
        fees_usdt += fee if str(fill.get("feeCcy")).upper() == "USDT" else fee * price
    return {"bought": bought, "bought_quote": bought_quote, "sold": sold,
            "sold_quote": sold_quote, "fees_usdt": fees_usdt}


def position_pnl(client, symbol: str, held_base: float) -> dict[str, Any]:
    """Unrealized and realized P&L for one symbol, in USDT."""
    fills = fills_for(client, symbol)
    totals = summarize_fills(fills)
    price = last_price(client, symbol)
    average_entry = totals["bought_quote"] / totals["bought"] if totals["bought"] else 0.0
    market_value = held_base * price
    cost_basis = held_base * average_entry
    unrealized = market_value - cost_basis
    realized = totals["sold_quote"] - totals["sold"] * average_entry if totals["sold"] else 0.0
    return {
        "symbol": symbol,
        "held_base": round(held_base, 10),
        "average_entry": round(average_entry, 6),
        "last_price": round(price, 6),
        "market_value_usdt": round(market_value, 6),
        "unrealized_pnl_usdt": round(unrealized, 6),
        "unrealized_pnl_pct": round((price / average_entry - 1) * 100, 4) if average_entry else 0.0,
        "realized_pnl_usdt": round(realized, 6),
        "fees_paid_usdt": round(totals["fees_usdt"], 6),
        "net_pnl_usdt": round(unrealized + realized - totals["fees_usdt"], 6),
        "fill_count": len(fills),
    }


def portfolio_pnl(client, state: dict[str, Any]) -> dict[str, Any]:
    """Whole-desk P&L: NAV against session start, plus every open position."""
    account = state.get("account", {})
    session = state.get("session", {})
    nav = number(account.get("nav"))
    starting_nav = number(session.get("starting_nav")) or nav
    positions = [
        position_pnl(client, item.get("symbol"), number(item.get("base_amount")))
        for item in account.get("positions", []) if item.get("symbol")
    ]
    return {
        "mode": session.get("mode"),
        "nav_usdt": round(nav, 6),
        "starting_nav_usdt": round(starting_nav, 6),
        "session_pnl_usdt": round(nav - starting_nav, 6),
        "session_pnl_pct": round((nav / starting_nav - 1) * 100, 4) if starting_nav else 0.0,
        "available_usdt": round(number(account.get("available_usdt")), 6),
        "total_exposure_usdt": round(number(account.get("total_exposure_usdt")), 6),
        "total_fees_usdt": round(sum(p["fees_paid_usdt"] for p in positions), 6),
        "positions": positions,
        "note": FEE_NOTE,
    }
