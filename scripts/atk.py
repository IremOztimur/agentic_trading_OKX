#!/usr/bin/env python3
"""OKX Agent Trade Kit MCP client.

Speaks MCP JSON-RPC directly over the stdio pipes of a local `okx-trade-mcp`
process. No LLM sits between the desk and the exchange: a tool call is one
pipe round-trip, so market perception stays inside the decision cycle.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
BINARY = "okx-trade-mcp"
MODULES = "market,smartmoney,account,spot"
CALL_TIMEOUT = 15.0
RESTART_COOLDOWN = 60.0
PROTOCOL_VERSION = "2025-06-18"


class ATKError(RuntimeError):
    pass


def load_env() -> dict[str, str]:
    """Read OKX credentials from .env without importing a dependency."""
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.isidentifier():
            values[key] = value.strip().strip('"').strip("'")
    return values


class ATKClient:
    """Persistent stdio MCP client for the OKX Agent Trade Kit."""

    def __init__(self, read_only: bool = True, modules: str = MODULES) -> None:
        self.read_only = read_only
        self.modules = modules
        self.process: subprocess.Popen | None = None
        self.next_id = 0
        self.last_start = 0.0
        self.lock = threading.Lock()
        self.tools: list[str] = []

    # -- process lifecycle ------------------------------------------------
    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        for key, value in load_env().items():
            if key.startswith("OKX_"):
                env[key] = value
        return env

    def command(self) -> list[str]:
        binary = shutil.which(BINARY)
        if not binary:
            raise ATKError(f"{BINARY} bulunamadı; `npm install -g @okx_ai/okx-trade-mcp`")
        args = [binary, "--modules", self.modules, "--log-level", "error"]
        args += ["--read-only"] if self.read_only else ["--live"]
        return args

    def alive(self) -> bool:
        return bool(self.process) and self.process.poll() is None

    def start(self) -> None:
        if self.alive():
            return
        if time.monotonic() - self.last_start < RESTART_COOLDOWN and self.last_start:
            raise ATKError("ATK MCP yeniden başlatma cooldown'da")
        self.last_start = time.monotonic()
        self.process = subprocess.Popen(
            self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=self.env(),
        )
        self.next_id = 0
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "regime-desk", "version": "2.0"},
        })
        self._notify("notifications/initialized")
        listing = self._request("tools/list", {})
        self.tools = [tool.get("name") for tool in listing.get("tools", [])]

    def close(self) -> None:
        if not self.alive():
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()

    # -- json-rpc ---------------------------------------------------------
    def _send(self, message: dict[str, Any]) -> None:
        if not self.alive():
            raise ATKError("ATK MCP süreci çalışmıyor")
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        request_id = self.next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + CALL_TIMEOUT
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line:
                raise ATKError(f"ATK MCP stdout kapandı: {method}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue  # the server may log non-JSON lines
            if message.get("id") != request_id:
                continue  # notification or a response we are not waiting on
            if "error" in message:
                raise ATKError(f"ATK MCP {method}: {message['error'].get('message')}")
            return message.get("result") or {}
        raise ATKError(f"ATK MCP timeout: {method}")

    # -- tool calls -------------------------------------------------------
    def call(self, tool: str, **arguments: Any) -> Any:
        """Call an ATK tool and return the parsed OKX `data` payload."""
        with self.lock:
            self.start()
            result = self._request("tools/call", {"name": tool, "arguments": arguments})
        if result.get("isError"):
            raise ATKError(f"ATK {tool}: {self._text(result)[:200]}")
        return self._payload(tool, self._text(result))

    @staticmethod
    def _text(result: dict[str, Any]) -> str:
        blocks = result.get("content") or []
        return "".join(block.get("text", "") for block in blocks if isinstance(block, dict))

    @staticmethod
    def _payload(tool: str, text: str) -> Any:
        if not text:
            return []
        try:
            envelope = json.loads(text)
        except json.JSONDecodeError:
            return text
        if not isinstance(envelope, dict):
            return envelope
        if envelope.get("ok") is False:
            raise ATKError(f"ATK {tool}: {json.dumps(envelope.get('error') or envelope)[:200]}")
        # ATK wraps the OKX response: {tool, ok, data:{endpoint, data:[...]}, capabilities}
        body = envelope.get("data")
        if isinstance(body, dict) and "data" in body:
            return body.get("data")
        if "code" in envelope:
            if str(envelope.get("code")) != "0":
                raise ATKError(f"ATK {tool}: {envelope.get('code')} {envelope.get('msg')}")
            return envelope.get("data")
        return envelope


_SHARED: ATKClient | None = None


def shared(read_only: bool = True) -> ATKClient:
    """One ATK process per runner; LIVE arming replaces the read-only child."""
    global _SHARED
    if _SHARED is None:
        _SHARED = ATKClient(read_only=read_only)
    elif _SHARED.read_only != read_only:
        _SHARED.close()
        _SHARED = ATKClient(read_only=read_only)
    return _SHARED


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("probe",))
    parser.add_argument("--symbol", default="BTC-USDT")
    args = parser.parse_args()
    client = ATKClient(read_only=True)
    try:
        started = time.monotonic()
        client.start()
        print(f"ATK MCP hazır: {len(client.tools)} tool, {round((time.monotonic() - started) * 1000)}ms")
        for tool, kwargs in (
            ("market_get_ticker", {"instId": args.symbol}),
            ("market_get_indicator", {"instId": args.symbol, "indicator": "supertrend", "bar": "15m"}),
            ("account_get_balance", {}),
        ):
            started = time.monotonic()
            try:
                data = client.call(tool, **kwargs)
                latency = round((time.monotonic() - started) * 1000)
                print(f"  {tool:<26} {latency:>5}ms  {json.dumps(data, ensure_ascii=False)[:140]}")
            except ATKError as exc:
                print(f"  {tool:<26} HATA: {exc}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
