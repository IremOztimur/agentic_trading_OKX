import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import risk_gate


def fixture():
    return json.loads((ROOT / "tests/fixtures/allow_state.json").read_text())


class RiskGateTests(unittest.TestCase):
    def test_allows_and_caps_at_coin_limit(self):
        state = fixture()
        state["cycle"]["proposal"]["requested_notional_usdt"] = 5000
        result = risk_gate.evaluate(state)
        self.assertEqual(result["verdict"], "ALLOW")
        self.assertEqual(result["allowed_notional_usdt"], 2500)
        self.assertEqual(result["approved_call"]["arguments"]["quoteSz"], "2500.0")

    def test_smart_money_veto(self):
        state = fixture()
        state["cycle"]["proposal"]["smart_money_veto"] = True
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["SMART_MONEY_VETO"])

    def test_soft_and_hard_drawdown(self):
        soft = fixture()
        soft["account"]["drawdown_pct"] = -0.03
        self.assertEqual(risk_gate.evaluate(soft)["verdict"], "HOLD")
        hard = fixture()
        hard["account"]["drawdown_pct"] = -0.05
        self.assertEqual(risk_gate.evaluate(hard)["verdict"], "HALT")

    def test_stale_and_duplicate_are_rejected(self):
        stale = fixture()
        stale["cycle"]["proposal"]["market_age_seconds"] = 21
        self.assertEqual(risk_gate.evaluate(stale)["reason_codes"], ["STALE_MARKET"])
        duplicate = fixture()
        duplicate["cycle"]["execution"].update(run_id="run-1", status="FILLED")
        self.assertEqual(risk_gate.evaluate(duplicate)["reason_codes"], ["DUPLICATE_RUN"])

    def test_preflight_and_safe_close_block_new_risk(self):
        state = fixture()
        state["session"]["health"]["trade_ready"] = False
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["PREFLIGHT_INCOMPLETE"])
        state["session"]["health"]["trade_ready"] = True
        after_close = datetime(2098, 12, 31, 17, 0, tzinfo=timezone.utc)
        self.assertEqual(risk_gate.evaluate(state, after_close)["reason_codes"], ["SAFE_CLOSE"])

    def test_instrument_minimum_and_state_are_enforced(self):
        state = fixture()
        state["symbols"] = [{"symbol": "ETH-USDT", "features": {"minSz": "0.1", "lotSz": "0.001", "instrument_state": "live"}}]
        state["cycle"]["proposal"].update(price=2500, requested_notional_usdt=100)
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["BELOW_MIN_SIZE"])
        state["symbols"][0]["features"]["instrument_state"] = "suspend"
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["INSTRUMENT_NOT_LIVE"])

    def test_dry_run_can_size_semantic_candidate_without_write_tool(self):
        state = fixture()
        state["session"]["mode"] = "DRY_RUN"
        state["cycle"]["proposal"].pop("mcp_call")
        result = risk_gate.evaluate(state)
        self.assertEqual(result["verdict"], "ALLOW")
        self.assertEqual(result["reason_codes"], ["DRY_RUN_SEMANTIC_ALLOW"])
        self.assertIsNone(result["approved_call"])

    def test_forbidden_tool_and_hook_argument_mismatch(self):
        state = fixture()
        state["cycle"]["proposal"]["mcp_call"]["tool"] = "mcp__claude_ai_okx-agent-trade-kit__swap_place_order"
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["FORBIDDEN_TOOL"])

        state = fixture()
        approved = risk_gate.evaluate(state)["approved_call"]
        state["cycle"]["gate"] = risk_gate.evaluate(state)
        mismatch = risk_gate.hook({"tool_name": approved["tool"], "tool_input": {**approved["arguments"], "quoteSz": "1001"}}, state)
        self.assertEqual(mismatch["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_asset_balance_is_not_misclassified_as_write(self):
        state = fixture()
        payload = {"tool_name": "mcp__claude_ai_okx-agent-trade-kit__account_get_asset_balance", "tool_input": {"ccy": "USDT"}}
        self.assertIsNone(risk_gate.hook(payload, state))

    def test_hook_allows_exact_call_only_in_live(self):
        state = fixture()
        approved = risk_gate.evaluate(state)["approved_call"]
        result = risk_gate.hook({"tool_name": approved["tool"], "tool_input": approved["arguments"]}, state)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "allow")
        state["session"]["mode"] = "DRY_RUN"
        result = risk_gate.hook({"tool_name": approved["tool"], "tool_input": approved["arguments"]}, state)
        self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_flatten_can_only_sell(self):
        state = fixture()
        proposal = state["cycle"]["proposal"]
        proposal.update(action="FLATTEN", symbol="BTC-USDT", price=100000, requested_notional_usdt=1000)
        proposal["mcp_call"]["arguments"].update(side="buy", sz="0.01")
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["NON_REDUCING_SIDE"])
        proposal["mcp_call"]["arguments"]["side"] = "sell"
        self.assertEqual(risk_gate.evaluate(state)["verdict"], "ALLOW")

    def test_flatten_cannot_sell_starting_inventory(self):
        state = fixture()
        state["account"]["positions"][0]["base_amount"] = 0.01
        proposal = state["cycle"]["proposal"]
        proposal.update(action="FLATTEN", symbol="BTC-USDT", price=100000, requested_notional_usdt=1000)
        proposal["mcp_call"] = {
            "tool": "mcp__claude_ai_okx-agent-trade-kit__spot_place_order",
            "arguments": {"instId": "BTC-USDT", "side": "sell", "ordType": "market", "sz": "0.01"},
        }
        result = risk_gate.evaluate(state)
        self.assertEqual(result["verdict"], "HOLD")
        self.assertEqual(result["reason_codes"], ["NO_AGENT_OWNED_EXPOSURE"])


if __name__ == "__main__":
    unittest.main()
