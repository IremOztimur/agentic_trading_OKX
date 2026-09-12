import unittest
from pathlib import Path
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import mcp_client


class McpClientTests(unittest.TestCase):
    def test_read_429_retries_but_write_does_not(self):
        client = mcp_client.McpClient()
        client.tools = {"market_get_ticker": {}, "spot_place_order": {}}
        ok = {"content": [{"type": "text", "text": '{"data": []}'}]}
        with patch.object(client, "_pace"), patch("mcp_client.time.sleep"), patch.object(client, "request", side_effect=[mcp_client.McpError("429"), ok]) as request:
            self.assertEqual(client.call("market_get_ticker", {}), {"data": []})
            self.assertEqual(request.call_count, 2)
        with patch.object(client, "_pace"), patch.object(client, "request", side_effect=mcp_client.McpError("429")) as request:
            with self.assertRaises(mcp_client.McpWriteUncertain):
                client.call("spot_place_order", {}, write=True)
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
