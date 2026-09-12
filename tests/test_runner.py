import json
import pathlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import atk
import execute
import perception
import runner as runner_module

NOW = datetime(2026, 9, 12, 12, 0, 0, tzinfo=timezone.utc)


class FakeATK:
    """Records calls and replays canned ATK envelopes."""

    def __init__(self, responses=None, fail=()):
        self.responses = responses or {}
        self.fail = set(fail)
        self.calls = []
        self.read_only = True
        self.tools = []

    def call(self, tool, **arguments):
        self.calls.append((tool, arguments))
        if tool in self.fail:
            raise atk.ATKError(f"boom: {tool}")
        return self.responses.get(tool, [])

    def alive(self):
        return True

    def close(self):
        pass


class PayloadTests(unittest.TestCase):
    def test_unwraps_the_atk_envelope(self):
        text = json.dumps({"tool": "market_get_ticker", "ok": True,
                           "data": {"endpoint": "GET /x", "data": [{"last": "77385.1"}]},
                           "capabilities": {}})
        self.assertEqual(atk.ATKClient._payload("market_get_ticker", text), [{"last": "77385.1"}])

    def test_raises_on_a_failed_tool(self):
        text = json.dumps({"tool": "spot_place_order", "ok": False, "error": {"msg": "denied"}})
        with self.assertRaises(atk.ATKError):
            atk.ATKClient._payload("spot_place_order", text)

    def test_raises_on_a_non_zero_okx_code(self):
        with self.assertRaises(atk.ATKError):
            atk.ATKClient._payload("market_get_ticker", json.dumps({"code": "51000", "msg": "bad", "data": []}))

    def test_credentials_are_read_from_env_file(self):
        values = atk.load_env()
        self.assertIsInstance(values, dict)


class IndicatorParsingTests(unittest.TestCase):
    payload = [{"data": [{"instId": "BTC-USDT", "timeframes": {"15m": {"indicators": {
        "SUPERTREND": [{"ts": "1", "values": {"trend": "UP", "superTrend": "77249.7"}}]}}}}]}]

    def test_reads_nested_indicator_values(self):
        values = perception.indicator_values(self.payload, "supertrend", "15m")
        self.assertEqual(values["trend"], "UP")

    def test_missing_timeframe_yields_empty_values(self):
        self.assertEqual(perception.indicator_values(self.payload, "supertrend", "1H"), {})


class SensorTests(unittest.TestCase):
    def test_trend_needs_direction_and_strength(self):
        flat = [{"data": [{"timeframes": {"15m": {"indicators": {"SUPERTREND": [{"values": {"trend": "UP"}}]}}}}]}]
        candles = [[str(i * 60000), "100", "100.1", "99.9", "100", "5"] for i in range(60)]
        client = FakeATK({"market_get_indicator": flat, "market_get_candles": candles})
        sensor = perception.sense_trend(client, "BTC-USDT", 100.0)
        self.assertEqual(sensor["id"], "trend")
        # A flat tape has no ADX, so an "UP" supertrend still scores near zero.
        self.assertLess(abs(sensor["score"]), 0.2)

    def test_smart_money_maps_ratio_onto_a_signed_score(self):
        rows = [{"ccy": "BTC", "longShortRatio": {"weightedLongRatio": "0.75", "longRatioVs24h": "0"},
                 "notional": {"netNotionalUsdt": "383429"}, "tradersWithPosition": 17}]
        sensor = perception.sense_smart_money(FakeATK({"smartmoney_get_signal_overview_by_filter": rows}), "BTC-USDT")
        self.assertGreater(sensor["score"], 0.5)
        self.assertEqual(sensor["evidence"]["weightedLongRatio"], 0.75)

    def test_balanced_smart_money_is_neutral(self):
        rows = [{"ccy": "BTC", "longShortRatio": {"weightedLongRatio": "0.5"}, "notional": {}}]
        sensor = perception.sense_smart_money(FakeATK({"smartmoney_get_signal_overview_by_filter": rows}), "BTC-USDT")
        self.assertEqual(sensor["score"], 0.0)

    def test_quorum_falls_as_sensors_go_stale(self):
        sensors = {key: {"observed_at": (NOW - timedelta(seconds=age)).isoformat(), "ttl_s": perception.TTL[key]}
                   for key, age in (("trend", 0), ("momentum", 0), ("microstructure", 0),
                                    ("volatility", 0), ("smart_money", 10_000))}
        live, nominal = perception.quorum(sensors, NOW)
        self.assertAlmostEqual(nominal, 1.0, places=6)
        self.assertLess(live, nominal)
        self.assertGreater(live, perception.MIN_QUORUM * nominal)  # one stale sensor is survivable


class ExecutionTests(unittest.TestCase):
    def proposal(self, action="BUY", run_id="20260912120000-abc123"):
        return {"run_id": run_id, "symbol": "BTC-USDT", "action": action, "price": 77000.0}

    def test_client_order_id_is_alphanumeric_and_bounded(self):
        clord = execute.client_order_id("20260912120000-abc123")
        self.assertTrue(clord.isalnum())
        self.assertLessEqual(len(clord), 32)

    def test_buy_sizes_in_quote_currency(self):
        call = execute.build_call(self.proposal("BUY"))
        self.assertEqual(call["tool"], "spot_place_order")
        self.assertEqual(call["arguments"]["side"], "buy")
        self.assertEqual(call["arguments"]["tgtCcy"], "quote_ccy")
        self.assertEqual(call["arguments"]["tdMode"], "cash")

    def test_reduce_sells_base_currency(self):
        call = execute.build_call(self.proposal("REDUCE"))
        self.assertEqual(call["arguments"]["side"], "sell")
        self.assertEqual(call["arguments"]["tgtCcy"], "base_ccy")

    def test_hold_builds_no_call(self):
        self.assertIsNone(execute.build_call(self.proposal("HOLD")))

    def test_notional_cap_is_the_tighter_of_the_two_limits(self):
        self.assertAlmostEqual(execute.notional_cap(30.0), 1.5)      # 5% of NAV
        self.assertAlmostEqual(execute.notional_cap(10_000.0), 10.0)  # absolute ceiling

    def test_an_existing_client_order_id_is_never_resent(self):
        client = FakeATK({"spot_get_order": [{"ordId": "42", "state": "filled"}]})
        found = execute.already_sent(client, "BTC-USDT", "rd1")
        self.assertEqual(found["ordId"], "42")

    def test_lookup_failure_reports_nothing_rather_than_guessing(self):
        self.assertIsNone(execute.already_sent(FakeATK(fail={"spot_get_order"}), "BTC-USDT", "rd1"))


class ShockLatchTests(unittest.TestCase):
    def setUp(self):
        self.runner = runner_module.Runner(client=FakeATK())

    def test_shock_fires_once_per_episode(self):
        proposal = {"symbol": "BTC-USDT"}
        self.assertTrue(self.runner.shock_is_new(proposal))
        self.assertFalse(self.runner.shock_is_new(proposal))

    def test_a_calm_symbol_releases_its_latch(self):
        self.runner.shock_is_new({"symbol": "BTC-USDT"})
        self.runner.release_latches([{"symbol": "BTC-USDT", "regime": "RANGE"}])
        self.assertTrue(self.runner.shock_is_new({"symbol": "BTC-USDT"}))

    def test_latches_are_tracked_per_symbol(self):
        self.assertTrue(self.runner.shock_is_new({"symbol": "BTC-USDT"}))
        self.assertTrue(self.runner.shock_is_new({"symbol": "ETH-USDT"}))


class LiveCallOrderingTests(unittest.TestCase):
    """The gate can only approve a call it can see, and only the current one."""

    def test_a_hold_proposal_clears_any_earlier_call(self):
        self.assertIsNone(execute.build_call({"run_id": "r1", "symbol": "BTC-USDT", "action": "HOLD"}))

    def test_the_call_always_names_the_proposal_symbol(self):
        call = execute.build_call({"run_id": "r1", "symbol": "ETH-USDT", "action": "BUY"})
        self.assertEqual(call["arguments"]["instId"], "ETH-USDT")

    def test_the_gate_accepts_the_spot_entry_tool(self):
        import risk_gate
        self.assertTrue(risk_gate.ENTRY_WRITE.search("spot_place_order"))
        self.assertFalse(risk_gate.FORBIDDEN_WRITE.search("spot_place_order"))


class ConfirmedToolTests(unittest.TestCase):
    """Anything that moves money must pause for a human before its body runs."""

    def setUp(self):
        import desk_tools
        self.tools = desk_tools
        # Tests must not write WARN/ERROR rows into the live audit journal.
        self.original_event = desk_tools.append_event
        desk_tools.append_event = lambda *args, **kwargs: None

    def tearDown(self):
        self.tools.append_event = self.original_event

    def test_money_moving_tools_require_confirmation(self):
        for func in self.tools.CONFIRMED_TOOLS:
            with self.subTest(tool=func.__name__):
                config = getattr(func, "_upsonic_tool_config", None)
                self.assertIsNotNone(config, f"{func.__name__} is not a registered tool")
                self.assertTrue(config.requires_confirmation,
                                f"{func.__name__} would run without asking the operator")

    def test_read_tools_are_not_gated(self):
        for func in self.tools.READ_TOOLS:
            config = getattr(func, "_upsonic_tool_config", None)
            self.assertIsNone(config)

    def test_the_agent_gets_exactly_these_tools(self):
        self.assertEqual(len(self.tools.ALL_TOOLS), 5)
        self.assertEqual(set(self.tools.CONFIRMED_TOOLS),
                         {self.tools.flatten_positions, self.tools.arm_live})

    def test_writes_go_through_control_with_its_safety_word(self):
        """Neither tool touches the exchange itself; both defer to control.py."""
        seen = []
        original = self.tools.run_control
        self.tools.run_control = lambda *args: (seen.append(args), (True, "ok"))[1]
        try:
            self.tools.flatten_positions()
            self.tools.arm_live()
        finally:
            self.tools.run_control = original
        self.assertIn(("flatten", "FLATTEN"), seen)
        self.assertIn(("live", "CANLI"), seen)

    def test_a_failed_control_call_is_reported_not_swallowed(self):
        original = self.tools.run_control
        self.tools.run_control = lambda *args: (False, "LIVE reddedildi: preflight hazır değil")
        try:
            result = self.tools.arm_live()
        finally:
            self.tools.run_control = original
        from journal import load_state
        self.assertFalse(result["executed"])
        self.assertIn("preflight", result["detail"])
        # A refused arm must leave the mode exactly as it found it.
        self.assertEqual(result["mode_after"], load_state()["session"]["mode"])


class SymbolToolTests(unittest.TestCase):
    def test_symbols_are_normalized_to_the_watchlist(self):
        import desk_tools
        self.assertEqual(desk_tools.normalize_symbol("eth"), "ETH-USDT")
        self.assertEqual(desk_tools.normalize_symbol("BTC-USDT"), "BTC-USDT")
        self.assertIsNone(desk_tools.normalize_symbol("DOGE"))
        self.assertIsNone(desk_tools.normalize_symbol(""))

    def test_an_unwatched_symbol_returns_an_error_not_a_guess(self):
        import desk_tools
        result = desk_tools.get_symbol_decision("DOGE")
        self.assertIn("error", result)
        self.assertIn("watched_symbols", result)


if __name__ == "__main__":
    unittest.main()
