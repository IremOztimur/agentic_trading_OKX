import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DashboardTests(unittest.TestCase):
    def test_static_dashboard_uses_json_not_backend_api(self):
        html = (ROOT / "static/index.html").read_text()
        script = (ROOT / "static/app.js").read_text()
        self.assertIn("RANGE / TREND / SHOCK", html)
        self.assertIn("data/dashboard.json", script)
        self.assertNotIn("/api/", script)
        self.assertNotIn("EventSource", script)

    def test_banned_visual_patterns_are_absent(self):
        source = ((ROOT / "static/index.html").read_text() + (ROOT / "static/style.css").read_text()).lower()
        for pattern in ("linear-gradient", "radial-gradient", "backdrop-filter", "font-family:inter", "glassmorphism"):
            self.assertNotIn(pattern, source)

    def test_execution_mode_is_prominent_and_explains_order_flow(self):
        html = (ROOT / "static/index.html").read_text()
        script = (ROOT / "static/app.js").read_text()
        self.assertIn('id="modeHero"', html)
        self.assertIn('id="orderFlow"', html)
        self.assertIn("Hiçbir emir OKX’e gönderilmez.", script)
        self.assertIn("Gerçek emir akışı açık.", script)
        self.assertNotIn("Karar, risk ve execution aynı yerde.", html)

    def test_dashboard_projection_has_valid_contract(self):
        state = json.loads((ROOT / "static/data/dashboard.json").read_text())
        self.assertEqual(state["schema_version"], 1)
        self.assertIn("symbols", state)
        self.assertIn("cycle", state)


if __name__ == "__main__":
    unittest.main()
