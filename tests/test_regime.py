import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import regime


def base(**features):
    values = {"adx": 15, "atr_pct": 0.01, "ema20": 100, "ema50": 100.2, "volume_zscore": 0.2, "return_5m": 0.005, "spread_multiple": 1, "liquidity_drop_pct": 0, "orderflow": 0.1, "vwap_crosses_12": 4, "breakout_20": False, "breakdown_20": False}
    values.update(features)
    return {"symbol": "BTC-USDT", "price": 100, "features": values, "smart_money_veto": False, "news": {"high_impact_negative": False}, "exposure_usdt": 0, "market_age_seconds": 1}


class RegimeTests(unittest.TestCase):
    def test_range_needs_two_consecutive_observations(self):
        first = regime.classify_symbol(base())
        self.assertEqual(first["regime"], "RANGE")
        self.assertEqual(first["last_action"], "HOLD")
        second = regime.classify_symbol(base(), first)
        self.assertEqual(second["last_action"], "OPEN_GRID")

    def test_uptrend_and_smart_money_veto(self):
        item = base(adx=30, ema20=105, ema50=100, volume_zscore=1.5, orderflow=.4, breakout_20=True)
        first = regime.classify_symbol(item)
        second = regime.classify_symbol(item, first)
        self.assertEqual(second["regime"], "TREND")
        self.assertEqual(second["direction"], "UP")
        self.assertEqual(second["last_action"], "BUY_BREAKOUT")
        item["smart_money_veto"] = True
        vetoed = regime.classify_symbol(item, second)
        self.assertEqual(vetoed["last_action"], "HOLD")

    def test_downtrend_never_shorts(self):
        item = base(adx=32, ema20=95, ema50=100, volume_zscore=1.4, orderflow=-.5, breakdown_20=True)
        item["exposure_usdt"] = 0
        second = regime.classify_symbol(item, regime.classify_symbol(item))
        self.assertEqual(second["regime"], "TREND")
        self.assertEqual(second["direction"], "DOWN")
        self.assertEqual(second["last_action"], "HOLD")

    def test_shock_is_immediate_and_reduces(self):
        item = base(volume_zscore=3.2)
        item["exposure_usdt"] = 800
        result = regime.classify_symbol(item, {"regime": "RANGE", "candidate_regime": "RANGE", "candidate_streak": 4})
        self.assertEqual(result["regime"], "SHOCK")
        self.assertEqual(result["candidate_streak"], 1)
        self.assertEqual(result["last_action"], "REDUCE")

    def test_only_one_grid_candidate_survives(self):
        a = regime.classify_symbol(base(), regime.classify_symbol(base()))
        b_raw = base()
        b_raw["symbol"] = "ETH-USDT"
        b = regime.classify_symbol(b_raw, regime.classify_symbol(b_raw))
        state = {"account": {"nav": 10000}, "symbols": [a, b], "observation_symbols": [a, b]}
        symbols, proposal = regime.classify_state(state, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(sum(item["last_action"] == "OPEN_GRID" for item in symbols), 1)
        self.assertEqual(proposal["action"], "OPEN_GRID")

    def test_shock_suppresses_entry_on_other_symbol(self):
        shock = base(volume_zscore=3.5)
        trend = base(adx=35, ema20=110, ema50=100, volume_zscore=2, orderflow=.5, breakout_20=True)
        trend["symbol"] = "ETH-USDT"
        previous_trend = regime.classify_symbol(trend)
        state = {"account": {"nav": 10000}, "symbols": [previous_trend], "observation_symbols": [shock, trend]}
        _, proposal = regime.classify_state(state, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(proposal["symbol"], "BTC-USDT")
        self.assertEqual(proposal["regime"], "SHOCK")
        self.assertEqual(proposal["action"], "HOLD")


if __name__ == "__main__":
    unittest.main()
