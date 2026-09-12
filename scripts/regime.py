#!/usr/bin/env python3
"""Small deterministic RANGE / TREND / SHOCK classifier."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from journal import append_event, load_state, merge_state


CONFIDENCE_FLOOR = 0.65
UNIVERSE = ("BTC-USDT", "ETH-USDT", "SOL-USDT")


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def classify_symbol(item: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    features = item.get("features") or {}
    adx = number(features.get("adx"))
    atr = max(number(features.get("atr_pct")), 0.0001)
    ema20 = number(features.get("ema20"))
    ema50 = number(features.get("ema50"))
    price = max(number(item.get("price")), 0.0001)
    separation_atr = abs(ema20 - ema50) / price / atr
    volume_z = number(features.get("volume_zscore"))
    return_5m = abs(number(features.get("return_5m")))
    spread_multiple = number(features.get("spread_multiple"), 1.0)
    liquidity_drop = number(features.get("liquidity_drop_pct"))
    orderflow = number(features.get("orderflow"))
    crosses = int(number(features.get("vwap_crosses_12")))
    veto = bool(item.get("smart_money_veto"))
    negative_news = bool((item.get("news") or {}).get("high_impact_negative"))
    exposure = number(item.get("exposure_usdt"))

    shock = return_5m >= 2.5 * atr or volume_z >= 3 or spread_multiple >= 3 or liquidity_drop >= 0.5 or negative_news
    range_ready = adx <= 20 and separation_atr <= 0.5 and crosses >= 3 and spread_multiple < 3
    trend_up = adx >= 25 and ema20 > ema50 and bool(features.get("breakout_20")) and volume_z >= 1 and orderflow > 0
    trend_down = adx >= 25 and ema20 < ema50 and bool(features.get("breakdown_20")) and volume_z >= 1 and orderflow < 0

    if shock:
        candidate, direction, confidence = "SHOCK", "RISK_OFF", min(0.99, 0.72 + max(volume_z - 3, 0) * 0.04 + (0.08 if negative_news else 0))
    elif trend_up or trend_down or adx >= 25:
        candidate, direction = "TREND", "UP" if ema20 >= ema50 else "DOWN"
        confidence = min(0.92, 0.58 + max(adx - 25, 0) / 100 + max(volume_z, 0) / 20 + min(abs(orderflow), 1) * 0.08)
    else:
        candidate, direction = "RANGE", "NEUTRAL"
        confidence = min(0.90, 0.56 + max(20 - adx, 0) / 100 + min(crosses, 5) * 0.035 + max(0, 0.5 - separation_atr) * 0.08)

    previous = previous or {}
    previous_regime = previous.get("regime")
    previous_candidate = previous.get("candidate_regime")
    streak = int(previous.get("candidate_streak") or 0) + 1 if previous_candidate == candidate else 1
    regime = candidate if candidate == "SHOCK" or previous_regime is None or streak >= 2 else previous_regime

    action = "HOLD"
    rationale = "Rejim görünür, fakat giriş koşulları birlikte doğrulanmadı."
    if candidate == "SHOCK":
        action = "REDUCE" if exposure > 0 else "HOLD"
        rationale = "SHOCK önceliği etkin; yeni risk yok, açık agent inventory azaltılır."
    elif streak < 2:
        rationale = f"{candidate} adayı ilk kez görüldü; rejim değişimi için ikinci ardışık karar bekleniyor."
    elif candidate == "RANGE" and range_ready and confidence >= CONFIDENCE_FLOOR and not veto:
        action = "OPEN_GRID"
        rationale = "Düşük ADX, dar EMA ayrışması ve VWAP geçişleri RANGE koşullarını doğruluyor."
    elif candidate == "TREND" and trend_up and confidence >= CONFIDENCE_FLOOR and not veto:
        action = "BUY_BREAKOUT"
        rationale = "ADX, yukarı breakout, hacim ve order-flow aynı yönde TREND koşulunu doğruluyor."
    elif candidate == "TREND" and trend_down:
        action = "REDUCE" if exposure > 0 else "HOLD"
        rationale = "Aşağı TREND tespit edildi; spot-only desk short açmaz."
    elif veto:
        rationale = "Price/volume adayı oluştu ancak Smart Money vetosu yeni riski engelledi."
    elif confidence < CONFIDENCE_FLOOR:
        rationale = "Rejim sınıflandırıldı ancak confidence eşiğin altında; HOLD."

    result = dict(item)
    result.update(regime=regime, candidate_regime=candidate, candidate_streak=streak, direction=direction, confidence=round(confidence, 4), last_action=action, rationale_tr=rationale)
    return result


def classify_state(state: dict[str, Any], now: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    previous = {item.get("symbol"): item for item in state.get("symbols", [])}
    incoming = state.get("observation_symbols") or state.get("symbols", [])
    symbols = [classify_symbol(item, previous.get(item.get("symbol"))) for item in incoming if item.get("symbol") in UNIVERSE]

    grid_candidates = [item for item in symbols if item["last_action"] == "OPEN_GRID"]
    if len(grid_candidates) > 1:
        winner = max(grid_candidates, key=lambda item: item["confidence"])["symbol"]
        for item in symbols:
            if item["last_action"] == "OPEN_GRID" and item["symbol"] != winner:
                item["last_action"] = "HOLD"
                item["rationale_tr"] = f"Tek-grid kuralında {winner} daha yüksek confidence aldığı için HOLD."

    priority = {"REDUCE": 3, "BUY_BREAKOUT": 2, "OPEN_GRID": 1, "HOLD": 0}
    selected = max(symbols, key=lambda item: (priority[item["last_action"]], item["confidence"]), default=None)
    run_id = f"{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    if not selected:
        proposal = {"run_id": run_id, "action": "HOLD", "requested_notional_usdt": 0, "rationale_tr": "Geçerli observation yok."}
    else:
        requested = 0.0
        if selected["last_action"] in {"OPEN_GRID", "BUY_BREAKOUT"}:
            requested = number(state.get("account", {}).get("nav")) * 0.20
        elif selected["last_action"] == "REDUCE":
            requested = number(selected.get("exposure_usdt")) * 0.50
        proposal = {
            "run_id": run_id,
            "symbol": selected["symbol"],
            "regime": selected["regime"],
            "direction": selected["direction"],
            "confidence": selected["confidence"],
            "action": selected["last_action"],
            "requested_notional_usdt": round(requested, 8),
            "stop_distance_pct": max(0.01, number(selected.get("features", {}).get("atr_pct")) * 1.5),
            "market_age_seconds": number(selected.get("market_age_seconds"), 9999),
            "smart_money_veto": bool(selected.get("smart_money_veto")),
            "rationale_tr": selected["rationale_tr"],
            "expires_at": (now + timedelta(seconds=90)).isoformat(),
        }
    return symbols, proposal


def main() -> int:
    state = load_state()
    symbols, proposal = classify_state(state)
    merge_state({"symbols": symbols, "cycle": {"run_id": proposal["run_id"], "proposal": proposal, "gate": {"verdict": "HOLD", "allowed_notional_usdt": 0, "reason_codes": ["NOT_EVALUATED"]}, "execution": {"status": "NOT_SENT", "tool": None, "client_order_id": None, "order_id": None}}})
    append_event("DECISION", "WARN" if proposal["action"] == "REDUCE" else "INFO", proposal["rationale_tr"], {"symbol": proposal.get("symbol"), "regime": proposal.get("regime"), "action": proposal["action"], "confidence": proposal.get("confidence")}, proposal["run_id"])
    print(json.dumps(proposal, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

