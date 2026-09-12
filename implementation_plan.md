# Regime Desk Implementation Plan

## 1. Runtime contract

- [x] Ürün niyetini `intent.md` ile sabitle.
- [x] Runner-owned timestamp alanlarını state kontratına ekle.
- [x] Tek runner sahipliğini process lock ile garanti et.
- [x] Dashboard projection ve event redaksiyonunu koru.

Kabul: ikinci runner başlamayı reddeder; heartbeat gerçek süreç yaşamını gösterir.

## 2. Data and custom MCP boundary

- [x] Sürekli teknik taramayı credential gerektirmeyen OKX public endpoint'lerine bağla.
- [x] Runner içindeki ikinci OAuth/MCP istemcisini kaldır.
- [x] Mevcut Claude custom MCP bağlantısını yalnızca candidate/emergency event'inde kullan.
- [x] Agent komutunu `*_get_*` read araçlarıyla sınırla.
- [x] Canlı para riske atmadan private bağlantıyı ölçmek için runner-owned `control.py preflight` kuyruğu ekle.

Kabul: public preflight credentials olmadan tamamlandı; candidate agent mevcut custom MCP üzerinden doğru sub-account'ı okur; token/secret loglanmaz.

## 3. Rate-limit policy

- [x] Endpoint sınıflarına minimum çağrı aralığı uygula.
- [x] Read çağrılarında 429 `Retry-After`, yoksa `1s/2s/4s` backoff uygula.
- [x] Public read 429 için kontrollü retry uygula.
- [x] Preflight sonrasında scheduler sayaçlarını güncelle; başlangıç çift çağrısını engelle.
- [x] Tek turda en fazla bir exposure-increasing write uygula.

Kabul: fake public-client testinde throttle ve 429 backoff doğrulanır.

## 4. Deterministic observation and regimes

- [x] 15 saniye ticker/book/trade snapshot'ı.
- [ ] Account/order/fill reconciliation yalnızca candidate ve açık pozisyon event'lerinde custom MCP üzerinden tamamlanır.
- [x] 2 dakika candles ve feature hesaplama.
- [x] Public OI 5 dakika; Smart Money/news yalnızca candidate anında custom MCP.
- [x] RANGE/TREND iki turluk hysteresis; SHOCK anlık öncelik.
- [x] Eksik Smart Money verisinde yeni riski fail-closed engelle.

Kabul: RANGE, TREND, SHOCK replay fixture'ları ve stale-provider testleri geçer.

## 5. Event-driven Claude checkpoint

- [x] Claude'u yalnızca actionable RANGE/TREND teknik adayında çağır.
- [x] Agent çıktısını `approve/context_risk/reason_codes/rationale_tr` ile sınırla.
- [x] Claude'un symbol, action ve notional değiştirmesine izin verme.
- [x] Timeout/bozuk JSON durumunda HOLD üret.
- [x] SHOCK aksiyonunda Claude'u bekleme.
- [x] Claude subprocess çalışırken heartbeat ve public market taramasını non-blocking sürdür.

Kabul: normal HOLD turunda sıfır agent çağrısı; aday turunda tam bir çağrı; geçersiz yanıtta sıfır write.

## 6. Risk gate and execution

- [x] `trade_ready`, mode, timestamp freshness ve 19:20 kontrolünü tamamla.
- [x] `%0.35` risk, `%25` coin, `%50` total, `-%3/-5` drawdown kurallarını koru.
- [x] Instrument `minSz/lotSz/tickSz` normalizasyonu ekle.
- [ ] LIVE için semantic action'ı exact allowlisted custom MCP çağrısına dönüştür.
- [x] Write öncesi gate'i tekrar değerlendir.
- [ ] LIVE write sonucunu order ID ile doğrula.

Kabul: LLM çıktısı tek başına write üretemez; gate dışı her çağrı reddedilir.

## 7. Watchdog and controls

- [x] Watchdog'u runner heartbeat turuna dahil et.
- [x] Stale main cycle durumunda PAUSED + cancel/stop uygula.
- [ ] Hard drawdown/safe-close/FLATTEN talebinde custom MCP executor ile yalnızca agent-owned inventory'yi kapat.
- [x] `control.py` ile status/preflight/live/pause/resume/flatten komutlarını ayır.
- [x] `/desk` komutunu yalnızca manuel explain/inspect işine indir.

Kabul: watchdog hiçbir koşulda buy, yeni grid veya exposure artışı üretemez.

## 8. Operations and verification

- [x] `runner.py preflight`, `once`, `run` komutlarını belgele.
- [x] `init.sh` yalnızca dashboard/test sorumluluğunda kalsın.
- [x] Unit, replay, fake-public-client ve state contract testlerini çalıştır.
- [ ] Önce en az 30 dakika DRY_RUN gözle.
- [ ] Canlı write smoke testini yalnızca açık `CANLI_TEST` onayıyla yap.

Kabul: iki terminalle runner + dashboard çalışır; `/loop /desk` gerekmez.

## Teslim sırası

1. Rate-limited public market client ve runner scheduler
2. Event-driven Claude + existing custom MCP read contract
3. Deterministic execution mapper ve risk gate
4. User-approved custom MCP LIVE executor
5. Testler, dokümantasyon ve DRY_RUN
6. Kullanıcı onaylı live smoke
