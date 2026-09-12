import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runner


class FakeClient:
    def __init__(self):
        self.tools = {
            "spot_place_order": {"inputSchema": {"properties": {key: {} for key in ("instId", "tdMode", "side", "ordType", "tgtCcy", "sz", "clOrdId", "simulatedTrading")}}},
            "grid_create_order": {"inputSchema": {"properties": {key: {} for key in ("instId", "algoOrdType", "maxPx", "minPx", "gridNum", "investAmt", "simulatedTrading")}}},
        }

    def find(self, *terms):
        return next((name for name in self.tools if all(term in name for term in terms)), None)


class TestRunner(runner.Runner):
    def __init__(self):
        super().__init__(FakeClient())
        self.ai_calls = 0

    def ask_ai(self, proposal):
        self.ai_calls += 1
        return {"candidate_id": proposal["run_id"], "approve": True, "context_risk": "LOW", "reason_codes": [], "rationale_tr": "Onay."}


class RunnerTests(unittest.TestCase):
    def test_hold_does_not_call_agent(self):
        subject = TestRunner()
        proposal = {"run_id": "hold-1", "action": "HOLD", "requested_notional_usdt": 0, "rationale_tr": "Bekle."}
        gate = {"verdict": "HOLD", "reason_codes": ["NO_ACTION"], "allowed_notional_usdt": 0}
        with patch("runner.load_state", return_value={"session": {"mode": "DRY_RUN"}}), patch("runner.classify_state", return_value=([], proposal)), patch("runner.merge_state"), patch("runner.append_event"), patch("runner.evaluate_risk", return_value=gate):
            subject.decide()
        self.assertEqual(subject.ai_calls, 0)

    def test_actionable_candidate_calls_agent_without_tool_authority(self):
        subject = TestRunner()
        proposal = {"run_id": "trend-1", "symbol": "ETH-USDT", "price": 2500, "regime": "TREND", "action": "BUY_BREAKOUT", "requested_notional_usdt": 6, "evidence": {"price_volume": {}}, "rationale_tr": "Aday."}
        gate = {"verdict": "ALLOW", "reason_codes": ["WITHIN_LIMITS"], "allowed_notional_usdt": 6, "approved_call": {"tool": "spot_place_order", "arguments": {}}}
        with patch("runner.load_state", return_value={"session": {"mode": "DRY_RUN"}}), patch("runner.classify_state", return_value=([], proposal)), patch("runner.merge_state"), patch("runner.append_event"), patch("runner.evaluate_risk", return_value=gate):
            subject.decide()
        self.assertEqual(subject.ai_calls, 1)


if __name__ == "__main__":
    unittest.main()
