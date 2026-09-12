#!/usr/bin/env python3
"""Perception layer: five sensors, every one an OKX ATK MCP call.

Each sensor emits the same record — a score in [-1, +1] signed toward risk-on,
a weight, and a timestamp. Freshness is applied at decision time, so a sensor
that stops updating fades out of the decision instead of poisoning it.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from features import build_features, microstructure

# Nominal weights. Trend and smart money dominate; volatility mostly guards.
WEIGHTS = {"trend": 0.30, "smart_money": 0.25, "microstructure": 0.20, "momentum": 0.15, "volatility": 0.10}
# Seconds after which a sensor contributes nothing at all.
TTL = {"trend": 120, "smart_money": 420, "microstructure": 45, "momentum": 120, "volatility": 120}
SENSOR_IDS = tuple(WEIGHTS)
MIN_QUORUM = 0.6
SPREAD_VETO_MULTIPLE = 3.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clip(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def record(sensor_id: str, score: float, source: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sensor_id,
        "score": round(clip(score), 4),
        "weight": WEIGHTS[sensor_id],
        "ttl_s": TTL[sensor_id],
        "observed_at": utc_now(),
        "source": source,
        "evidence": evidence,
    }


def freshness(sensor: dict[str, Any], now: datetime | None = None) -> float:
    """1.0 when just observed, decaying linearly to 0.0 at the sensor's TTL."""
    now = now or datetime.now(timezone.utc)
    observed = sensor.get("observed_at")
    if not observed:
        return 0.0
    try:
        age = (now - datetime.fromisoformat(str(observed).replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return 0.0
    ttl = max(1.0, number(sensor.get("ttl_s"), 60))
    return max(0.0, min(1.0, 1 - age / ttl))


def indicator_values(payload: Any, name: str, bar: str) -> dict[str, Any]:
    """Dig the values dict out of the nested market_get_indicator response."""
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if not isinstance(row, dict):
            continue
        for entry in row.get("data") or []:
            frames = (entry or {}).get("timeframes") or {}
            indicators = (frames.get(bar) or {}).get("indicators") or {}
            for key, series in indicators.items():
                if key.upper() == name.upper() and series:
                    return (series[0] or {}).get("values") or {}
    return {}


# -- sensors --------------------------------------------------------------

def sense_trend(client, symbol: str, price: float) -> dict[str, Any]:
    """Supertrend gives the direction, ADX the strength. No direction, no score.

    Both are read on 15m: a 1m ADX flips too often to define a regime."""
    from features import adx as adx_of, candle_rows

    adx = adx_of(candle_rows(client.call("market_get_candles", instId=symbol, bar="15m", limit="100")))
    values = indicator_values(
        client.call("market_get_indicator", instId=symbol, indicator="supertrend", bar="15m"),
        "SUPERTREND", "15m",
    )
    trend = str(values.get("trend") or "").upper()
    direction = 1.0 if trend == "UP" else -1.0 if trend == "DOWN" else 0.0
    strength = clip((adx - 20) / 30, 0.0, 1.0)
    return record("trend", direction * strength, "atk:market_get_indicator/supertrend",
                  {"trend": trend or "UNKNOWN", "adx": round(adx, 2), "superTrend": values.get("superTrend"), "price": price})


def sense_momentum(client, symbol: str) -> dict[str, Any]:
    values = indicator_values(
        client.call("market_get_indicator", instId=symbol, indicator="rsi", bar="15m"),
        "RSI", "15m",
    )
    rsi = number(next(iter(values.values()), None), 50.0)
    return record("momentum", (rsi - 50) / 50, "atk:market_get_indicator/rsi", {"rsi": round(rsi, 2)})


def sense_microstructure(client, symbol: str, previous_spread: float) -> tuple[dict[str, Any], dict[str, float]]:
    book = client.call("market_get_orderbook", instId=symbol, sz="20")
    trades = client.call("market_get_trades", instId=symbol, limit="50")
    micro = microstructure({"data": book}, trades, previous_spread)
    score = (micro["orderflow"] + micro["trade_imbalance"]) / 2
    sensor = record("microstructure", score, "atk:market_get_orderbook+market_get_trades",
                    {"orderflow": micro["orderflow"], "trade_imbalance": micro["trade_imbalance"],
                     "spread_multiple": micro["spread_multiple"]})
    return sensor, micro


def sense_volatility(client, symbol: str) -> tuple[dict[str, Any], float, dict[str, Any]]:
    candles = client.call("market_get_candles", instId=symbol, bar="1m", limit="100")
    price, features = build_features(candles, {"data": []})
    atr = max(features.get("atr_pct") or 0.0, 0.0001)
    # ATR is a 1m figure; scale it to the 5m return horizon before comparing.
    move = number(features.get("return_5m")) / (atr * math.sqrt(5))
    volume_z = number(features.get("volume_zscore"))
    # Directional score: a move is only meaningful when volume backs it.
    score = clip(move / 2.5) * clip(0.5 + volume_z / 4, 0.0, 1.0)
    sensor = record("volatility", score, "atk:market_get_candles",
                    {"return_5m": features.get("return_5m"), "atr_pct": features.get("atr_pct"),
                     "volume_zscore": volume_z, "move_atr": round(move, 3)})
    return sensor, price, features


def sense_smart_money(client, symbol: str) -> dict[str, Any]:
    base = symbol.split("-")[0]
    rows = client.call("smartmoney_get_signal_overview_by_filter", sortBy="pnlRatio", period="7",
                       instCcyList=[base], pnlTier="PNL_TOP20")
    row = next((item for item in (rows or []) if isinstance(item, dict) and item.get("ccy") == base), {})
    ratios = row.get("longShortRatio") or {}
    notional = row.get("notional") or {}
    weighted_long = number(ratios.get("weightedLongRatio"), 0.5)
    score = 2 * weighted_long - 1
    # A net position that agrees with the ratio deepens conviction; disagreement damps it.
    net = number(notional.get("netNotionalUsdt"))
    if net and score:
        score *= 1.15 if (net > 0) == (score > 0) else 0.7
    score += clip(number(ratios.get("longRatioVs24h")) * 2, -0.2, 0.2)
    return record("smart_money", score, "atk:smartmoney_get_signal_overview_by_filter",
                  {"weightedLongRatio": round(weighted_long, 4), "netNotionalUsdt": round(net, 2),
                   "longRatioVs24h": ratios.get("longRatioVs24h"), "tradersWithPosition": row.get("tradersWithPosition")})


# -- cadence tiers --------------------------------------------------------

def refresh_fast(client, symbol: str, previous: dict[str, Any]) -> dict[str, Any]:
    """5s tier: order book and tape. Short-term confirmation, not order-flow trading."""
    ticker = client.call("market_get_ticker", instId=symbol)
    last = number((ticker or [{}])[0].get("last")) if ticker else 0.0
    previous_spread = number((previous.get("features") or {}).get("spread_baseline_pct"))
    sensor, micro = sense_microstructure(client, symbol, previous_spread)
    return {
        "price": last or previous.get("price"),
        "market_observed_at": utc_now(),
        "sensors": {"microstructure": sensor},
        "features": micro,
    }


def refresh_mid(client, symbol: str, previous: dict[str, Any]) -> dict[str, Any]:
    """30s tier: candles and indicators."""
    volatility, price, features = sense_volatility(client, symbol)
    trend = sense_trend(client, symbol, price)
    momentum = sense_momentum(client, symbol)
    return {
        "price": price,
        "market_observed_at": utc_now(),
        "sensors": {"volatility": volatility, "trend": trend, "momentum": momentum},
        "features": features,
    }


def refresh_slow(client, symbol: str, previous: dict[str, Any]) -> dict[str, Any]:
    """120s tier: smart money consensus."""
    return {"sensors": {"smart_money": sense_smart_money(client, symbol)}}


def merge_observation(previous: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    merged["sensors"] = {**(previous.get("sensors") or {}), **(patch.get("sensors") or {})}
    merged["features"] = {**(previous.get("features") or {}), **(patch.get("features") or {})}
    for key, value in patch.items():
        if key not in ("sensors", "features") and value is not None:
            merged[key] = value
    return merged


def quorum(sensors: dict[str, Any], now: datetime | None = None) -> tuple[float, float]:
    """Live weight versus nominal weight — the desk holds when too much has gone stale."""
    nominal = sum(WEIGHTS.values())
    live = sum(WEIGHTS[key] * freshness(sensor, now) for key, sensor in sensors.items() if key in WEIGHTS)
    return live, nominal


def spread_veto(features: dict[str, Any]) -> bool:
    return number(features.get("spread_multiple"), 1.0) >= SPREAD_VETO_MULTIPLE
