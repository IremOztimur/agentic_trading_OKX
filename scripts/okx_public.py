#!/usr/bin/env python3
"""Rate-limited, unauthenticated OKX market-data client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

BASE_URL = "https://www.okx.com/api/v5"

# Conservative pacing versus documented per-IP endpoint limits.
MIN_INTERVAL = {
    "/market/ticker": 0.20,
    "/market/books": 0.10,
    "/market/candles": 0.10,
    "/market/trades": 0.05,
    "/public/instruments": 0.20,
    "/public/open-interest": 0.20,
}


class PublicMarketError(RuntimeError):
    pass


class PublicMarketClient:
    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")
        self.last_call: dict[str, float] = {}

    def _pace(self, endpoint: str) -> None:
        minimum = MIN_INTERVAL.get(endpoint, 0.20)
        remaining = minimum - (time.monotonic() - self.last_call.get(endpoint, 0.0))
        if remaining > 0:
            time.sleep(remaining)
        self.last_call[endpoint] = time.monotonic()

    def get(self, endpoint: str, **params: Any) -> list[Any]:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}{endpoint}?{query}" if query else f"{self.base_url}{endpoint}"
        for attempt in range(3):
            self._pace(endpoint)
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "regime-desk/1.0", "Accept": "application/json"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if str(payload.get("code", "0")) != "0":
                    raise PublicMarketError(f"OKX {endpoint}: {payload.get('code')} {payload.get('msg')}")
                return payload.get("data") or []
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 2:
                    raise PublicMarketError(f"OKX HTTP {exc.code}: {endpoint}") from exc
                retry_after = exc.headers.get("Retry-After")
                time.sleep(float(retry_after) if retry_after else float(2**attempt))
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == 2:
                    raise PublicMarketError(f"OKX public read başarısız: {endpoint}: {exc}") from exc
                time.sleep(float(2**attempt))
        raise PublicMarketError(f"OKX public read başarısız: {endpoint}")

    def instruments(self) -> list[Any]:
        return self.get("/public/instruments", instType="SPOT")

    def ticker(self, symbol: str) -> list[Any]:
        return self.get("/market/ticker", instId=symbol)

    def books(self, symbol: str, size: int = 20) -> list[Any]:
        return self.get("/market/books", instId=symbol, sz=size)

    def candles(self, symbol: str, bar: str = "1m", limit: int = 100) -> list[Any]:
        return self.get("/market/candles", instId=symbol, bar=bar, limit=limit)

    def trades(self, symbol: str, limit: int = 100) -> list[Any]:
        return self.get("/market/trades", instId=symbol, limit=limit)

    def open_interest(self, symbol: str) -> list[Any]:
        return self.get("/public/open-interest", instType="SWAP", instId=symbol.replace("-USDT", "-USDT-SWAP"))
