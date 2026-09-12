import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import runner


class TestRunner(runner.Runner):
    def __init__(self):
        super().__init__(market=object(), agent_command="false")
        self.agent_calls = []

    def trigger_agent(self, event, run_id=None):
        self.agent_calls.append((event, run_id))
        return True


class RunnerTests(unittest.TestCase):
    def test_missing_timestamp_has_no_json_infinity(self):
        self.assertIsNone(TestRunner.age(None, __import__("datetime").datetime.now(__import__("datetime").timezone.utc)))

    def run_cycle(self, proposal):
        subject = TestRunner()
        with patch.object(subject, "refresh_features"), patch("runner.load_state", return_value={}), patch("runner.classify_state", return_value=([], proposal)), patch("runner.merge_state"), patch("runner.append_event"):
            subject.decision_cycle()
        return subject.agent_calls

    def test_hold_does_not_call_agent(self):
        proposal = {"run_id": "hold-1", "action": "HOLD", "candidate_action": "HOLD", "regime": "RANGE", "rationale_tr": "Bekle."}
        self.assertEqual(self.run_cycle(proposal), [])

    def test_actionable_candidate_calls_agent(self):
        proposal = {"run_id": "trend-1", "action": "HOLD", "candidate_action": "BUY_BREAKOUT", "regime": "TREND", "rationale_tr": "Aday."}
        self.assertEqual(self.run_cycle(proposal), [("candidate", "trend-1")])

    def test_shock_calls_emergency_not_candidate(self):
        proposal = {"run_id": "shock-1", "action": "REDUCE", "candidate_action": "REDUCE", "regime": "SHOCK", "rationale_tr": "Şok."}
        self.assertEqual(self.run_cycle(proposal), [("emergency", "shock-1")])


if __name__ == "__main__":
    unittest.main()
