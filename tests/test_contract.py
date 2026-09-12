import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import journal


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.paths = patch.multiple(
            journal,
            RUN_DIR=base / "run",
            STATE_PATH=base / "run/state.json",
            EVENTS_PATH=base / "run/events.jsonl",
            LOCK_PATH=base / "run/.lock",
            DASHBOARD_PATH=base / "static/data/dashboard.json",
        )
        self.paths.start()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_atomic_state_and_dashboard_projection(self):
        state = journal.save_state(journal.default_state())
        self.assertEqual(state, json.loads(journal.STATE_PATH.read_text()))
        self.assertEqual(state, json.loads(journal.DASHBOARD_PATH.read_text()))

    def test_recursive_redaction(self):
        state = journal.default_state()
        state["recent_events"] = [{"data": {"authorization": "Bearer secret", "nested": {"api_key": "abc"}}}]
        saved = journal.save_state(state)
        self.assertEqual(saved["recent_events"][0]["data"]["authorization"], "<redacted>")
        self.assertEqual(saved["recent_events"][0]["data"]["nested"]["api_key"], "<redacted>")

    def test_invalid_state_is_rejected(self):
        state = copy.deepcopy(journal.default_state())
        del state["cycle"]
        with self.assertRaises(ValueError):
            journal.save_state(state)

    def test_non_finite_numbers_are_rejected(self):
        state = journal.default_state()
        state["account"]["account_age_seconds"] = float("inf")
        with self.assertRaises(ValueError):
            journal.save_state(state)

    def test_legacy_non_finite_number_is_migrated_on_load(self):
        journal.STATE_PATH.parent.mkdir(parents=True)
        journal.STATE_PATH.write_text('{"account_age_seconds": Infinity}', encoding="utf-8")
        self.assertIsNone(journal.load_state()["account_age_seconds"])


if __name__ == "__main__":
    unittest.main()
