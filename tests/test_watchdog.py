import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import watchdog


def fixture():
    return json.loads((ROOT / "tests/fixtures/allow_state.json").read_text())


class WatchdogTests(unittest.TestCase):
    def test_stale_heartbeat_only_cancels_and_stops(self):
        state = fixture()
        state["session"]["heartbeat_at"] = "2025-01-01T00:00:00+00:00"
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        result = watchdog.evaluate(state, now)
        self.assertEqual(result["mode"], "PAUSED")
        self.assertEqual([item["action"] for item in result["actions"]], ["CANCEL_ENTRY_ORDERS", "STOP_GRIDS"])

    def test_hard_drawdown_flattens_only_agent_inventory(self):
        state = fixture()
        state["account"]["drawdown_pct"] = -0.05
        state["session"]["heartbeat_at"] = "2026-01-01T11:59:59+00:00"
        now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        result = watchdog.evaluate(state, now)
        flatten = next(item for item in result["actions"] if item["action"] == "FLATTEN_AGENT_INVENTORY")
        self.assertAlmostEqual(flatten["base_amount"], 0.01)
        self.assertEqual(flatten["side"], "sell")
        self.assertFalse(any(item["action"] in {"BUY", "CREATE_GRID"} for item in result["actions"]))

    def test_dry_run_never_emits_emergency_writes(self):
        state = fixture()
        state["session"]["mode"] = "DRY_RUN"
        state["account"]["drawdown_pct"] = -0.10
        result = watchdog.evaluate(state, datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(result["actions"], [])


if __name__ == "__main__":
    unittest.main()

