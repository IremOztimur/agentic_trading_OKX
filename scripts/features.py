#!/usr/bin/env python3
"""Feature calculations for OKX candle and order-book observations."""

from __future__ import annotations

from statistics import mean, pstdev
from typing import Any


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "result", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = rows(value)
                if nested:
                    return nested
    return []


def candle_rows(payload: Any) -> list[list[float]]:
    parsed = []
    for item in rows(payload):
        if not isinstance(item, (list, tuple)) or len(item) < 6:
            continue
        try:
            parsed.append([float(item[index]) for index in range(6)])
        except (TypeError, ValueError):
            continue
    parsed.sort(key=lambda item: item[0])
    return parsed


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha, value = 2 / (period + 1), values[0]
    for current in values[1:]:
        value = alpha * current + (1 - alpha) * value
    return value


def atr(candles: list[list[float]], period: int = 14) -> float:
    values = [max(cur[2] - cur[3], abs(cur[2] - prev[4]), abs(cur[3] - prev[4])) for prev, cur in zip(candles, candles[1:])]
    return mean(values[-period:]) if values else 0.0


def directional_index(plus_dm: list[float], minus_dm: list[float], true_ranges: list[float]) -> float:
    tr = sum(true_ranges)
    if tr <= 0:
        return 0.0
    plus_di, minus_di = 100 * sum(plus_dm) / tr, 100 * sum(minus_dm) / tr
    return 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if plus_di + minus_di else 0.0


def adx(candles: list[list[float]], period: int = 14) -> float:
    """ADX is the average of DX over `period` bars; a single DX reads 100 on a
    flat tape where one directional movement is zero, so it must be smoothed."""
    plus_dm, minus_dm, true_ranges = [], [], []
    for previous, current in zip(candles, candles[1:]):
        up, down = current[2] - previous[2], previous[3] - current[3]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        true_ranges.append(max(current[2] - current[3], abs(current[2] - previous[4]), abs(current[3] - previous[4])))
    if len(true_ranges) < period:
        return directional_index(plus_dm, minus_dm, true_ranges)
    windows = [
        directional_index(plus_dm[end - period:end], minus_dm[end - period:end], true_ranges[end - period:end])
        for end in range(period, len(true_ranges) + 1)
    ]
    return mean(windows[-period:]) if windows else 0.0


def book_rows(payload: Any) -> tuple[list[Any], list[Any]]:
    data = payload
    if isinstance(data, dict) and isinstance(data.get("data"), list) and data["data"]:
        data = data["data"][0]
    return (data.get("bids") or [], data.get("asks") or []) if isinstance(data, dict) else ([], [])


def microstructure(book_payload: Any, trades_payload: Any = None, previous_spread: float = 0.0) -> dict[str, float]:
    bids, asks = book_rows(book_payload)
    bid_qty = sum(float(item[1]) for item in bids[:5]) if bids else 0.0
    ask_qty = sum(float(item[1]) for item in asks[:5]) if asks else 0.0
    orderflow = (bid_qty - ask_qty) / (bid_qty + ask_qty) if bid_qty + ask_qty else 0.0
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    midpoint = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    spread = max(0.0, best_ask - best_bid) / midpoint if midpoint else 0.0
    baseline = previous_spread * 0.9 + spread * 0.1 if previous_spread else spread
    buy_volume = sell_volume = 0.0
    for trade in rows(trades_payload):
        if not isinstance(trade, dict):
            continue
        size = float(trade.get("sz") or trade.get("size") or 0)
        if str(trade.get("side", "")).lower() == "buy":
            buy_volume += size
        elif str(trade.get("side", "")).lower() == "sell":
            sell_volume += size
    total = buy_volume + sell_volume
    trade_imbalance = (buy_volume - sell_volume) / total if total else 0.0
    return {
        "orderflow": round(orderflow, 4), "trade_imbalance": round(trade_imbalance, 4),
        "spread_pct": round(spread, 8), "spread_baseline_pct": round(baseline, 8),
        "spread_multiple": round(spread / baseline, 4) if baseline else 1.0,
    }


def build_features(candle_payload: Any, book_payload: Any) -> tuple[float, dict[str, Any]]:
    candles = candle_rows(candle_payload)
    if len(candles) < 25:
        raise ValueError("En az 25 geçerli candle gerekli")
    closes, volumes = [item[4] for item in candles], [item[5] for item in candles]
    current, atr_value = closes[-1], atr(candles)
    recent_volumes = volumes[-20:]
    deviation = pstdev(recent_volumes) if len(recent_volumes) > 1 else 0
    volume_z = (volumes[-1] - mean(recent_volumes)) / deviation if deviation else 0.0
    typical = [(item[2] + item[3] + item[4]) / 3 for item in candles]
    vwap_values = []
    for end in range(max(1, len(candles) - 11), len(candles) + 1):
        total = sum(volumes[:end])
        vwap_values.append(sum(price * volume for price, volume in zip(typical[:end], volumes[:end])) / total if total else typical[end - 1])
    sides = [close >= vwap for close, vwap in zip(closes[-len(vwap_values):], vwap_values)]
    micro = microstructure(book_payload)
    return current, {
        "adx": round(adx(candles), 4), "atr_pct": round(atr_value / current, 8),
        "ema20": round(ema(closes, 20), 6), "ema50": round(ema(closes, 50), 6),
        "breakout_20": current > max(closes[-21:-1]), "breakdown_20": current < min(closes[-21:-1]),
        "volume_zscore": round(volume_z, 4), "return_5m": round(current / closes[-6] - 1, 8),
        **micro, "liquidity_drop_pct": 0.0,
        "vwap_crosses_12": sum(left != right for left, right in zip(sides, sides[1:])),
    }
