#!/usr/bin/env python3
"""The HQ agent: Claude, via Upsonic, with read access to the desk.

The agent inspects and narrates. It cannot decide a trade, size one, or
execute one — the only write it can reach stages a confirmation that a human
completes with a literal token, checked deterministically outside the agent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from atk import load_env
from desk_tools import ALL_TOOLS

MODEL = os.environ.get("HQ_MODEL", "anthropic/claude-sonnet-4-5")
TIMEOUT_SECONDS = 90

SYSTEM_PROMPT = """You are HQ, the operator interface for Regime Desk — an autonomous OKX spot
trading desk. The desk decides and trades on its own, deterministically. You inspect it and
explain it. You are the control plane, not the trader.

RULES
- Always call a tool before stating any fact. Never state a number you did not get from a tool.
- If a tool has no data for something, say so plainly. Never estimate, extrapolate or guess.
- Never give investment advice, never predict prices, never suggest a trade. If asked what to do,
  describe what the desk's own rules would do and leave the decision to the operator.
- You cannot buy, sell, size or arm anything. The only action you can start is request_flatten,
  which merely stages a confirmation. After calling it, tell the operator to reply with the exact
  word FLATTEN. Never say positions are closed — you are not the one who closes them.
- Answer in the same language the operator writes in.

STYLE
Telegram plain text. No markdown tables, no asterisks, no headers. Keep it under 12 lines.
Use simple aligned lines for numbers, for example:
  NAV        29.9639 USDT
  Session    -0.0283 USDT  (-0.09%)
Money to 4 decimals, percentages to 2. Lead with the answer, then the evidence in one or two
lines. When you explain a decision, name the sensors that actually moved it and their signed
contributions."""


def build_agent():
    """Fresh agent per request: Telegram turns are short and independent."""
    env = load_env()
    key = env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(".env içinde ANTHROPIC_API_KEY yok")
    os.environ["ANTHROPIC_API_KEY"] = key
    from upsonic import Agent

    return Agent(model=MODEL, name="Regime Desk HQ", system_prompt=SYSTEM_PROMPT,
                 tools=list(ALL_TOOLS), retry=1)


def ask(question: str) -> str:
    """Run one operator turn and return plain text for Telegram."""
    from upsonic import Task

    agent = build_agent()
    answer = agent.do(Task(description=question), timeout=TIMEOUT_SECONDS)
    text = str(answer).strip() if answer is not None else ""
    return text or "HQ yanıt üretemedi."


def main() -> int:
    question = " ".join(sys.argv[1:]) or "How am I doing?"
    print(ask(question))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
