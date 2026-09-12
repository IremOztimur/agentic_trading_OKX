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
python3 scripts/hq.py                    # terminal 3 — Telegram HQ (optional)
```

Control plane:

```bash
python3 scripts/control.py status
python3 scripts/control.py live CANLI    # arm real orders
python3 scripts/control.py pause
python3 scripts/control.py flatten FLATTEN
```

### Telegram HQ

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. HQ is read-only and supports two things:

- `status` — mode, NAV, positions, last decision, gate verdict
- `why BTC?` — the sensor contributions behind that decision, in plain language

HQ cannot place, size or authorize a trade. It reads `run/state.json` and speaks.

## Safety

Spot only; no swap, futures, options, earn, transfer, withdraw or leverage writes. Risk per trade
0.35% NAV, coin cap 25%, total cap 50%, soft drawdown −3%, hard halt −5%, safe close 19:20
Europe/Istanbul. Live orders are additionally capped at `min(10 USDT, NAV × 5%)` in
`scripts/execute.py`. The client order id is the run id, and an order is never resent without
looking that id up first.

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
