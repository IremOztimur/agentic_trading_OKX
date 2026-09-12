import copy
import json
import unittest
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

    def test_forbidden_tool_and_hook_argument_mismatch(self):
        state = fixture()
        state["cycle"]["proposal"]["mcp_call"]["tool"] = "mcp__claude_ai_okx-agent-trade-kit__swap_place_order"
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["FORBIDDEN_TOOL"])

        state = fixture()
        approved = risk_gate.evaluate(state)["approved_call"]
        state["cycle"]["gate"] = risk_gate.evaluate(state)
        mismatch = risk_gate.hook({"tool_name": approved["tool"], "tool_input": {**approved["arguments"], "quoteSz": "1001"}}, state)
        self.assertEqual(mismatch["hookSpecificOutput"]["permissionDecision"], "deny")

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
        proposal.update(action="FLATTEN", requested_notional_usdt=0)
        proposal["mcp_call"]["arguments"].update(side="buy", sz="0.01")
        self.assertEqual(risk_gate.evaluate(state)["reason_codes"], ["NON_REDUCING_SIDE"])
        proposal["mcp_call"]["arguments"]["side"] = "sell"
        self.assertEqual(risk_gate.evaluate(state)["verdict"], "ALLOW")


if __name__ == "__main__":
    unittest.main()
