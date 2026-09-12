import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import okx_public


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class PublicMarketTests(unittest.TestCase):
    def test_success_returns_data(self):
        client = okx_public.PublicMarketClient("https://example.test")
        with patch.object(client, "_pace"), patch("okx_public.urllib.request.urlopen", return_value=Response({"code": "0", "data": [{"last": "1"}]})):
            self.assertEqual(client.ticker("BTC-USDT"), [{"last": "1"}])

    def test_429_retries_with_backoff(self):
        client = okx_public.PublicMarketClient("https://example.test")
        error = okx_public.urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "0"}, io.BytesIO())
        self.addCleanup(error.close)
        with patch.object(client, "_pace"), patch("okx_public.time.sleep"), patch("okx_public.urllib.request.urlopen", side_effect=[error, Response({"code": "0", "data": []})]) as request:
            self.assertEqual(client.ticker("BTC-USDT"), [])
            self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
