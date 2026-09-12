#!/usr/bin/env python3
"""Deterministic market scout that triggers Claude only on actionable events."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timedelta, timezone

from features import build_features, microstructure
from journal import append_event, load_state, merge_state, utc_now
from okx_public import PublicMarketClient, PublicMarketError
from regime import classify_state
from risk_gate import evaluate as evaluate_risk
from watchdog import evaluate as evaluate_watchdog

SYMBOLS = ("BTC-USDT", "ETH-USDT", "SOL-USDT")
HEARTBEAT_SECONDS = 10
FAST_MARKET_SECONDS = 15
REGIME_SECONDS = 120
OI_SECONDS = 300
AGENT_TIMEOUT_SECONDS = 180
ENTRY_ACTIONS = {"OPEN_GRID", "BUY_BREAKOUT"}
RUNNER_LOCK = "/tmp/regime-desk-runner.lock"
DEFAULT_AGENT_COMMAND = "claude -p --output-format json --permission-mode dontAsk --allowedTools 'Skill,Read,mcp__claude_ai_okx-agent-trade-kit__*_get_*' --no-session-persistence"


def first(payload: list[object]) -> dict:
    return payload[0] if payload and isinstance(payload[0], dict) else {}


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_agent_output(stdout: str) -> dict:
    envelope = json.loads(stdout)
    raw = envelope.get("structured_output") or envelope.get("result", envelope) if isinstance(envelope, dict) else envelope
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise ValueError("agent sonucu JSON object değil")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.IGNORECASE)
        if not fenced:
            raise
        parsed = json.loads(fenced.group(1))
    if not isinstance(parsed, dict):
        raise ValueError("agent sonucu JSON object değil")
    return parsed


class Runner:
    def __init__(self, market: PublicMarketClient | None = None, agent_command: str | None = None) -> None:
        self.market = market or PublicMarketClient()
        self.agent_command = agent_command or os.environ.get("REGIME_DESK_AGENT_COMMAND", DEFAULT_AGENT_COMMAND)
        self.last_fast = self.last_regime = self.last_oi = 0.0
        self.last_agent_event: str | None = None

    def public_read(self, label: str, call, *args):
        started = time.monotonic()
        try:
            result = call(*args)
        except Exception as exc:
            append_event("MARKET_READ", "ERROR", f"Public OKX: {label} başarısız", {"provider": "OKX_PUBLIC", "error": str(exc)[:300]})
            raise
        append_event("MARKET_READ", "INFO", f"Public OKX: {label}", {"provider": "OKX_PUBLIC", "latency_ms": round((time.monotonic() - started) * 1000)})
        return result

    def refresh_instruments(self) -> None:
        instruments = {item.get("instId"): item for item in self.public_read("spot instruments", self.market.instruments) if isinstance(item, dict)}
        state = load_state()
        current = {item.get("symbol"): item for item in state.get("observation_symbols", [])}
        for symbol in SYMBOLS:
            source = instruments.get(symbol)
            if not source:
                raise PublicMarketError(f"Instrument bulunamadı: {symbol}")
            record = current.setdefault(symbol, {"symbol": symbol, "features": {}, "news": {}, "last_action": "HOLD"})
            record["features"] = {**record.get("features", {}), "minSz": source.get("minSz"), "lotSz": source.get("lotSz"), "tickSz": source.get("tickSz"), "instrument_state": source.get("state")}
        merge_state({"observation_symbols": [current[symbol] for symbol in SYMBOLS]})

    def refresh_fast_market(self) -> None:
        state = load_state()
        current = {item.get("symbol"): item for item in state.get("observation_symbols", [])}
        for symbol in SYMBOLS:
            ticker = first(self.public_read(f"{symbol} ticker", self.market.ticker, symbol))
            book = self.public_read(f"{symbol} book", self.market.books, symbol)
            trades = self.public_read(f"{symbol} trades", self.market.trades, symbol)
            record = current.setdefault(symbol, {"symbol": symbol, "features": {}, "news": {}, "last_action": "HOLD"})
            previous_spread = number(record.get("features", {}).get("spread_baseline_pct"))
            record.update(price=number(ticker.get("last")), market_observed_at=utc_now(), market_age_seconds=0)
            record["features"] = {**record.get("features", {}), **microstructure({"data": book}, trades, previous_spread)}
            record.setdefault("smart_money_status", "UNAVAILABLE")
            if record["smart_money_status"] != "READY":
                record["smart_money_veto"] = True
            record.setdefault("news", {"status": "UNAVAILABLE", "high_impact_negative": False})
        merge_state({"observation_symbols": [current[symbol] for symbol in SYMBOLS], "session": {"health": {"market": "READY"}}})

    def refresh_oi(self) -> None:
        state = load_state()
        current = {item.get("symbol"): item for item in state.get("observation_symbols", [])}
        for symbol in SYMBOLS:
            payload = self.public_read(f"{symbol} OI", self.market.open_interest, symbol)
            current[symbol]["open_interest"] = {"status": "READY", "observed_at": utc_now(), "data": first(payload)}
        merge_state({"observation_symbols": [current[symbol] for symbol in SYMBOLS]})

    def refresh_features(self) -> None:
        state = load_state()
        current = {item.get("symbol"): item for item in state.get("observation_symbols", [])}
        for symbol in SYMBOLS:
            candles = self.public_read(f"{symbol} candles", self.market.candles, symbol)
            book = self.public_read(f"{symbol} regime book", self.market.books, symbol)
            price, features = build_features(candles, {"data": book})
            record = current[symbol]
            record.update(price=price, market_observed_at=utc_now(), market_age_seconds=0)
            record["features"] = {**record.get("features", {}), **features}
        merge_state({"observation_symbols": [current[symbol] for symbol in SYMBOLS], "session": {"health": {"market": "READY"}}})

    def classify(self, *, shock_only: bool = False) -> dict | None:
        symbols, proposal = classify_state(load_state())
        if shock_only and proposal.get("regime") != "SHOCK":
            return None
        reason = "AGENT_CONTEXT_PENDING" if proposal.get("candidate_action") in ENTRY_ACTIONS else "NO_ACTION"
        merge_state({"symbols": symbols, "cycle": {"run_id": proposal["run_id"], "proposal": proposal, "gate": {"run_id": proposal["run_id"], "verdict": "HOLD", "allowed_notional_usdt": 0, "reason_codes": [reason], "checked_at": None, "expires_at": proposal.get("expires_at"), "approved_call": None}, "execution": {"run_id": proposal["run_id"], "status": "NOT_SENT", "tool": None, "client_order_id": None, "order_id": None}}})
        append_event("DECISION", "WARN" if proposal.get("regime") == "SHOCK" else "INFO", proposal.get("rationale_tr", "Deterministik karar"), {"source": "DETERMINISTIC", "symbol": proposal.get("symbol"), "regime": proposal.get("regime"), "action": proposal.get("action")}, proposal["run_id"])
        return proposal

    def trigger_agent(self, event: str, run_id: str | None = None) -> bool:
        event_key = f"{event}:{run_id or ''}"
        if event != "preflight" and event_key == self.last_agent_event:
            return False
        prompt = f"/desk {event} {run_id or ''}".strip()
        started = time.monotonic()
        completed = subprocess.run(shlex.split(self.agent_command) + [prompt], text=True, capture_output=True, timeout=AGENT_TIMEOUT_SECONDS, check=False)
        self.last_agent_event = event_key
        if completed.returncode:
            append_event("SYSTEM", "ERROR", "Claude custom MCP görevi başarısız; fail-closed", {"event": event, "run_id": run_id, "error": (completed.stderr or completed.stdout)[:500]}, run_id)
            return False
        try:
            result = parse_agent_output(completed.stdout)
            self.apply_agent_result(event, run_id, result)
        except Exception as exc:
            append_event("SYSTEM", "ERROR", "Claude sonucu geçersiz; fail-closed", {"event": event, "run_id": run_id, "error": str(exc)[:300], "stdout": completed.stdout[:500], "stderr": completed.stderr[:500]}, run_id)
            return False
        append_event("SYSTEM", "INFO", "Claude custom MCP görevi tamamlandı", {"event": event, "run_id": run_id, "latency_ms": round((time.monotonic() - started) * 1000)}, run_id)
        return True

    def apply_private_account(self, result: dict) -> None:
        account = result.get("account") or {}
        nav = number(account.get("nav") or account.get("totalEqUsd"))
        balances = account.get("balances") or []
        usdt = next((item for item in balances if isinstance(item, dict) and item.get("ccy") == "USDT"), {})
        available = number(account.get("available_usdt") or usdt.get("availBal"))
        if nav <= 0:
            raise ValueError("custom MCP account NAV doğrulanamadı")
        state = load_state()
        now = utc_now()
        starting_nav = state.get("session", {}).get("starting_nav") or nav
        merge_state({
            "session": {"health": {"mcp": "READY", "account": "READY"}},
            "account": {
                "nav": nav,
                "available_usdt": available,
                "drawdown_pct": nav / starting_nav - 1 if starting_nav else 0,
                "account_observed_at": now,
                "account_age_seconds": 0,
                "total_exposure_usdt": number(account.get("total_exposure_usdt")),
                "positions": account.get("positions", state.get("account", {}).get("positions", [])),
                "open_orders": account.get("open_orders", state.get("account", {}).get("open_orders", [])),
                "recent_fills": account.get("recent_fills", state.get("account", {}).get("recent_fills", [])),
            },
        })
        refreshed = load_state()
        health = refreshed["session"]["health"]
        health["trade_ready"] = all(health.get(key) == "READY" for key in ("mcp", "account", "market", "watchdog"))
        merge_state({"session": {"health": health}})
        append_event("MCP_READ", "INFO", "Custom MCP private account preflight tamamlandı", {"provider": "CLAUDE_CUSTOM_MCP", "nav": nav, "available_usdt": available})

    def apply_agent_result(self, event: str, run_id: str | None, result: dict) -> None:
        if event == "preflight":
            self.apply_private_account(result)
            return
        if event != "candidate":
            append_event("DECISION", "WARN", "Emergency context custom MCP ile değerlendirildi", {"reason_codes": result.get("reason_codes", []), "rationale_tr": result.get("rationale_tr")}, run_id)
            return
        state = load_state()
        proposal = state.get("cycle", {}).get("proposal") or {}
        if not run_id or proposal.get("run_id") != run_id or result.get("candidate_id") != run_id:
            raise ValueError("candidate_id güncel state ile eşleşmiyor")
        self.apply_private_account(result)
        approved = bool(result.get("approve")) and not bool(result.get("smart_money_veto"))
        proposal = {
            **proposal,
            "action": proposal.get("candidate_action") if approved else "HOLD",
            "smart_money_veto": bool(result.get("smart_money_veto", True)),
            "rationale_tr": result.get("rationale_tr") or "Agent bağlam değerlendirmesi tamamlandı.",
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat(),
        }
        merge_state({"cycle": {"proposal": proposal}})
        gate = evaluate_risk(load_state())
        if load_state()["session"]["mode"] == "LIVE" and gate["verdict"] == "ALLOW":
            gate = {**gate, "verdict": "HOLD", "allowed_notional_usdt": 0, "reason_codes": ["LIVE_EXECUTOR_NOT_ENABLED"], "approved_call": None}
        merge_state({"cycle": {"gate": gate, "execution": {"run_id": run_id, "status": "SIMULATED" if gate["verdict"] == "ALLOW" else "NOT_SENT", "tool": None, "client_order_id": None, "order_id": None}}})
        append_event("GATE", "INFO", f"Agent sonrası risk gate: {gate['verdict']}", {"reason_codes": gate["reason_codes"], "allowed_notional_usdt": gate["allowed_notional_usdt"]}, run_id)

    def decision_cycle(self) -> None:
        self.refresh_features()
        proposal = self.classify()
        if proposal and proposal.get("candidate_action") in ENTRY_ACTIONS:
            self.trigger_agent("candidate", proposal["run_id"])
        elif proposal and proposal.get("regime") == "SHOCK" and load_state().get("session", {}).get("mode") == "LIVE":
            self.trigger_agent("emergency", proposal["run_id"])

    def heartbeat(self) -> None:
        state = load_state()
        watchdog = evaluate_watchdog(state)
        now = datetime.now(timezone.utc)
        ages = [self.age(item.get("market_observed_at"), now) for item in state.get("observation_symbols", [])]
        market_health = "READY" if len(ages) == len(SYMBOLS) and all(age is not None and age <= 20 for age in ages) else "STALE"
        account_age = self.age(state.get("account", {}).get("account_observed_at"), now)
        account_health = "READY" if account_age is not None and account_age <= 30 else "STALE"
        mcp_health = state.get("session", {}).get("health", {}).get("mcp", "UNKNOWN")
        trade_ready = mcp_health == account_health == market_health == "READY"
        merge_state({"session": {"mode": watchdog["mode"], "heartbeat_at": utc_now(), "health": {"mcp": mcp_health, "account": account_health, "market": market_health, "watchdog": "READY", "trade_ready": trade_ready}}, "account": {"account_age_seconds": round(account_age, 3) if account_age is not None else None}, "watchdog": {"status": watchdog["status"], "last_check_at": watchdog["checked_at"], "last_action": watchdog["reason"]}})
        if watchdog["actions"]:
            append_event("WATCHDOG", "WARN", watchdog["reason"] or "Emergency gerekli", {"actions": watchdog["actions"]})
            self.trigger_agent("emergency", watchdog["reason"])
        if state.get("session", {}).get("flatten_requested"):
            self.trigger_agent("emergency", "USER_FLATTEN")
        if state.get("session", {}).get("private_preflight_requested"):
            success = self.trigger_agent("preflight")
            patch = {"session": {"private_preflight_requested": False}}
            if not success:
                patch["session"]["health"] = {"mcp": "ERROR", "account": "STALE", "trade_ready": False}
            merge_state(patch)

    @staticmethod
    def age(value: str | None, now: datetime) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())
        except ValueError:
            return None

    def preflight(self) -> None:
        state = load_state()
        session_patch = {"health": {"mcp": "ON_DEMAND", "account": "STALE", "trade_ready": False}}
        if state.get("session", {}).get("mode") == "DISCONNECTED":
            session_patch["mode"] = "DRY_RUN"
        merge_state({"session": session_patch})
        self.refresh_instruments()
        self.refresh_fast_market()
        self.refresh_oi()
        self.refresh_features()
        self.heartbeat()
        append_event("SYSTEM", "INFO", "Public deterministic runner preflight tamamlandı", {"provider": "OKX_PUBLIC", "custom_mcp": "CLAUDE_OWNED"})

    def once(self) -> None:
        self.preflight()
        proposal = self.classify()
        if proposal and proposal.get("candidate_action") in ENTRY_ACTIONS:
            self.trigger_agent("candidate", proposal["run_id"])

    def run(self) -> None:
        self.preflight()
        started = time.monotonic()
        self.last_fast = self.last_regime = self.last_oi = started
        append_event("SYSTEM", "INFO", "Event-driven runner başladı", {"fast_market_s": FAST_MARKET_SECONDS, "regime_s": REGIME_SECONDS, "oi_s": OI_SECONDS})
        while True:
            now = time.monotonic()
            try:
                self.heartbeat()
                if now - self.last_fast >= FAST_MARKET_SECONDS:
                    self.refresh_fast_market()
                    shock = self.classify(shock_only=True)
                    if shock and load_state().get("session", {}).get("mode") == "LIVE":
                        self.trigger_agent("emergency", shock["run_id"])
                    self.last_fast = now
                if now - self.last_oi >= OI_SECONDS:
                    self.refresh_oi(); self.last_oi = now
                if now - self.last_regime >= REGIME_SECONDS:
                    self.decision_cycle(); self.last_regime = now
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                merge_state({"session": {"health": {"market": "ERROR", "trade_ready": False}}})
                append_event("SYSTEM", "ERROR", "Runner turu fail-closed tamamlandı", {"error": str(exc)[:500]})
            time.sleep(HEARTBEAT_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "once", "run"))
    args = parser.parse_args()
    try:
        lock_handle = open(RUNNER_LOCK, "w", encoding="utf-8")
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ERROR: başka bir Regime Desk runner zaten çalışıyor")
        return 1
    try:
        getattr(Runner(), args.command)()
    except (PublicMarketError, OSError) as exc:
        merge_state({"session": {"health": {"market": "ERROR", "trade_ready": False}}})
        append_event("SYSTEM", "ERROR", "Public runner başlatılamadı", {"error": str(exc)[:500]})
        print(f"ERROR: {exc}")
        return 1
    finally:
        lock_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
