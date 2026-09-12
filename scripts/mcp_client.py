#!/usr/bin/env python3
"""Tiny stdio MCP client backed by mcp-remote OAuth."""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from typing import Any

ENDPOINT = "https://www.okx.com/api/v1/mcp/trading-oauth"


class McpError(RuntimeError):
    pass


class McpWriteUncertain(McpError):
    pass


class McpClient:
    def __init__(self, endpoint: str = ENDPOINT) -> None:
        self.endpoint = endpoint
        self.process: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self.pending: dict[int, dict[str, Any]] = {}
        self.next_id = 1
        self.tools: dict[str, dict[str, Any]] = {}
        self.last_call: dict[str, float] = {}

    def __enter__(self) -> "McpClient":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def start(self) -> None:
        if self.process:
            return
        self.process = subprocess.Popen(
            [
                "npx", "-y", "mcp-remote@0.13.5", self.endpoint,
                "--transport", "http-only", "--keep-alive", "--auth-timeout", "180",
                "--ignore-tool", "*swap*", "--ignore-tool", "*future*",
                "--ignore-tool", "*option*", "--ignore-tool", "*withdraw*",
                "--ignore-tool", "*transfer*", "--ignore-tool", "*earn*",
                "--ignore-tool", "*margin*", "--ignore-tool", "*borrow*",
                "--ignore-tool", "*repay*", "--ignore-tool", "*loan*",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._reader, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "regime-desk-runner", "version": "1.0.0"}}, timeout=300)
        self.notify("notifications/initialized", {})
        cursor = None
        while True:
            listed = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=60)
            self.tools.update({item["name"]: item for item in listed.get("tools", [])})
            cursor = listed.get("nextCursor")
            if not cursor:
                break

    def _reader(self) -> None:
        assert self.process and self.process.stdout
        for raw in self.process.stdout:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "id" in message:
                self.responses.put(message)

    def _send(self, message: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise McpError("MCP process çalışmıyor")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any], timeout: float = 45) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        while request_id not in self.pending:
            try:
                message = self.responses.get(timeout=timeout)
            except queue.Empty as exc:
                raise McpError(f"MCP timeout: {method}") from exc
            self.pending[int(message["id"])] = message
        message = self.pending.pop(request_id)
        if "error" in message:
            raise McpError(f"{method}: {message['error']}")
        return message.get("result") or {}

    def _bucket(self, name: str) -> tuple[str, float]:
        lowered = name.lower()
        if "ticker" in lowered:
            return "ticker", 0.20
        if "book" in lowered:
            return "book", 0.10
        if "candle" in lowered:
            return "candle", 0.10
        if "trade" in lowered and "place" not in lowered and "order" not in lowered:
            return "trades", 0.05
        if "algo" in lowered:
            return "algo", 0.20
        if any(word in lowered for word in ("place", "cancel", "amend", "create", "stop")):
            return "write", 0.20
        return "other", 0.10

    def _pace(self, name: str) -> None:
        bucket, minimum = self._bucket(name)
        remaining = minimum - (time.monotonic() - self.last_call.get(bucket, 0.0))
        if remaining > 0:
            time.sleep(remaining)
        self.last_call[bucket] = time.monotonic()

    @staticmethod
    def _retry_after(error: Exception, attempt: int) -> float:
        match = re.search(r"retry[- ]after[^0-9]*([0-9]+(?:\.[0-9]+)?)", str(error), re.I)
        return float(match.group(1)) if match else float(2**attempt)

    def call(self, name: str, arguments: dict[str, Any], timeout: float = 60, *, write: bool = False) -> Any:
        if name not in self.tools:
            raise McpError(f"MCP tool bulunamadı: {name}")
        attempts = 1 if write else 3
        for attempt in range(attempts):
            self._pace(name)
            try:
                result = self.request("tools/call", {"name": name, "arguments": arguments}, timeout)
                break
            except McpError as exc:
                rate_limited = "429" in str(exc) or "rate limit" in str(exc).lower()
                if write:
                    raise McpWriteUncertain(f"Write sonucu belirsiz; lookup gerekli: {exc}") from exc
                if not rate_limited or attempt == attempts - 1:
                    raise
                time.sleep(self._retry_after(exc, attempt))
        if result.get("isError"):
            error = McpError(f"{name}: {result.get('content')}")
            if write:
                raise McpWriteUncertain(f"Write sonucu belirsiz; lookup gerekli: {error}") from error
            raise error
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        texts = [block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"]
        if len(texts) == 1:
            try:
                return json.loads(texts[0])
            except json.JSONDecodeError:
                return texts[0]
        return texts

    def find(self, *terms: str) -> str | None:
        return next((name for name in self.tools if all(term.lower() in name.lower() for term in terms)), None)

    def close(self) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


def tool_arguments(tool: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    properties = (tool.get("inputSchema") or {}).get("properties") or {}
    return {key: value for key, value in values.items() if key in properties}
