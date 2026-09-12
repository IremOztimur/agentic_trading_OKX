# Regime Desk

**An AI-operated autonomous trading desk that separates slow AI reasoning from fast deterministic execution.**
It continuously reads OKX market signals through the Agent Trade Kit MCP, identifies the market
regime, explains its decisions, and executes real spot trades behind a hard risk gate.

Regime-aware spot trading — not high-frequency, not tick-level. The desk classifies BTC, ETH and
SOL as `TREND`, `RANGE` or `SHOCK`, acts only on confirmed trend conviction, and shows the exact
sensor contributions behind every decision.

## Where the AI is

```text
        AI HQ  (Telegram)                ← CONTROL PLANE
              ↕  explanation · status
   ─────────────────────────────────────────────────────
        Trading engine (deterministic)   ← EXECUTION PLANE
        Perception → Regime → Decision → Risk gate → OKX
```

No LLM sits on the trading path. The engine decides with arithmetic in milliseconds; the LLM
answers for it afterwards, in words, from the decision the engine already recorded. Asking a model
"should I buy BTC?" every few seconds produces answers that are stale before they arrive — the
previous version of this desk waited up to 180 seconds for one, and that is exactly what this
architecture removes.

## Pipeline

```text
OKX Agent Trade Kit MCP  (local stdio, scripts/atk.py)
        ↓  market_get_* · smartmoney_get_* · account_get_* · spot_place_order
PERCEPTION   scripts/perception.py    5 sensors → score ∈ [-1,+1] + freshness
        ↓
DECISION     scripts/regime.py        TREND / RANGE / SHOCK + conviction
        ↓
RISK GATE    scripts/risk_gate.py     sizing, caps, drawdown, safe close
        ↓
EXECUTION    scripts/execute.py       spot_place_order + order lookup
```

### The five sensors

Every sensor is an ATK MCP call, normalized to a score in `[-1, +1]` signed toward risk-on, and
weighted by freshness at decision time. Past its TTL a sensor contributes **zero** — it never
votes with stale data.

| Sensor | ATK MCP tool | Weight | TTL | Reads |
|---|---|---|---|---|
| `trend` | `market_get_indicator` (supertrend) + `market_get_candles` 15m | 0.30 | 120s | supertrend direction × ADX strength |
| `smart_money` | `smartmoney_get_signal_overview_by_filter` | 0.25 | 420s | weighted long ratio of top-PnL traders |
| `microstructure` | `market_get_orderbook` + `market_get_trades` | 0.20 | 45s | depth imbalance + taker imbalance |
| `momentum` | `market_get_indicator` (rsi 15m) | 0.15 | 120s | RSI distance from 50; inverted in RANGE |
| `volatility` | `market_get_candles` 1m | 0.10 | 120s | 5m return vs ATR, volume z-score |

A wide spread (`spread_multiple ≥ 3`) is a hard veto rather than a score, and if live sensor
weight drops below 60% of nominal the desk holds with `SENSOR_QUORUM_LOST`.

### The playbook

`conviction` is the freshness-weighted mean of the five sensors.

| Regime | Condition | Action |
|---|---|---|
| `TREND` | `conviction ≥ +0.35` | **BUY**, size ∝ `(conviction − 0.35) / 0.65` |
| `TREND` | `conviction ≤ −0.35` | **REDUCE** if holding, else HOLD — spot-only, never short |
| `RANGE` | — | **HOLD** — an explicit no-trade state |
| `SHOCK` | `shock_level ≥ 0.60` | **REDUCE** if holding, else HOLD |

A regime flip needs a 0.10 margin over the runner-up and two consecutive agreeing cycles. SHOCK
skips hysteresis and fires immediately. Range regimes support a grid strategy as a plug-in; it is
deliberately not wired in this version.

Cadence: order book and tape every 5s, candles and indicators every 30s, smart money every 120s,
account every 20s, decision every 15s. Decisions read a cache, so they never wait on the network.

## Running it

```bash
npm install -g @okx_ai/okx-trade-mcp     # the ATK MCP server the desk speaks to
python3 scripts/atk.py probe             # verify credentials and tool access
```

`.env` needs `OKX_API_KEY`, `OKX_SECRET_KEY`, `OKX_PASSPHRASE`. The desk reads them straight into
the ATK child process; no OAuth and no `~/.okx/config.toml` are required.

```bash
python3 scripts/runner.py run            # terminal 1 — the trading engine
./init.sh                                # terminal 2 — dashboard on :8765
uvicorn api:app --app-dir scripts --port 8900   # terminal 3 — HQ API
```

Control plane:

```bash
python3 scripts/control.py status
python3 scripts/control.py live CANLI    # arm real orders
python3 scripts/control.py pause
python3 scripts/control.py flatten FLATTEN
```

### Telegram AI HQ

A FastAPI service forwards every Telegram message to Claude (via the Upsonic agent framework),
which answers by calling tools that read the desk's real state:

| Tool | Does | Gated |
|---|---|---|
| `get_desk_status()` | mode, NAV, session P&L, exposure, positions, last decision, gate verdict | — |
| `get_symbol_decision(symbol)` | regime, conviction, every sensor's signed contribution, that symbol's P&L | — |
| `get_recent_changes(minutes)` | regime flips, gate events and real orders in the window | — |
| `flatten_positions()` | closes every position and halts new risk | Confirm/Reject |
| `arm_live()` | arms LIVE so the desk can place real orders | Confirm/Reject |

```text
You:  How am I doing?
HQ:   NAV 29.9639 USDT / Session -0.0283 USDT (-0.09%) / Exposure 0.0000

You:  Why is ETH losing?
HQ:   ETH is down -0.06% unrealized. Trend +0.29 and smart money +0.09 are
      holding the position; microstructure turned -0.01. Most of the loss is
      0.0291 USDT of fees across 28 fills.

You:  Flatten everything.
HQ:   Closing all positions and halting new risk.
      [ Confirm ]  [ Reject ]        <- inline buttons, not a typed word
```

Claude can inspect and operate the desk, but every financial action still passes through
deterministic controls, in three layers:

1. **The agent cannot approve itself.** `flatten_positions` and `arm_live` are declared
   `requires_confirmation=True`, so Upsonic raises a `ConfirmationPause` *before the function
   body runs* and Telegram renders Confirm/Reject buttons. Verified: asking to flatten returns
   `is_paused=True, pause_reason=confirmation` with the body untouched.
2. **The tools do not touch the exchange.** They shell out to `scripts/control.py`, which still
   demands its own literal safety word (`FLATTEN`, `CANLI`) and refuses to arm LIVE unless every
   preflight health check is READY and the runner heartbeat is fresh.
3. **The risk gate is unchanged.** The actual selling is done by the runner through
   `scripts/execute.py` and the same gate as any other order.

The agent can never buy, size, or set a limit at all — there is no tool for it.

Transport is Upsonic's `TelegramInterface`, which owns the webhook route, the secret-token
check, the user allowlist, chat sessions and message splitting. `scripts/api.py` only subclasses
it to add the guard, and is under 80 lines.

Setup:

```bash
pip install -r requirements.txt
ngrok http 8900
# put the tunnel in .env as HQ_PUBLIC_URL, then:
uvicorn api:app --host 127.0.0.1 --port 8900 --app-dir scripts
```

The webhook registers itself on startup from `HQ_PUBLIC_URL`. `.env` also needs
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET` and `ANTHROPIC_API_KEY`.
Messages from any other user id are dropped.

`/live`, `go live`, `let's go live`, `canli` and `canlı` bypass model latency and return a
signed Confirm/Cancel keyboard directly. The confirmation expires after five minutes, survives
an API restart, and still executes through `control.py live CANLI`.

## Safety

Spot only; no swap, futures, options, earn, transfer, withdraw or leverage writes. Risk per trade
0.35% NAV, coin cap 25%, total cap 50%, soft drawdown −3%, hard halt −5%, safe close 19:20
Europe/Istanbul. Live orders are additionally capped at `min(10 USDT, NAV × 5%)` in
`scripts/execute.py`. The client order id is the run id, and an order is never resent without
looking that id up first. An explicit `CANLI` re-arm after 19:20 overrides safe close only for
that Istanbul calendar day; the override expires automatically the next day.

## Fork it

Adding a signal is one function. Write a `sense_*` that returns `record(id, score, source,
evidence)`, add its weight and TTL to `WEIGHTS` and `TTL` in `scripts/perception.py`, and call it
from a cadence tier. Everything downstream — scoring, explainability, dashboard, Telegram — picks
it up with no further changes. News, funding and open interest are all available on the same ATK
MCP surface and are the obvious next three.

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Competition and educational project; not investment advice. Use a dedicated sub-account for LIVE.

Sources: [OKX Agent Trade Kit](https://www.okx.com/docs-v5/agent_en/), [`@okx_ai/okx-trade-mcp`](https://www.npmjs.com/package/@okx_ai/okx-trade-mcp).
