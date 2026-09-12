#!/usr/bin/env python3
"""Tools the HQ agent may call.

Reads come straight from `run/state.json`, `run/events.jsonl` and live OKX
fills. The only write the agent can reach is `request_flatten`, and that one
does not flatten — it stages a confirmation that a human must complete with a
literal token, checked in Python before the agent is ever invoked.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from atk import ATKClient
from journal import EVENTS_PATH, append_event, load_state
from perception import number
from pnl import portfolio_pnl, position_pnl
from regime import UNIVERSE

PENDING_PATH = ROOT / "run" / "pending.json"
CONFIRMATION_TTL_SECONDS = 180
FLATTEN_TOKEN = "FLATTEN"

_client: ATKClient | None = None


def client() -> ATKClient:
    """One read-only ATK child per API process, started on first use."""
    global _client
    if _client is None or not _client.alive():
        _client = ATKClient(read_only=True)
        _client.start()
    return _client


def normalize_symbol(symbol: str) -> str | None:
    wanted = str(symbol or "").strip().upper()
    if not wanted:
        return None
    if "-" not in wanted:
        wanted = f"{wanted}-USDT"
    return wanted if wanted in UNIVERSE else None


# -- read tools -----------------------------------------------------------

def get_desk_status() -> dict[str, Any]:
    """Current desk state: mode, NAV, session P&L, exposure, open positions,
    the last decision the engine made and the risk gate's verdict on it.

    Use this for questions like "how am I doing", "what is my P&L",
    "what is the desk doing right now", or any portfolio-level question.
    """
    state = load_state()
    session, cycle = state.get("session", {}), state.get("cycle", {})
    proposal, gate = cycle.get("proposal") or {}, cycle.get("gate") or {}
    execution = cycle.get("execution") or {}
    money = portfolio_pnl(client(), state)
    return {
        **money,
        "health": session.get("health"),
        "drawdown_pct": round(number(state.get("account", {}).get("drawdown_pct")) * 100, 4),
        "last_decision": {
            "symbol": proposal.get("symbol"), "regime": proposal.get("regime"),
            "action": proposal.get("action"), "conviction": proposal.get("conviction"),
            "requested_notional_usdt": proposal.get("requested_notional_usdt"),
            "rationale": proposal.get("rationale_tr"),
        },
        "risk_gate": {"verdict": gate.get("verdict"), "reason_codes": gate.get("reason_codes"),
                      "allowed_notional_usdt": gate.get("allowed_notional_usdt")},
        "execution": {"status": execution.get("status"), "order_id": execution.get("order_id")},
        "watched_symbols": list(UNIVERSE),
    }


def get_symbol_decision(symbol: str) -> dict[str, Any]:
    """Why the desk is doing what it is doing on ONE symbol.

    Returns the regime, the conviction, the three composite scores, every
    sensor's signed contribution to the decision, the raw sensor evidence, and
    that symbol's P&L. Use this for "why did you buy X", "why is X losing",
    "what do you see in X".

    symbol: a watched symbol such as "BTC", "ETH-USDT" or "SOL".
    """
    wanted = normalize_symbol(symbol)
    if not wanted:
        return {"error": f"{symbol} izlenmiyor", "watched_symbols": list(UNIVERSE)}
    state = load_state()
    row = next((item for item in state.get("symbols", []) if item.get("symbol") == wanted), None)
    if not row:
        return {"error": f"{wanted} için henüz karar yok"}
    held = next((number(p.get("base_amount")) for p in state.get("account", {}).get("positions", [])
                 if p.get("symbol") == wanted), 0.0)
    return {
        "symbol": wanted,
        "regime": row.get("regime"),
        "conviction": row.get("conviction"),
        "action": row.get("last_action"),
        "scores": row.get("scores"),
        "contributions": row.get("contributions"),
        "sensor_evidence": {key: (value or {}).get("evidence")
                            for key, value in (row.get("sensors") or {}).items()},
        "quorum_ok": row.get("quorum_ok"),
        "smart_money_stale": row.get("smart_money_veto"),
        "rationale": row.get("rationale_tr"),
        "pnl": position_pnl(client(), wanted, held) if held > 0 else {"held_base": 0.0},
    }


def get_recent_changes(minutes: int = 15) -> dict[str, Any]:
    """What has changed on the desk recently: regime flips, gate verdicts and
    real orders, over the last `minutes` minutes. Use this for "what happened",
    "what changed", "did you trade".
    """
    window = max(1, min(int(minutes or 15), 240))
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=window)
    decisions, orders, gates = [], [], []
    if EVENTS_PATH.exists():
        for line in EVENTS_PATH.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
                stamp = datetime.fromisoformat(event["ts"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
            if stamp < cutoff:
                continue
            data = event.get("data") or {}
            if event["type"] == "DECISION":
                decisions.append({"at": event["ts"], "symbol": data.get("symbol"),
                                  "regime": data.get("regime"), "action": data.get("action"),
                                  "conviction": data.get("conviction")})
            elif event["type"] == "MCP_WRITE":
                orders.append({"at": event["ts"], "level": event["level"],
                               "message": event["message"], "data": data})
            elif event["type"] == "GATE" and data.get("reason_codes") != ["NO_ACTION"]:
                gates.append({"at": event["ts"], "message": event["message"],
                              "reason_codes": data.get("reason_codes")})
    regimes = []
    for item in decisions:
        key = (item["symbol"], item["regime"])
        if not regimes or regimes[-1][0] != key:
            regimes.append((key, item["at"]))
    return {
        "window_minutes": window,
        "decision_count": len(decisions),
        "regime_timeline": [{"symbol": s, "regime": r, "since": at} for (s, r), at in regimes][-10:],
        "orders": orders[-10:],
        "gate_events": gates[-10:],
        "latest_decision": decisions[-1] if decisions else None,
    }


# -- the one guarded write ------------------------------------------------

def request_flatten() -> dict[str, Any]:
    """Ask to close every open position. This does NOT close anything: it
    stages a confirmation and returns what would be closed. The human must
    then reply with the exact word FLATTEN, which is verified outside this
    agent. Use this when the operator asks to flatten, close everything, or
    exit all positions.
    """
    state = load_state()
    account = state.get("account", {})
    exposure = number(account.get("total_exposure_usdt"))
    positions = [{"symbol": p.get("symbol"), "exposure_usdt": p.get("exposure_usdt")}
                 for p in account.get("positions", [])]
    expires = datetime.now(timezone.utc) + timedelta(seconds=CONFIRMATION_TTL_SECONDS)
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_PATH.write_text(json.dumps({
        "action": "FLATTEN", "exposure_usdt": exposure, "positions": positions,
        "staged_at": datetime.now(timezone.utc).isoformat(), "expires_at": expires.isoformat(),
    }, ensure_ascii=False), encoding="utf-8")
    append_event("SYSTEM", "WARN", "HQ flatten onayı beklemede",
                 {"exposure_usdt": exposure, "expires_at": expires.isoformat()})
    return {
        "staged": True, "exposure_usdt": exposure, "positions": positions,
        "expires_in_seconds": CONFIRMATION_TTL_SECONDS,
        "instruction": f"Tell the operator to reply with the exact word {FLATTEN_TOKEN} to confirm. "
                       "You cannot confirm it yourself and you must not claim anything was closed.",
    }


def pending_flatten() -> dict[str, Any] | None:
    """The staged confirmation, if one exists and has not expired."""
    if not PENDING_PATH.exists():
        return None
    try:
        pending = json.loads(PENDING_PATH.read_text(encoding="utf-8"))
        expires = datetime.fromisoformat(pending["expires_at"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
    if datetime.now(timezone.utc) > expires:
        PENDING_PATH.unlink(missing_ok=True)
        return None
    return pending


def confirm_flatten(token: str) -> dict[str, Any]:
    """Deterministic gate for the only financial action HQ can reach.

    Never called by the model — the webhook checks the literal token first.
    """
    if token.strip() != FLATTEN_TOKEN:
        return {"executed": False, "reason": "TOKEN_MISMATCH"}
    pending = pending_flatten()
    if not pending:
        return {"executed": False, "reason": "NO_PENDING_REQUEST"}
    PENDING_PATH.unlink(missing_ok=True)
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "control.py"), "flatten", FLATTEN_TOKEN],
        capture_output=True, text=True, cwd=str(ROOT), timeout=30, check=False,
    )
    ok = completed.returncode == 0
    append_event("SYSTEM", "WARN" if ok else "ERROR",
                 "HQ flatten onaylandı ve runner'a iletildi" if ok else "HQ flatten başarısız",
                 {"exposure_usdt": pending.get("exposure_usdt"),
                  "stdout": completed.stdout[:200], "stderr": completed.stderr[:200]})
    return {"executed": ok, "exposure_usdt": pending.get("exposure_usdt"),
            "positions": pending.get("positions"),
            "detail": (completed.stdout or completed.stderr).strip()[:300]}


READ_TOOLS = [get_desk_status, get_symbol_decision, get_recent_changes]
ALL_TOOLS = READ_TOOLS + [request_flatten]
