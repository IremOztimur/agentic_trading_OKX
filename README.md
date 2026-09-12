# Regime Desk

Regime Desk, OKX Agentic Trading Hackathon için açıklanabilir ve risk-gated bir spot trading ajanıdır. `scripts/regime.py` piyasayı `RANGE`, `TREND` veya `SHOCK` olarak sınıflandırır. Claude Code kanıtları açıklar ve güncel OKX MCP aracını seçer; `scripts/risk_gate.py` değişmez risk sınırlarını uygular.

```text
Claude /desk → OKX MCP read → state.json → regime.py → risk_gate.py
                                                     ├─ HOLD/HALT → log
                                                     └─ ALLOW → OKX MCP write → log
```

## Çalıştırma

```bash
./init.sh
```

Dashboard: [http://127.0.0.1:8765](http://127.0.0.1:8765)

Claude Code: `/desk once` veya `/loop 2m /desk once`. Watchdog için ayrı oturumda `/loop 10s /desk watchdog`.

`run/state.json` tek güncel state, `run/events.jsonl` tek geçmiş kaydıdır. Panel `static/data/dashboard.json` içindeki redakte edilmiş yansımayı okur. Gerçek account snapshot alınmadan demo NAV veya position gösterilmez.

## Test

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Canlı write testi otomatik pakette değildir. `tests/live_smoke.md` açık `CANLI_TEST` onayı ister ve `min(10 USDT, NAV × %0.1)` sınırını aşmaz.

Sabit limitler: işlem riski `%0.35 NAV`, coin `%25`, toplam `%50`, soft drawdown `−%3`, hard halt `−%5`, safe close `19:20 Europe/Istanbul`.

Kaynaklar: [OKX MCP](https://www.okx.com/docs-v5/agent_en/#mcp), [OKX TR Skills](https://tr.okx.com/agent-tradekit/skills), [Anthropic agent harness](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

Yarışma/eğitim amaçlıdır; yatırım tavsiyesi değildir. LIVE yalnızca ayrılmış yarışma sub-account'ında kullanılmalıdır.

