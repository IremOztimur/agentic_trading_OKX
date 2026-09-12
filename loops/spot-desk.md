# Spot Desk Loop

Trigger: `python3 scripts/runner.py run`

Cadence: heartbeat/watchdog 10s; ticker/book/trades 15s; regime 2m; public OI 5m. Private account, Smart Money ve news yalnızca aday olayında custom MCP ile okunur.

Work: OKX public read → normalize → deterministic features → RANGE/TREND/SHOCK → yalnızca actionable RANGE/TREND veya emergency olayında Claude → mevcut custom MCP account/context read → deterministic risk gate → DRY_RUN simulation. LIVE executor ayrıca doğrulanır.

Verify: gerçek timestamp freshness → gate re-check → belirsiz write için client order ID lookup → state contract → event journal.

Memory: `run/state.json` + `run/events.jsonl`.

Stop: `HALTED`, günlük drawdown `<= -5%`, saat `>= 19:20 Europe/Istanbul` veya kullanıcı flatten talebi. Aday anındaki custom MCP hatası yalnızca o adayı `HOLD` yapar.

Budget: endpoint pacing; read 429 için Retry-After/1s-2s-4s; tek turda en fazla bir exposure-increasing write.

Never: runner ile `/loop /desk` aynı anda çalışmaz; derivatives/transfer/withdraw yok; belirsiz write körlemesine tekrarlanmaz.
