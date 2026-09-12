# Regime Desk Implementation Plan

## 1. Runtime contract

- [x] Ürün niyetini `intent.md` ile sabitle.
- [x] Runner-owned timestamp alanlarını state kontratına ekle.
- [x] Tek runner sahipliğini process lock ile garanti et.
- [x] Dashboard projection ve event redaksiyonunu koru.

Kabul: ikinci runner başlamayı reddeder; heartbeat gerçek süreç yaşamını gösterir.

## 2. Custom MCP transport

- [x] Aynı OKX hosted MCP endpoint'ine OAuth destekli minimal istemci bağla.
- [x] `tools/list` yalnızca başlangıçta çalışsın.
- [x] Exact read/write allowlist oluştur; türev, transfer, withdraw ve earn tool'larını reddet.
- [x] OAuth preflight'i normal trading turundan ayır.

Kabul: `authorize` ve `preflight` gerçek sub-account'ta read-only tamamlanır; token/secret loglanmaz.

## 3. Rate-limit policy

- [x] Endpoint sınıflarına minimum çağrı aralığı uygula.
- [x] Read çağrılarında 429 `Retry-After`, yoksa `1s/2s/4s` backoff uygula.
- [x] Write timeout/429 sonrasında kör retry yapma; önce `client order ID` ile sorgula.
- [x] Preflight sonrasında scheduler sayaçlarını güncelle; başlangıç çift çağrısını engelle.
- [x] Tek turda en fazla bir exposure-increasing write uygula.

Kabul: fake MCP testinde throttle, 429 backoff ve duplicate-write engeli doğrulanır.

## 4. Deterministic observation and regimes

- [x] 15 saniye ticker/book/trade snapshot'ı.
- [x] 30 saniye account/order/fill reconciliation.
- [x] 2 dakika candles ve feature hesaplama.
- [x] 5 dakika OI/Smart Money/news bağlamı.
- [x] RANGE/TREND iki turluk hysteresis; SHOCK anlık öncelik.
- [x] Eksik Smart Money verisinde yeni riski fail-closed engelle.

Kabul: RANGE, TREND, SHOCK replay fixture'ları ve stale-provider testleri geçer.

## 5. Event-driven Claude checkpoint

- [x] Claude'u yalnızca actionable RANGE/TREND teknik adayında çağır.
- [x] Agent çıktısını `approve/context_risk/reason_codes/rationale_tr` ile sınırla.
- [x] Claude'un symbol, action, notional, tool veya order input değiştirmesine izin verme.
- [x] Timeout/bozuk JSON durumunda HOLD üret.
- [x] SHOCK aksiyonunda Claude'u bekleme.

Kabul: normal HOLD turunda sıfır agent çağrısı; aday turunda tam bir çağrı; geçersiz yanıtta sıfır write.

## 6. Risk gate and execution

- [x] `trade_ready`, mode, timestamp freshness ve 19:20 kontrolünü tamamla.
- [x] `%0.35` risk, `%25` coin, `%50` total, `-%3/-5` drawdown kurallarını koru.
- [x] Instrument `minSz/lotSz/tickSz` normalizasyonu ekle.
- [x] Semantic action'ı runner içinde allowlisted MCP çağrısına dönüştür.
- [x] Write öncesi gate'i tekrar değerlendir.
- [x] Order ID ile sonucu doğrula.

Kabul: LLM çıktısı tek başına write üretemez; gate dışı her çağrı reddedilir.

## 7. Watchdog and controls

- [x] Watchdog'u runner heartbeat turuna dahil et.
- [x] Stale main cycle durumunda PAUSED + cancel/stop uygula.
- [x] Hard drawdown/safe-close/FLATTEN talebinde yalnızca agent-owned inventory'yi kapat.
- [x] `control.py` ile status/live/pause/resume/flatten komutlarını ayır.
- [x] `/desk` komutunu yalnızca manuel explain/inspect işine indir.

Kabul: watchdog hiçbir koşulda buy, yeni grid veya exposure artışı üretemez.

## 8. Operations and verification

- [x] `runner.py authorize`, `preflight`, `once`, `run` komutlarını belgele.
- [x] `init.sh` yalnızca dashboard/test sorumluluğunda kalsın.
- [x] Unit, replay, fake-MCP ve state contract testlerini çalıştır.
- [ ] Önce en az 30 dakika DRY_RUN gözle.
- [ ] Canlı write smoke testini yalnızca açık `CANLI_TEST` onayıyla yap.

Kabul: iki terminalle runner + dashboard çalışır; `/loop /desk` gerekmez.

## Teslim sırası

1. Rate-limited MCP client ve runner scheduler
2. Event-driven agent contract
3. Deterministic execution mapper ve risk gate
4. Watchdog emergency execution
5. Testler, dokümantasyon ve DRY_RUN
6. Kullanıcı onaylı live smoke
