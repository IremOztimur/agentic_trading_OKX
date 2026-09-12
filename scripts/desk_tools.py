#!/usr/bin/env python3
"""Tools the HQ agent may call.

Reads come straight from `run/state.json`, `run/events.jsonl` and live OKX
fills. The two tools that move money are marked `requires_confirmation`, so
Upsonic raises a ConfirmationPause before the function body runs and Telegram
shows the operator Confirm/Reject buttons. The model cannot approve its own
call, and underneath, `control.py` still demands its literal safety word.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from upsonic.tools import tool

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from atk import ATKClient
from journal import EVENTS_PATH, append_event, load_state
from perception import number
from pnl import portfolio_pnl, position_pnl
from regime import UNIVERSE

CONTROL = ROOT / "scripts" / "control.py"
FLATTEN_TOKEN = "FLATTEN"
LIVE_TOKEN = "CANLI"

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


# -- confirmed writes -----------------------------------------------------
# Both of these pause for a human before their body runs. Neither talks to the
# exchange directly: they go through control.py, which the runner obeys.

def run_control(*args: str) -> tuple[bool, str]:
    completed = subprocess.run([sys.executable, str(CONTROL), *args], capture_output=True,
                               text=True, cwd=str(ROOT), timeout=30, check=False)
    detail = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, detail[:300]


@tool(requires_confirmation=True)
def flatten_positions() -> dict[str, Any]:
    """Close every open position on the desk and halt new risk.

    The operator is shown Confirm/Reject buttons before this runs; you cannot
    approve it yourself. Use it when the operator asks to flatten, close
    everything, or exit all positions.
    """
    state = load_state()
    account = state.get("account", {})
    exposure = number(account.get("total_exposure_usdt"))
    positions = [{"symbol": p.get("symbol"), "exposure_usdt": p.get("exposure_usdt")}
                 for p in account.get("positions", [])]
    ok, detail = run_control("flatten", FLATTEN_TOKEN)
    append_event("SYSTEM", "WARN" if ok else "ERROR",
                 "HQ flatten onaylandı" if ok else "HQ flatten başarısız",
                 {"exposure_usdt": exposure, "detail": detail})
    return {"executed": ok, "closing_exposure_usdt": round(exposure, 4), "positions": positions,
            "mode_after": "HALTED" if ok else state.get("session", {}).get("mode"),
            "detail": detail,
            "note": "Runner bir sonraki döngüde satış emirlerini gönderir." if ok else ""}


@tool(requires_confirmation=True)
def arm_live() -> dict[str, Any]:
    """Arm LIVE mode so the desk can place real spot orders.

    The operator is shown Confirm/Reject buttons before this runs. control.py
    refuses unless every preflight health check is READY and the runner
    heartbeat is fresh, so this can fail for good reasons — report the reason
    verbatim if it does.
    """
    state = load_state()
    health = state.get("session", {}).get("health", {})
    ok, detail = run_control("live", LIVE_TOKEN)
    append_event("SYSTEM", "WARN" if ok else "ERROR",
                 "HQ LIVE moduna aldı" if ok else "HQ LIVE arm reddedildi",
                 {"detail": detail, "health": health})
    return {"executed": ok, "mode_after": "LIVE" if ok else state.get("session", {}).get("mode"),
            "health": health, "detail": detail,
            "note": "Desk artık gerçek spot emir gönderebilir." if ok else ""}


READ_TOOLS = [get_desk_status, get_symbol_decision, get_recent_changes]
CONFIRMED_TOOLS = [flatten_positions, arm_live]
ALL_TOOLS = READ_TOOLS + CONFIRMED_TOOLS
