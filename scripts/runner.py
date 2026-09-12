#!/usr/bin/env python3
"""Regime Desk runner: one deterministic loop, no LLM on the decision path.

Every market read is an OKX ATK MCP call. Perception refreshes on three
cadence tiers into a cache; the decision reads that cache, so a decision is
never waiting on a network round-trip and never waiting on a model.
"""

from __future__ import annotations

import argparse
import fcntl
import time
from datetime import datetime, timezone

import perception
from atk import ATKClient, ATKError
from execute import build_call, execute, flatten
from journal import append_event, load_state, merge_state, utc_now
from perception import number
from regime import UNIVERSE, classify_state
from risk_gate import evaluate as evaluate_risk
from watchdog import evaluate as evaluate_watchdog

SYMBOLS = UNIVERSE
LOOP_SECONDS = 5
FAST_SECONDS = 5
MID_SECONDS = 30
SLOW_SECONDS = 120
ACCOUNT_SECONDS = 20
DECISION_SECONDS = 15
SHOCK_COOLDOWN_SECONDS = 300
RUNNER_LOCK = "/tmp/regime-desk-runner.lock"
BASES = {symbol.split("-")[0] for symbol in SYMBOLS}


class Runner:
    def __init__(self, client: ATKClient | None = None) -> None:
        self.client = client or ATKClient(read_only=True)
        self.last = {"fast": 0.0, "mid": 0.0, "slow": 0.0, "account": 0.0, "decision": 0.0}
        self.shock_latch: dict[str, float] = {}
        self.live_armed = False

    # -- ATK reads --------------------------------------------------------
    def timed(self, label: str, call, *args, **kwargs):
        started = time.monotonic()
        try:
            result = call(*args, **kwargs)
        except Exception as exc:
            append_event("MARKET_READ", "ERROR", f"ATK MCP: {label} başarısız",
                         {"provider": "OKX_ATK_MCP", "error": str(exc)[:300]})
            raise
        append_event("MARKET_READ", "INFO", f"ATK MCP: {label}",
                     {"provider": "OKX_ATK_MCP", "latency_ms": round((time.monotonic() - started) * 1000)})
        return result

    def arm_live(self, live: bool) -> None:
        """LIVE swaps the read-only ATK child for one that can place orders."""
        if live == self.live_armed:
            return
        self.client.close()
        self.client = ATKClient(read_only=not live)
        self.live_armed = live
        append_event("SYSTEM", "WARN", f"ATK MCP {'LIVE write' if live else 'read-only'} moduna alındı", {})

    def refresh_instruments(self) -> None:
        rows = self.timed("spot instruments", self.client.call, "market_get_instruments", instType="SPOT")
        catalog = {row.get("instId"): row for row in rows or [] if isinstance(row, dict)}
        observations = self.observations()
        for symbol in SYMBOLS:
            source = catalog.get(symbol)
            if not source:
                raise ATKError(f"Instrument bulunamadı: {symbol}")
            record = observations.setdefault(symbol, {"symbol": symbol, "features": {}, "sensors": {}})
            record["features"] = {**record.get("features", {}), "minSz": source.get("minSz"),
                                  "lotSz": source.get("lotSz"), "tickSz": source.get("tickSz"),
                                  "instrument_state": source.get("state")}
        self.save_observations(observations)

    def refresh_account(self) -> None:
        rows = self.timed("account balance", self.client.call, "account_get_balance")
        row = (rows or [{}])[0] if isinstance(rows, list) else rows
        details = (row or {}).get("details") or []
        nav = number((row or {}).get("totalEq"))
        usdt = next((item for item in details if item.get("ccy") == "USDT"), {})
        available = number(usdt.get("availBal"))
        positions, exposure = [], 0.0
        for item in details:
            base = item.get("ccy")
            if base not in BASES:
                continue
            value = number(item.get("eqUsd"))
            if value <= 0.01:
                continue
            positions.append({"symbol": f"{base}-USDT", "base_amount": number(item.get("eq")),
                              "exposure_usdt": round(value, 8), "owner": "DESK"})
            exposure += value
        if nav <= 0:
            nav = available + exposure
        if nav <= 0:
            raise ATKError("ATK MCP account NAV doğrulanamadı")

        state = load_state()
        session = state.get("session", {})
        patch = {
            "session": {"health": {"mcp": "READY", "account": "READY"}},
            "account": {"nav": round(nav, 8), "available_usdt": round(available, 8),
                        "total_exposure_usdt": round(exposure, 8), "positions": positions,
                        "account_observed_at": utc_now(), "account_age_seconds": 0,
                        "drawdown_pct": nav / number(session.get("starting_nav"), nav) - 1 if number(session.get("starting_nav")) else 0.0},
        }
        if not session.get("starting_nav"):
            patch["session"]["starting_nav"] = round(nav, 8)
            patch["session"]["starting_inventory"] = {base: 0.0 for base in sorted(BASES)}
        merge_state(patch)
        # Exposure must reach the observations too, so REDUCE decisions can see it.
        observations = self.observations()
        held = {item["symbol"]: item["exposure_usdt"] for item in positions}
        for symbol, record in observations.items():
            record["exposure_usdt"] = held.get(symbol, 0.0)
        self.save_observations(observations)

    # -- perception -------------------------------------------------------
    def observations(self) -> dict[str, dict]:
        return {item.get("symbol"): item for item in load_state().get("observation_symbols", [])}

    def save_observations(self, observations: dict[str, dict]) -> None:
        merge_state({"observation_symbols": [observations[symbol] for symbol in SYMBOLS if symbol in observations]})

    def refresh_tier(self, tier: str) -> None:
        refresh = {"fast": perception.refresh_fast, "mid": perception.refresh_mid, "slow": perception.refresh_slow}[tier]
        observations = self.observations()
        for symbol in SYMBOLS:
            previous = observations.setdefault(symbol, {"symbol": symbol, "features": {}, "sensors": {}})
            patch = self.timed(f"{symbol} {tier}", refresh, self.client, symbol, previous)
            observations[symbol] = perception.merge_observation(previous, patch)
        self.save_observations(observations)
        merge_state({"session": {"health": {"market": "READY"}}})

    # -- decision ---------------------------------------------------------
    def shock_is_new(self, proposal: dict) -> bool:
        """Edge-trigger SHOCK so one episode does not fire on every cycle."""
        symbol = str(proposal.get("symbol") or "")
        now = time.monotonic()
        opened = self.shock_latch.get(symbol)
        if opened and now - opened < SHOCK_COOLDOWN_SECONDS:
            return False
        self.shock_latch[symbol] = now
        return True

    def release_latches(self, symbols: list[dict]) -> None:
        for item in symbols:
            if item.get("regime") != "SHOCK":
                self.shock_latch.pop(item.get("symbol"), None)

    def decide(self) -> dict:
        state = load_state()
        symbols, proposal = classify_state(state)
        self.release_latches(symbols)
        merge_state({"symbols": symbols, "cycle": {"run_id": proposal["run_id"], "proposal": proposal,
                                                   "execution": {"run_id": proposal["run_id"], "status": "NOT_SENT",
                                                                 "tool": None, "client_order_id": None, "order_id": None}}})
        level = "WARN" if proposal.get("regime") == "SHOCK" or proposal.get("action") == "REDUCE" else "INFO"
        if proposal.get("regime") != "SHOCK" or self.shock_is_new(proposal):
            append_event("DECISION", level, proposal.get("rationale_tr", "Karar"),
                         {"symbol": proposal.get("symbol"), "regime": proposal.get("regime"),
                          "action": proposal.get("action"), "conviction": proposal.get("conviction"),
                          "scores": proposal.get("scores")}, proposal["run_id"])

        state = load_state()
        # In LIVE the gate approves a concrete tool call, so it has to see one:
        # attach the mapping before evaluating, not after.
        # Always write the key, never only on the actionable branch: state is
        # deep-merged, so a stale call from an earlier symbol would survive.
        call = build_call(proposal) if state["session"]["mode"] == "LIVE" else None
        proposal = {**proposal, "mcp_call": call}
        merge_state({"cycle": {"proposal": proposal}})
        state = load_state()
        gate = evaluate_risk(state)
        merge_state({"cycle": {"gate": gate}})
        append_event("GATE", "ERROR" if gate["verdict"] == "HALT" else "INFO", f"Risk gate: {gate['verdict']}",
                     {"reason_codes": gate["reason_codes"], "allowed_notional_usdt": gate["allowed_notional_usdt"]},
                     proposal["run_id"])

        if gate["verdict"] == "ALLOW" and state["session"]["mode"] == "LIVE":
            self.arm_live(True)
            execute(self.client, load_state())
        elif gate["verdict"] == "ALLOW":
            merge_state({"cycle": {"execution": {"run_id": proposal["run_id"], "status": "SIMULATED",
                                                 "tool": "spot_place_order", "client_order_id": None, "order_id": None}}})
        return proposal

    # -- housekeeping -----------------------------------------------------
    @staticmethod
    def age(value: str | None, now: datetime) -> float | None:
        if not value:
            return None
        try:
            return max(0.0, (now - datetime.fromisoformat(value.replace("Z", "+00:00"))).total_seconds())
        except ValueError:
            return None

    def heartbeat(self) -> None:
        state = load_state()
        watchdog = evaluate_watchdog(state)
        now = datetime.now(timezone.utc)
        ages = [self.age(item.get("market_observed_at"), now) for item in state.get("observation_symbols", [])]
        market = "READY" if len(ages) == len(SYMBOLS) and all(age is not None and age <= 60 for age in ages) else "STALE"
        account_age = self.age(state.get("account", {}).get("account_observed_at"), now)
        account = "READY" if account_age is not None and account_age <= 30 else "STALE"
        mcp = "READY" if self.client.alive() else "ERROR"
        merge_state({
            "session": {"mode": watchdog["mode"], "heartbeat_at": utc_now(),
                        "health": {"mcp": mcp, "account": account, "market": market, "watchdog": "READY",
                                   "trade_ready": mcp == account == market == "READY"}},
            "account": {"account_age_seconds": round(account_age, 3) if account_age is not None else None},
            "watchdog": {"status": watchdog["status"], "last_check_at": watchdog["checked_at"],
                         "last_action": watchdog["reason"]},
        })
        if watchdog["actions"]:
            append_event("WATCHDOG", "WARN", watchdog["reason"] or "Risk azaltma gerekli", {"actions": watchdog["actions"]})
            # The watchdog decides; the runner executes. Nothing here waits on a model.
            if any(item.get("action") == "FLATTEN_AGENT_INVENTORY" for item in watchdog["actions"]):
                self.arm_live(True)
                flatten(self.client, load_state())
        if state.get("session", {}).get("mode") != "LIVE" and self.live_armed:
            self.arm_live(False)

    def close(self) -> None:
        self.client.close()

    # -- entry points -----------------------------------------------------
    def preflight(self) -> None:
        state = load_state()
        if state.get("session", {}).get("mode") == "DISCONNECTED":
            merge_state({"session": {"mode": "DRY_RUN"}})
        self.client.start()
        append_event("SYSTEM", "INFO", "ATK MCP bağlandı", {"tools": len(self.client.tools), "modules": self.client.modules})
        self.refresh_instruments()
        self.refresh_account()
        for tier in ("mid", "fast", "slow"):
            self.refresh_tier(tier)
        self.heartbeat()
        append_event("SYSTEM", "INFO", "Runner preflight tamamlandı", {"provider": "OKX_ATK_MCP", "symbols": list(SYMBOLS)})

    def once(self) -> None:
        self.preflight()
        self.decide()

    def run(self) -> None:
        self.preflight()
        now = time.monotonic()
        self.last = {key: now for key in self.last}
        append_event("SYSTEM", "INFO", "Deterministik runner başladı",
                     {"fast_s": FAST_SECONDS, "mid_s": MID_SECONDS, "slow_s": SLOW_SECONDS, "decision_s": DECISION_SECONDS})
        while True:
            now = time.monotonic()
            try:
                self.heartbeat()
                for tier, interval in (("fast", FAST_SECONDS), ("mid", MID_SECONDS), ("slow", SLOW_SECONDS)):
                    if now - self.last[tier] >= interval:
                        self.refresh_tier(tier)
                        self.last[tier] = now
                if now - self.last["account"] >= ACCOUNT_SECONDS:
                    self.refresh_account()
                    self.last["account"] = now
                if now - self.last["decision"] >= DECISION_SECONDS:
                    self.decide()
                    self.last["decision"] = now
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                merge_state({"session": {"health": {"market": "ERROR", "trade_ready": False}}})
                append_event("SYSTEM", "ERROR", "Runner turu fail-closed tamamlandı", {"error": str(exc)[:500]})
            time.sleep(LOOP_SECONDS)


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
    runner = Runner()
    try:
        getattr(runner, args.command)()
    except (ATKError, OSError) as exc:
        merge_state({"session": {"health": {"mcp": "ERROR", "trade_ready": False}}})
        append_event("SYSTEM", "ERROR", "Runner başlatılamadı", {"error": str(exc)[:500]})
        print(f"ERROR: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nRunner durduruldu")
    finally:
        runner.close()
        lock_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
