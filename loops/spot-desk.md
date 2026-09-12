# Spot Desk Loop

Trigger: `python3 scripts/runner.py run`

Cadence: heartbeat/watchdog 10s; ticker 15s; account 30s; regime 2m; OI/Smart Money/news 5m.

Work: OKX custom MCP read → normalize → deterministic features → RANGE/TREND/SHOCK → yalnızca actionable RANGE/TREND adayında Claude APPROVE/VETO → deterministic risk gate → gerekiyorsa MCP write.

Verify: gerçek timestamp freshness → gate re-check → belirsiz write için client order ID lookup → state contract → event journal.

Memory: `run/state.json` + `run/events.jsonl`.

Stop: `HALTED`, günlük drawdown `<= -5%`, saat `>= 19:20 Europe/Istanbul`, OAuth/MCP circuit-open veya kullanıcı flatten talebi.

Budget: endpoint pacing; read 429 için Retry-After/1s-2s-4s; tek turda en fazla bir exposure-increasing write.

Never: runner ile `/loop /desk` aynı anda çalışmaz; derivatives/transfer/withdraw yok; belirsiz write körlemesine tekrarlanmaz.
