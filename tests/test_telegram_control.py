import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from telegram_control import action_callback, is_flatten_request, is_live_request, live_callback, verify_action_callback, verify_live_callback


class TelegramControlTests(unittest.TestCase):
    def test_common_live_requests_take_the_deterministic_path(self):
        for text in ("/live", "Go LIVE", "let's go live", "  CANLI  ", "canlı"):
            with self.subTest(text=text):
                self.assertTrue(is_live_request(text))
        self.assertFalse(is_live_request("why are we live"))

    def test_common_flatten_requests_take_the_deterministic_path(self):
        for text in ("/flatten", "FLATTEN", "flatten all", "close everything", "hepsini kapat"):
            with self.subTest(text=text):
                self.assertTrue(is_flatten_request(text))

    def test_callback_is_signed_and_short_lived(self):
        callback = live_callback("secret", now=1_000)
        self.assertTrue(verify_live_callback(callback, "secret", now=1_200))
        self.assertFalse(verify_live_callback(callback, "wrong", now=1_200))
        self.assertFalse(verify_live_callback(callback, "secret", now=1_301))

    def test_callback_preserves_the_confirmed_action(self):
        callback = action_callback("flatten", "secret", now=1_000)
        self.assertEqual(verify_action_callback(callback, "secret", now=1_100), "flatten")

    def test_tampered_callback_is_rejected(self):
        callback = live_callback("secret", now=1_000)
        self.assertFalse(verify_live_callback(callback[:-1] + "0", "secret", now=1_001))


if __name__ == "__main__":
    unittest.main()
