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
- Tek-process `runner.py`, ayrı `control.py`, rate-limited `okx_public.py` ve yerel feature motoru eklendi.
- 10s heartbeat, 15s hızlı market, 30s account, 2m regime ve 5m context cadence tanımlandı.
- Public endpoint pacing ve read 429 backoff eklendi.
- Claude yalnızca candidate/emergency event'inde mevcut custom MCP read araçlarıyla çağrılacak şekilde sınırlandı.
- Risk gate'e trade-ready, timestamp freshness, 19:20 safe-close ve agent-owned inventory sınırı eklendi.
- 29 unit/contract/replay testi geçiyor.
- Runner'ın ayrı OAuth yaklaşımı kaldırıldı; mevcut Claude custom MCP bağlantısı tek private bağlantı olarak kaldı.
- Credential gerektirmeyen gerçek OKX public runner preflight tamamlandı; BTC/ETH/SOL instrument, ticker, book, trades, candles ve OI okundu.
- Runtime `DRY_RUN`; public market ve watchdog `READY`; custom MCP `ON_DEMAND`. Account snapshot aday anına kadar bilinçli olarak `STALE`, dolayısıyla `trade_ready=false`.
- Eski ikinci-OAuth denemesinden kalan `DISCONNECTED/MCP ERROR` state'i temizlendi; non-standard `Infinity` değeri `null` migrasyonuyla giderildi.
- Smart Money custom MCP ile doğrulanana kadar veto artık kesin olarak `true`; yeni risk fail-closed.
- 36 unit/contract/replay testi ve strict JSON doğrulaması geçiyor.
- Event-triggered gerçek candidate çağrısı ve 30 dakikalık DRY_RUN soak henüz tamamlanmadı.

## Private preflight aşaması

- `control.py preflight` komutu eklendi; istek state üzerinden çalışan runner'a iletiliyor.
- Runner bu istekte Claude'u yalnızca bağlı custom MCP read araçlarıyla çağırıyor ve account freshness'i state'e yansıtıyor.
- Smart Money verisi yokken gösterilen `VETO`, dashboard'da `BEKLENİYOR`; teknik aday yokken `NOT CHECKED` olarak ayrıştırıldı.
- Her yeni proposal gate/run kimliği ve expiry alanlarını sıfırlıyor; eski gate zamanı yeni kararla karışmıyor.
- Runner-owned gerçek private preflight tamamlandı: 30 USDT, yaklaşık 29.9925 USD NAV, sıfır açık emir ve grid state'e yansıtıldı.

## Headless Claude/MCP teşhisi

- İlk hata custom MCP bağlantısı değil, headless `/desk` çağrısında `Skill` izninin bulunmaması ve Claude'un JSON'u markdown fence içinde döndürmesiydi.
- Read-only `Skill, Read, *_get_*` allowlist'iyle doğrudan preflight başarılı: 30 USDT, yaklaşık 29.9928 USD equity, sıfır açık spot emir, sıfır aktif grid ve sıfır recent fill doğrulandı.
- Runner varsayılan Claude komutuna explicit read-only allowlist ve fenced-JSON parser eklendi; stdout/stderr hata ayrıntıları audit'e ekleniyor.
- 1m ATR ile 5m return doğrudan karşılaştırılmasından doğan yanlış SHOCK, `sqrt(5)` zaman ölçeğiyle düzeltildi.
- DRY_RUN ve sıfır pozisyonda SHOCK artık Claude emergency çağrısı üretmiyor.
- Custom MCP üzerinde spot place/get/cancel ve instrument tool adları ile input şemaları write çağrısı yapılmadan keşfedildi.
- Claude görevi artık non-blocking alt süreçtir; agent çalışırken heartbeat, public market ve watchdog devam eder.
- 39 unit/contract/replay testi geçiyor.
