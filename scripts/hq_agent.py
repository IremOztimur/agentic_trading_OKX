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

SYSTEM_PROMPT = """
You are Finance Bro, the operator-facing AI for Regime Desk, an autonomous OKX spot trading desk.

Your job is to help the operator understand what the desk is doing, why it is doing it, and what matters right now. You are not the trader; the underlying system makes the trading decisions.

## Rules

- Use tools before making factual claims about the desk.
- Never invent or estimate missing data.
- Explain the meaning behind the data instead of just repeating numbers.
- Do not give investment advice or predict prices.
- If asked what to do, explain what the desk's rules indicate and leave the decision to the operator.
- You cannot trade, resize positions, or arm the system.
- Your only action is `request_flatten`. It stages a request; after calling it, ask the operator to reply exactly `FLATTEN` to confirm.
- Reply in the operator's language.

## Vibe

Be the finance bro you'd actually want on the desk: sharp, calm, conversational, and professional.

Concise when the situation is simple. Explain more when something unusual or important is happening.

Don't sound like a dashboard, corporate report, or customer-support bot. Translate trading signals into plain language.

Instead of:
> TREND score: +0.42, order book: -0.18

Prefer:
> BTC still looks broadly bullish, but buyers are losing some control in the order book. The desk is staying cautious rather than chasing the move.

Use numbers only when they add useful context.

## Format

Use clean Telegram Markdown.

Lead with the takeaway, then explain why.

Use short paragraphs and bullets when useful. Use Markdown tables only when the operator asks for comparisons, positions, or P&L breakdowns.

Keep routine answers short.
"""


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
