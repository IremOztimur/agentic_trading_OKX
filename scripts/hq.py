#!/usr/bin/env python3
"""Telegram AI HQ: the control plane's read-only voice.

HQ never trades. It reads the desk's own state and explains the decision the
deterministic engine already made — the numbers come from `cycle.contributions`,
the LLM only puts them into words.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from atk import load_env
from journal import append_event, load_state
from perception import number
from regime import UNIVERSE

API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 50
EXPLAIN_TIMEOUT = 60
AGENT_COMMAND = "claude -p --output-format text --permission-mode dontAsk --allowedTools '' --no-session-persistence"

SENSOR_LABELS = {"trend": "Trend", "momentum": "Momentum", "microstructure": "Order flow",
                 "volatility": "Volatility", "smart_money": "Smart Money"}


def api(token: str, method: str, **params: Any) -> dict[str, Any]:
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=POLL_TIMEOUT + 10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "description": str(exc)}


# -- facts ----------------------------------------------------------------

def symbol_in(text: str) -> str | None:
    upper = text.upper()
    for symbol in UNIVERSE:
        if symbol in upper or symbol.split("-")[0] in re.findall(r"[A-Z]{2,5}", upper):
            return symbol
    return None


def facts_for(symbol: str | None) -> dict[str, Any]:
    """Everything HQ is allowed to talk about, straight from the desk's state."""
    state = load_state()
    session, account, cycle = state.get("session", {}), state.get("account", {}), state.get("cycle", {})
    proposal, gate = cycle.get("proposal") or {}, cycle.get("gate") or {}
    execution = cycle.get("execution") or {}
    symbols = {item.get("symbol"): item for item in state.get("symbols", [])}
    target = symbols.get(symbol) if symbol else symbols.get(proposal.get("symbol"))

    payload = {
        "mode": session.get("mode"),
        "nav_usdt": account.get("nav"),
        "available_usdt": account.get("available_usdt"),
        "drawdown_pct": round(number(account.get("drawdown_pct")) * 100, 3),
        "open_positions": account.get("positions") or [],
        "health": session.get("health"),
        "last_decision": {
            "symbol": proposal.get("symbol"), "regime": proposal.get("regime"),
            "action": proposal.get("action"), "conviction": proposal.get("conviction"),
            "requested_notional_usdt": proposal.get("requested_notional_usdt"),
            "rationale_tr": proposal.get("rationale_tr"),
        },
        "risk_gate": {"verdict": gate.get("verdict"), "reason_codes": gate.get("reason_codes"),
                      "allowed_notional_usdt": gate.get("allowed_notional_usdt")},
        "execution": {"status": execution.get("status"), "order_id": execution.get("order_id")},
    }
    if target:
        payload["symbol_detail"] = {
            "symbol": target.get("symbol"), "regime": target.get("regime"),
            "conviction": target.get("conviction"), "action": target.get("last_action"),
            "scores": target.get("scores"), "rationale_tr": target.get("rationale_tr"),
            "contributions": target.get("contributions"),
            "sensor_evidence": {key: (value or {}).get("evidence") for key, value in (target.get("sensors") or {}).items()},
        }
    return payload


def template_answer(facts: dict[str, Any]) -> str:
    """Deterministic fallback — used whenever the model is unavailable."""
    detail = facts.get("symbol_detail")
    gate = facts.get("risk_gate") or {}
    if not detail:
        decision = facts.get("last_decision") or {}
        return (f"Mode {facts.get('mode')} · NAV {facts.get('nav_usdt')} USDT\n"
                f"Son karar: {decision.get('symbol')} {decision.get('regime')} → {decision.get('action')}\n"
                f"Risk gate: {gate.get('verdict')} ({', '.join(gate.get('reason_codes') or [])})")
    drivers = ", ".join(
        f"{SENSOR_LABELS.get(item['id'], item['id'])} {item['delta']:+.2f}"
        for item in (detail.get("contributions") or [])[:3]
    )
    line = (f"{detail['symbol']} is in {detail['regime']} regime with "
            f"{number(detail.get('conviction')):+.2f} conviction.\nMain drivers: {drivers}.\n"
            f"Action: {detail.get('action')}.")
    if gate.get("verdict") == "ALLOW":
        line += f" Risk gate allowed {gate.get('allowed_notional_usdt')} USDT."
    else:
        line += f" Risk gate: {gate.get('verdict')} ({', '.join(gate.get('reason_codes') or [])})."
    return line


def explain(question: str, facts: dict[str, Any]) -> str:
    """Ask Claude to phrase the answer. The numbers are already decided."""
    prompt = (
        "You are the reporting voice of an autonomous OKX spot trading desk. "
        "Answer the operator's question using ONLY the JSON facts below. "
        "Never invent a number, a symbol or an action that is not in the facts. "
        "Never give trading advice and never suggest a trade.\n"
        "Output ONLY the answer: same language as the question, at most 4 short plain-text lines. "
        "No preamble, no notes to the user, no caveats about your own tools or environment, "
        "no code, no markdown, nothing after the answer.\n\n"
        f"FACTS:\n{json.dumps(facts, ensure_ascii=False)}\n\nQUESTION: {question}"
    )
    try:
        # Run outside the repo so the project's CLAUDE.md cannot leak into the reply.
        completed = subprocess.run(shlex.split(AGENT_COMMAND) + [prompt], text=True,
                                   capture_output=True, timeout=EXPLAIN_TIMEOUT, check=False,
                                   cwd=tempfile.gettempdir())
    except subprocess.TimeoutExpired:
        return template_answer(facts)
    answer = trim(completed.stdout or "")
    return answer if completed.returncode == 0 and answer else template_answer(facts)


META = re.compile(r"^\s*(?:---+|note[: ]|not[:\s]|snippet|çalıştıramadım|i (?:could|cannot|can't))", re.I)


def trim(raw: str) -> str:
    """Keep the answer, drop any commentary the model appends after it."""
    lines = []
    for line in raw.strip().splitlines():
        if META.match(line):
            break
        lines.append(line)
        if len(lines) >= 4:
            break
    return "\n".join(lines).strip()


# -- routing --------------------------------------------------------------

def status_text(facts: dict[str, Any]) -> str:
    decision = facts.get("last_decision") or {}
    gate = facts.get("risk_gate") or {}
    health = facts.get("health") or {}
    positions = facts.get("open_positions") or []
    held = ", ".join(f"{p['symbol']} {round(number(p.get('exposure_usdt')), 2)} USDT" for p in positions) or "yok"
    return (
        f"REGIME DESK · {facts.get('mode')}\n"
        f"NAV {facts.get('nav_usdt')} USDT · available {facts.get('available_usdt')} · dd {facts.get('drawdown_pct')}%\n"
        f"Pozisyon: {held}\n"
        f"Son karar: {decision.get('symbol')} {decision.get('regime')} "
        f"conviction {decision.get('conviction')} → {decision.get('action')}\n"
        f"Risk gate: {gate.get('verdict')} ({', '.join(gate.get('reason_codes') or [])})\n"
        f"Health: mcp {health.get('mcp')} · account {health.get('account')} · market {health.get('market')}"
    )


def respond(text: str) -> str:
    """Two intents only: status, and why. Anything else gets pointed at those."""
    cleaned = text.strip()
    lowered = cleaned.lower().lstrip("/")
    if lowered.startswith(("status", "durum")):
        return status_text(facts_for(None))
    if lowered.startswith(("why", "neden", "explain", "aciklama", "açıklama")) or "?" in cleaned:
        return explain(cleaned, facts_for(symbol_in(cleaned)))
    return ("Regime Desk HQ salt-okunur bir açıklama arayüzüdür.\n"
            "· `status` — mode, NAV, pozisyon, son karar\n"
            "· `why BTC?` — o kararın sensor gerekçesi")


def main() -> int:
    env = load_env()
    token = env.get("TELEGRAM_BOT_TOKEN")
    allowed = str(env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token:
        print("ERROR: .env içinde TELEGRAM_BOT_TOKEN yok")
        return 1

    me = api(token, "getMe")
    if not me.get("ok"):
        print(f"ERROR: Telegram getMe başarısız: {me.get('description')}")
        return 1
    print(f"HQ bağlandı: @{me['result'].get('username')} · izinli chat: {allowed or 'HEPSİ (uyarı)'}")
    append_event("SYSTEM", "INFO", "Telegram HQ başladı", {"bot": me["result"].get("username")})

    offset = None
    while True:
        updates = api(token, "getUpdates", offset=offset, timeout=POLL_TIMEOUT)
        if not updates.get("ok"):
            time.sleep(3)
            continue
        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message") or update.get("edited_message") or {}
            chat_id = str((message.get("chat") or {}).get("id") or "")
            text = message.get("text")
            if not text or not chat_id:
                continue
            if allowed and chat_id != allowed:
                append_event("SYSTEM", "WARN", "Telegram HQ yetkisiz chat reddedildi", {"chat_id": chat_id})
                continue
            append_event("SYSTEM", "INFO", "Telegram HQ isteği", {"chat_id": chat_id, "text": text[:200]})
            try:
                reply = respond(text)
            except Exception as exc:
                reply = f"HQ hatası: {str(exc)[:200]}"
            api(token, "sendMessage", chat_id=chat_id, text=reply[:3900])


if __name__ == "__main__":
    raise SystemExit(main())
