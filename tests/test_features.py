import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import features


class FeatureTests(unittest.TestCase):
    def test_builds_deterministic_market_features(self):
        candles = []
        for index in range(60):
            close = 100 + index * 0.1
            candles.append([str(index), str(close - 0.05), str(close + 0.2), str(close - 0.2), str(close), str(10 + index % 3)])
        book = {"data": [{"bids": [["105.9", "4"]], "asks": [["106.0", "2"]]}]}
        price, result = features.build_features({"data": list(reversed(candles))}, book)
        self.assertAlmostEqual(price, 105.9)
        self.assertGreater(result["ema20"], result["ema50"])
        self.assertGreater(result["orderflow"], 0)
        self.assertIn("atr_pct", result)


if __name__ == "__main__":
    unittest.main()
