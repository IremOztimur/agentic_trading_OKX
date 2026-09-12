# Regime Desk Progress

## Başlangıç

- KISS refactor başlatıldı.
- FastAPI/SQLite mimarisi kaldırılıyor.
- Canlı OKX write testleri `CANLI_TEST` onayı verilene kadar kapsam dışı.

## KISS refactor sonucu

- FastAPI, REST, SSE, SQLite ve backend engine kaldırıldı.
- Runtime `state.json` ve `events.jsonl` olarak sadeleştirildi.
- Deterministik RANGE/TREND/SHOCK engine eklendi; hysteresis, SHOCK önceliği, Smart Money veto ve spot-only aşağı trend davranışı test edildi.
- Risk-gate aynı script içinde hem check hem PreToolUse hook olarak çalışıyor.
- Watchdog yalnızca cancel, grid-stop ve agent-owned inventory azaltma aksiyonları üretebiliyor.
- Statik dashboard gerçek JSON projection üzerinden mobil tarayıcıda doğrulandı.
- 21 unit/contract testi, JavaScript syntax kontrolü, shell syntax kontrolü ve state validation geçiyor.
- Watchdog emergency aksiyonları da aynı risk-gate ve hook üzerinden geçiyor; flatten tarafında `side=buy` reddediliyor.
- Gerçek OKX MCP read preflight ve `CANLI_TEST` smoke testi henüz yapılmadı; ilgili feature'lar failing kalıyor.

## Canlı MCP preflight

- `simulatedTrading=false` ile account config, balance, BTC/ETH/SOL instrument ve ticker read çağrıları başarılı.
- OAuth bağlantısı doğru yarışma sub-account'ına yönlendirildi; `30 USDT` available ve yaklaşık `29.9922 USD` NAV doğrulandı.
- Açık spot emir ve aktif grid bulunmuyor.
- Hiçbir canlı write çağrısı yapılmadı.

## Event-driven runner refactor

- Mimari niyeti `intent.md`, kabul sırası `implementation_plan.md` içinde sabitlendi.
- Claude `/loop` otomasyon yolundan çıkarıldı; `/desk` salt-okunur inspect/explain komutuna indirildi.
- Tek-process `runner.py`, ayrı `control.py`, OAuth destekli `mcp_client.py` ve yerel feature motoru eklendi.
- 10s heartbeat, 15s hızlı market, 30s account, 2m regime ve 5m context cadence tanımlandı.
- MCP endpoint pacing, read 429 backoff ve belirsiz write sonrası lookup/no-blind-retry eklendi.
- Claude çıktısı APPROVE/VETO ile sınırlandı; tool, input ve notional sahipliği runner/gate katmanına taşındı.
- Risk gate'e trade-ready, timestamp freshness, 19:20 safe-close ve agent-owned inventory sınırı eklendi.
- 29 unit/contract/replay testi geçiyor.
- Runner'ın ayrı OAuth oturumu, gerçek custom MCP preflight ve 30 dakikalık DRY_RUN soak henüz tamamlanmadı.
- İlk runner OAuth denemesi sessiz transport nedeniyle kullanıcı yetkilendirmesi tamamlanmadan initialize timeout oldu; hiçbir MCP read/write yapılmadı. Terminalde URL gösteren üç dakikalık auth akışı hazırlandı.
