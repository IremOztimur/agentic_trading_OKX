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
