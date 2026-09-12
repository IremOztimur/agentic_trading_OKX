import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import perception
import regime

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


def sensor(sensor_id, score, age=0.0, evidence=None):
    return {
        "id": sensor_id, "score": score, "weight": perception.WEIGHTS[sensor_id],
        "ttl_s": perception.TTL[sensor_id], "source": "test", "evidence": evidence or {},
        "observed_at": (NOW - timedelta(seconds=age)).isoformat(),
    }


def observation(symbol="BTC-USDT", trend=0.0, momentum=0.0, micro=0.0, volatility=0.0,
                smart_money=0.0, move_atr=0.0, volume_z=0.0, spread_multiple=1.0,
                exposure=0.0, ages=None):
    ages = ages or {}
    return {
        "symbol": symbol, "price": 100.0, "exposure_usdt": exposure,
        "market_observed_at": NOW.isoformat(), "market_age_seconds": 0,
        "features": {"atr_pct": 0.004, "spread_multiple": spread_multiple, "minSz": "0.0001", "instrument_state": "live"},
        "sensors": {
            "trend": sensor("trend", trend, ages.get("trend", 0)),
            "momentum": sensor("momentum", momentum, ages.get("momentum", 0)),
            "microstructure": sensor("microstructure", micro, ages.get("microstructure", 0)),
            "volatility": sensor("volatility", volatility, ages.get("volatility", 0),
                                 {"move_atr": move_atr, "volume_zscore": volume_z}),
            "smart_money": sensor("smart_money", smart_money, ages.get("smart_money", 0)),
        },
    }


def state_of(*observations, nav=1000.0):
    return {"account": {"nav": nav, "positions": []}, "symbols": [], "observation_symbols": list(observations)}


def settle(item, cycles=2):
    """Run the classifier repeatedly so hysteresis can confirm the regime."""
    previous = None
    for _ in range(cycles):
        previous = regime.classify_symbol(item, previous, NOW)
    return previous


class RegimeScoringTests(unittest.TestCase):
    def test_regime_flip_needs_two_consecutive_cycles(self):
        item = observation(trend=0.9, smart_money=0.6, micro=0.4)
        first = regime.classify_symbol(item, None, NOW)
        self.assertEqual(first["regime"], "TREND")  # no prior regime to hold on to
        cold = regime.classify_symbol(item, {"regime": "RANGE", "candidate_regime": "RANGE"}, NOW)
        self.assertEqual(cold["regime"], "RANGE")
        self.assertEqual(cold["candidate_regime"], "TREND")
        self.assertEqual(cold["last_action"], "HOLD")
        warm = regime.classify_symbol(item, cold, NOW)
        self.assertEqual(warm["regime"], "TREND")

    def test_confirmed_uptrend_buys_with_conviction(self):
        result = settle(observation(trend=0.9, smart_money=0.8, micro=0.5, momentum=0.4))
        self.assertEqual(result["regime"], "TREND")
        self.assertEqual(result["last_action"], "BUY")
        self.assertGreaterEqual(result["conviction"], regime.CONVICTION_ENTRY)
        self.assertGreater(result["size_multiplier"], 0)

    def test_size_multiplier_grows_with_conviction(self):
        weak = settle(observation(trend=0.7, smart_money=0.5, micro=0.3))
        strong = settle(observation(trend=1.0, smart_money=1.0, micro=1.0, momentum=1.0))
        self.assertEqual(weak["last_action"], "BUY")
        self.assertLess(weak["size_multiplier"], strong["size_multiplier"])

    def test_downtrend_never_shorts_and_reduces_only_when_holding(self):
        flat = settle(observation(trend=-0.9, smart_money=-0.8, micro=-0.6))
        self.assertEqual(flat["regime"], "TREND")
        self.assertEqual(flat["last_action"], "HOLD")
        holding = settle(observation(trend=-0.9, smart_money=-0.8, micro=-0.6, exposure=250.0))
        self.assertEqual(holding["last_action"], "REDUCE")

    def test_shock_is_immediate_and_skips_hysteresis(self):
        item = observation(move_atr=4.0, volume_z=5.0, exposure=100.0)
        result = regime.classify_symbol(item, {"regime": "RANGE", "candidate_regime": "RANGE"}, NOW)
        self.assertEqual(result["regime"], "SHOCK")
        self.assertGreaterEqual(result["scores"]["shock_level"], regime.SHOCK_FLOOR)
        self.assertEqual(result["last_action"], "REDUCE")
        self.assertIn("VOLATILITY_SHOCK", result["shock_reasons"])

    def test_range_is_an_explicit_no_trade_state(self):
        result = settle(observation(trend=0.0, smart_money=0.9, micro=0.9, momentum=0.9))
        self.assertEqual(result["regime"], "RANGE")
        self.assertEqual(result["last_action"], "HOLD")
        self.assertIn("RANGE", result["rationale_tr"])

    def test_stale_sensors_lose_quorum_and_force_hold(self):
        stale = {key: 10_000 for key in perception.WEIGHTS}
        result = settle(observation(trend=0.9, smart_money=0.9, micro=0.9, ages=stale))
        self.assertFalse(result["quorum_ok"])
        self.assertEqual(result["last_action"], "HOLD")
        self.assertIn("quorum", result["rationale_tr"].lower())

    def test_stale_sensor_contributes_zero_weight(self):
        fresh = perception.freshness(sensor("trend", 1.0, 0), NOW)
        expired = perception.freshness(sensor("trend", 1.0, perception.TTL["trend"] + 1), NOW)
        self.assertAlmostEqual(fresh, 1.0, places=2)
        self.assertEqual(expired, 0.0)

    def test_wide_spread_blocks_entry(self):
        result = settle(observation(trend=0.9, smart_money=0.8, micro=0.5, spread_multiple=3.5))
        self.assertEqual(result["last_action"], "HOLD")
        self.assertIn("Spread", result["rationale_tr"])

    def test_stale_smart_money_blocks_new_positions(self):
        result = settle(observation(trend=0.9, smart_money=0.9, micro=0.6,
                                    ages={"smart_money": perception.TTL["smart_money"] + 1}))
        self.assertTrue(result["smart_money_veto"])
        self.assertEqual(result["last_action"], "HOLD")

    def test_momentum_is_inverted_in_range(self):
        item = observation(trend=0.0, momentum=0.8)
        ranged = settle(item)
        contribution = next(c for c in ranged["contributions"] if c["id"] == "momentum")
        self.assertLess(contribution["delta"], 0)  # stretched RSI fades in a range


class ProposalTests(unittest.TestCase):
    def test_proposal_carries_contributions_and_sized_request(self):
        item = observation(symbol="ETH-USDT", trend=0.9, smart_money=0.8, micro=0.5)
        state = state_of(item, nav=1000.0)
        symbols, _ = regime.classify_state(state, NOW)
        symbols, proposal = regime.classify_state({**state, "symbols": symbols}, NOW)
        self.assertEqual(proposal["symbol"], "ETH-USDT")
        self.assertEqual(proposal["action"], "BUY")
        self.assertGreater(proposal["requested_notional_usdt"], 0)
        self.assertEqual({c["id"] for c in proposal["contributions"]}, set(perception.WEIGHTS))
        self.assertGreaterEqual(proposal["confidence"], 0.65)

    def test_reduce_outranks_buy_when_both_are_available(self):
        buying = observation(symbol="ETH-USDT", trend=0.9, smart_money=0.8, micro=0.5)
        shocking = observation(symbol="BTC-USDT", move_atr=4.0, volume_z=5.0, exposure=300.0)
        state = state_of(buying, shocking)
        symbols, _ = regime.classify_state(state, NOW)
        _, proposal = regime.classify_state({**state, "symbols": symbols}, NOW)
        self.assertEqual(proposal["symbol"], "BTC-USDT")
        self.assertEqual(proposal["action"], "REDUCE")


if __name__ == "__main__":
    unittest.main()
