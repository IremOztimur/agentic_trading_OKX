# Regime Desk

Regime Desk, OKX Agentic Trading Hackathon için açıklanabilir ve risk-gated bir spot trading ajanıdır. `scripts/runner.py` gerçek loop'un sahibidir; `scripts/regime.py` piyasayı `RANGE`, `TREND` veya `SHOCK` olarak sınıflandırır. Claude yalnızca actionable RANGE/TREND adayında APPROVE/VETO checkpoint'i olarak çağrılır.

```text
runner → OKX public market API → features → regime.py
                                  ├─ HOLD → log
                                  ├─ SHOCK → deterministic risk reduction
                                  └─ candidate → Claude + existing custom MCP → risk_gate.py
```

## Çalıştırma

Terminal 1, otomasyon:

```bash
python3 scripts/runner.py preflight
python3 scripts/runner.py run
```

Terminal 2, dashboard:

```bash
./init.sh
```

Dashboard: [http://127.0.0.1:8765](http://127.0.0.1:8765)

`/loop /desk` kullanılmaz. Runner normal turlarda OKX public endpoint'lerini kullanır. `/desk candidate` yalnızca teknik aday oluştuğunda mevcut Claude custom MCP bağlantısını kullanır; ikinci OAuth veya API credential yoktur.

Kontrol komutları:

```bash
python3 scripts/control.py status
python3 scripts/control.py preflight
python3 scripts/control.py live CANLI
python3 scripts/control.py pause
python3 scripts/control.py resume
python3 scripts/control.py flatten FLATTEN
```

`run/state.json` tek güncel state, `run/events.jsonl` tek geçmiş kaydıdır. Panel `static/data/dashboard.json` içindeki redakte edilmiş yansımayı okur. Gerçek account snapshot alınmadan demo NAV veya position gösterilmez.

`control.py preflight`, çalışan runner'a salt-okunur bir custom MCP account kontrolü kuyruğa alır. Bu komut emir göndermez. Runner kodu güncellendiyse önce çalışan runner durdurulup yeniden başlatılmalıdır.

## Test

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Canlı write testi otomatik pakette değildir. `tests/live_smoke.md` açık `CANLI_TEST` onayı ister ve `min(10 USDT, NAV × %0.1)` sınırını aşmaz.

Sabit limitler: işlem riski `%0.35 NAV`, coin `%25`, toplam `%50`, soft drawdown `−%3`, hard halt `−%5`, safe close `19:20 Europe/Istanbul`. Public read 429 çağrıları Retry-After veya `1s/2s/4s` ile yeniden denenir.

LIVE executor henüz etkin değildir. Custom MCP write araçları yalnızca exact tool şemaları doğrulandıktan ve kullanıcı `CANLI_TEST` onayı verdikten sonra açılır.

Mimari niyet: [`intent.md`](intent.md). Uygulama ve kabul planı: [`implementation_plan.md`](implementation_plan.md).

Kaynaklar: [OKX MCP](https://www.okx.com/docs-v5/agent_en/#mcp), [OKX TR Skills](https://tr.okx.com/agent-tradekit/skills), [Anthropic agent harness](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents).

Yarışma/eğitim amaçlıdır; yatırım tavsiyesi değildir. LIVE yalnızca ayrılmış yarışma sub-account'ında kullanılmalıdır.
