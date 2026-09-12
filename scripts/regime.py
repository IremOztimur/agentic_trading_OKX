#!/usr/bin/env python3
"""Decision layer: sensor scores in, one explainable spot decision out.

No LLM runs here. The regime is an argmax over three composites, the action is
a table lookup, and the size is proportional to conviction. Every decision
carries the per-sensor contributions that produced it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from journal import append_event, load_state, merge_state
from perception import MIN_QUORUM, WEIGHTS, clip, freshness, number, quorum, spread_veto

UNIVERSE = ("BTC-USDT", "ETH-USDT", "SOL-USDT")

SHOCK_FLOOR = 0.60          # shock_level at or above this preempts everything
REGIME_MARGIN = 0.10        # the winner must beat the runner-up by this much
CONVICTION_ENTRY = 0.35     # |conviction| needed to act at all
BASE_RISK_FRACTION = 0.20   # of NAV; the risk gate clamps this down
DECISION_TTL_SECONDS = 90
DIRECTIONAL = ("trend", "momentum", "microstructure", "smart_money", "volatility")


def effective(sensors: dict[str, Any], now: datetime | None = None) -> dict[str, dict[str, float]]:
    """Weight every sensor by its freshness; stale sensors fall out entirely."""
    live = {}
    for key in DIRECTIONAL:
        sensor = sensors.get(key)
        if not sensor:
            continue
        fresh = freshness(sensor, now)
        live[key] = {"score": number(sensor.get("score")), "weight": WEIGHTS[key],
                     "freshness": round(fresh, 4), "effective_weight": WEIGHTS[key] * fresh}
    return live


def composites(sensors: dict[str, Any], features: dict[str, Any], live: dict[str, dict[str, float]]) -> dict[str, float]:
    """Three numbers decide the regime: shock, trend, range."""
    volatility = (sensors.get("volatility") or {}).get("evidence") or {}
    move_atr = abs(number(volatility.get("move_atr")))
    volume_z = number(volatility.get("volume_zscore"))
    spread_multiple = number(features.get("spread_multiple"), 1.0)
    # A wide spread is a liquidity guard first; only an extreme one is a shock,
    # so it must not trip SHOCK before `spread_veto` gets to block the entry.
    shock_level = clip(max(move_atr / 3.0, volume_z / 4.0, (spread_multiple - 1) / 6.0), 0.0, 1.0)

    trend = live.get("trend") or {}
    trend_strength = abs(trend.get("score", 0.0)) * trend.get("freshness", 0.0)
    range_quality = clip(1 - trend_strength - shock_level / 2, 0.0, 1.0)
    return {"shock_level": round(shock_level, 4), "trend_strength": round(trend_strength, 4),
            "range_quality": round(range_quality, 4)}


def conviction_of(live: dict[str, dict[str, float]], regime: str) -> tuple[float, list[dict[str, Any]]]:
    """Freshness-weighted mean of the directional sensors, plus the breakdown."""
    total = sum(item["effective_weight"] for item in live.values())
    if total <= 0:
        return 0.0, []
    contributions = []
    conviction = 0.0
    for key, item in live.items():
        score = item["score"]
        # In RANGE a stretched RSI is a fade signal, not a follow signal.
        if key == "momentum" and regime == "RANGE":
            score = -score
        delta = score * item["effective_weight"] / total
        conviction += delta
        contributions.append({"id": key, "score": round(score, 4), "weight": item["weight"],
                              "freshness": item["freshness"], "delta": round(delta, 4)})
    contributions.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return clip(conviction), contributions


def classify_symbol(item: dict[str, Any], previous: dict[str, Any] | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    sensors = item.get("sensors") or {}
    features = item.get("features") or {}
    exposure = number(item.get("exposure_usdt"))
    live = effective(sensors, now)
    scores = composites(sensors, features, live)

    if scores["shock_level"] >= SHOCK_FLOOR:
        candidate = "SHOCK"
    elif scores["trend_strength"] >= scores["range_quality"] + REGIME_MARGIN:
        candidate = "TREND"
    elif scores["range_quality"] >= scores["trend_strength"] + REGIME_MARGIN:
        candidate = "RANGE"
    else:
        candidate = (previous or {}).get("candidate_regime") or "RANGE"

    previous = previous or {}
    previous_regime = previous.get("regime")
    streak = int(previous.get("candidate_streak") or 0) + 1 if previous.get("candidate_regime") == candidate else 1
    # SHOCK is immediate; every other flip waits for a second agreeing cycle.
    regime = candidate if candidate == "SHOCK" or previous_regime is None or streak >= 2 else previous_regime

    conviction, contributions = conviction_of(live, regime)
    live_weight, nominal = quorum(sensors, now)
    quorum_ok = live_weight >= MIN_QUORUM * nominal
    smart_money = sensors.get("smart_money")
    smart_money_fresh = bool(smart_money) and freshness(smart_money, now) > 0

    action, reason = "HOLD", ""
    if regime == "SHOCK":
        action = "REDUCE" if exposure > 0 else "HOLD"
        reason = "SHOCK rejimi: yeni risk yok." + (" Mevcut pozisyon azaltılıyor." if exposure > 0 else "")
    elif not quorum_ok:
        reason = f"Sensor quorum kayıp ({live_weight:.2f}/{nominal:.2f}); fail-closed HOLD."
    elif spread_veto(features):
        reason = "Spread normalin 3 katından geniş; giriş bloklandı."
    elif streak < 2:
        reason = f"{candidate} adayı ilk kez görüldü; rejim değişimi ikinci ardışık döngüyü bekliyor."
    elif regime == "RANGE":
        reason = f"RANGE rejimi işlem yapılmayan durumdur (range_quality {scores['range_quality']:.2f})."
    elif regime == "TREND" and conviction >= CONVICTION_ENTRY:
        action = "BUY" if smart_money_fresh else "HOLD"
        reason = (f"TREND rejimi {conviction:+.2f} conviction ile alım koşulunu doğruluyor."
                  if smart_money_fresh else "TREND adayı oluştu ancak Smart Money verisi stale; yeni pozisyon yok.")
    elif regime == "TREND" and conviction <= -CONVICTION_ENTRY:
        action = "REDUCE" if exposure > 0 else "HOLD"
        reason = ("Aşağı yönlü TREND; spot-only desk short açmaz, pozisyon azaltılıyor."
                  if exposure > 0 else "Aşağı yönlü TREND; pozisyon olmadığı için beklemede.")
    else:
        reason = f"{regime} rejimi, conviction {conviction:+.2f} eşiğin ({CONVICTION_ENTRY}) altında; HOLD."

    size_multiplier = clip((abs(conviction) - CONVICTION_ENTRY) / (1 - CONVICTION_ENTRY), 0.0, 1.0) if action == "BUY" else 0.0
    result = dict(item)
    result.pop("sensors", None)
    result.update(
        regime=regime, candidate_regime=candidate, candidate_streak=streak,
        direction="UP" if conviction > 0 else "DOWN" if conviction < 0 else "NEUTRAL",
        conviction=round(conviction, 4),
        # risk_gate reads `confidence`; conviction at the entry floor maps just above it.
        confidence=round(0.5 + abs(conviction) / 2, 4),
        scores=scores, contributions=contributions, sensors=sensors,
        live_weight=round(live_weight, 4), quorum_ok=quorum_ok,
        smart_money_veto=not smart_money_fresh,
        technical_action=action, last_action=action, size_multiplier=round(size_multiplier, 4),
        rationale_tr=reason,
        shock_reasons=shock_reasons(scores, features),
    )
    return result


def shock_reasons(scores: dict[str, float], features: dict[str, Any]) -> list[str]:
    reasons = []
    if scores["shock_level"] >= SHOCK_FLOOR:
        reasons.append("VOLATILITY_SHOCK")
    if number(features.get("spread_multiple"), 1.0) >= 3:
        reasons.append("SPREAD_SPIKE")
    return reasons


def classify_state(state: dict[str, Any], now: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    previous = {item.get("symbol"): item for item in state.get("symbols", [])}
    incoming = state.get("observation_symbols") or state.get("symbols", [])
    symbols = [classify_symbol(item, previous.get(item.get("symbol")), now)
               for item in incoming if item.get("symbol") in UNIVERSE]

    priority = {"REDUCE": 2, "BUY": 1, "HOLD": 0}
    selected = max(symbols, key=lambda item: (4 if item["regime"] == "SHOCK" else priority[item["technical_action"]],
                                              abs(item["conviction"])), default=None)
    run_id = f"{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    if not selected:
        return symbols, {"run_id": run_id, "action": "HOLD", "requested_notional_usdt": 0,
                         "rationale_tr": "Geçerli observation yok.", "contributions": []}

    nav = number(state.get("account", {}).get("nav"))
    requested = 0.0
    if selected["technical_action"] == "BUY":
        requested = nav * BASE_RISK_FRACTION * selected["size_multiplier"]
    elif selected["technical_action"] == "REDUCE":
        requested = number(selected.get("exposure_usdt")) * 0.50

    proposal = {
        "run_id": run_id,
        "symbol": selected["symbol"],
        "price": selected.get("price"),
        "regime": selected["regime"],
        "direction": selected["direction"],
        "conviction": selected["conviction"],
        "confidence": selected["confidence"],
        "action": selected["last_action"],
        "candidate_action": selected["technical_action"],
        "requested_notional_usdt": round(requested, 8),
        "stop_distance_pct": max(0.01, number(selected.get("features", {}).get("atr_pct")) * 1.5),
        "market_age_seconds": number(selected.get("market_age_seconds"), 9999),
        "market_observed_at": selected.get("market_observed_at"),
        "smart_money_veto": selected["smart_money_veto"],
        "shock_reasons": list(selected.get("shock_reasons") or []),
        "scores": selected["scores"],
        "contributions": selected["contributions"],
        "rationale_tr": selected["rationale_tr"],
        "expires_at": (now + timedelta(seconds=DECISION_TTL_SECONDS)).isoformat(),
    }
    return symbols, proposal


def main() -> int:
    symbols, proposal = classify_state(load_state())
    merge_state({"symbols": symbols, "cycle": {"run_id": proposal["run_id"], "proposal": proposal}})
    append_event("DECISION", "WARN" if proposal["action"] == "REDUCE" else "INFO", proposal["rationale_tr"],
                 {"symbol": proposal.get("symbol"), "regime": proposal.get("regime"),
                  "action": proposal["action"], "conviction": proposal.get("conviction")}, proposal["run_id"])
    print(json.dumps(proposal, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
