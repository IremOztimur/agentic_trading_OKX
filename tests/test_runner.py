import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch
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
    def test_parses_claude_json_envelope_and_fenced_result(self):
        output = json.dumps({"result": "Preflight tamamlandı.\n```json\n{\"approve\": true}\n```"})
        self.assertEqual(runner.parse_agent_output(output), {"approve": True})

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

    def test_dry_run_shock_does_not_call_agent_without_live_risk(self):
        proposal = {"run_id": "shock-1", "action": "REDUCE", "candidate_action": "REDUCE", "regime": "SHOCK", "rationale_tr": "Şok."}
        self.assertEqual(self.run_cycle(proposal), [])

    def test_private_preflight_requires_verified_nav(self):
        subject = TestRunner()
        with self.assertRaises(ValueError):
            subject.apply_private_account({"account": {"nav": 0, "available_usdt": 0}})

    def test_background_agent_request_does_not_block(self):
        subject = TestRunner()
        subject.background_agents = True
        process = Mock()
        process.poll.return_value = None
        with patch("runner.subprocess.Popen", return_value=process), patch("runner.append_event"):
            self.assertTrue(subject.request_agent("preflight"))
        self.assertIs(subject.agent_process, process)


if __name__ == "__main__":
    unittest.main()
