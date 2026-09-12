# Regime Desk Intent

## Ürün amacı

Regime Desk, BTC-USDT, ETH-USDT ve SOL-USDT spot piyasasını TREND, RANGE ve SHOCK olarak
sınıflandıran; yalnızca doğrulanmış conviction'da risk alan; her kararın kanıtını, veto nedenini
ve execution sonucunu görünür tutan otonom bir OKX spot desk'tir.

## Temel mimari kararı

**Yavaş AI muhakemesi ile hızlı deterministik execution ayrılmıştır.** LLM trading path'inde
değildir. Tek yerel `runner.py` süreci algı, karar, risk ve execution döngüsünü milisaniyelerle
yürütür. LLM yalnızca control plane'dedir: kararı sonradan, insan diliyle açıklar.

Önceki sürümde her actionable adayda headless Claude çağrılıyor ve 30–180 saniye bekleniyordu;
aday cevap dönmeden bayatlıyordu. O checkpoint mimarisi tamamen kaldırıldı.

```text
        AI HQ (Telegram)                 ← control plane
              ↕  status · why
   ─────────────────────────────────────────────────────
OKX ATK MCP (local stdio, scripts/atk.py) ← execution plane
        ↓
perception (5 sensör, skor + freshness)
        ↓
regime (TREND / RANGE / SHOCK + hysteresis) → conviction
        ↓
playbook → BUY / REDUCE / HOLD
        ↓
deterministic risk gate
        ↓
DRY_RUN simulate / LIVE spot_place_order + order lookup
```

## Deterministik katmanın sahipliği

Runner ve Python kuralları şunların tek sahibidir:

- ATK MCP bağlantısı, cadence tier'ları ve fail-closed yeniden başlatma
- Beş sensörün normalizasyonu, ağırlığı ve TTL'i
- Freshness çürümesi, sensor quorum ve spread vetosu
- TREND/RANGE/SHOCK sınıflandırması, 0.10 margin ve iki turluk hysteresis
- Conviction hesabı, playbook eşiği ve size multiplier
- Pozisyon boyutlama, exposure, drawdown ve safe-close limitleri
- Heartbeat, watchdog, duplicate engeli ve client order ID idempotency'si
- SHOCK'un olay olarak latch'lenmesi ve 5 dakikalık cooldown
- `run/state.json` ile `run/events.jsonl` belleği

## LLM'in dar yetkisi

`scripts/hq.py` içindeki Telegram HQ:

- Yalnızca `status` ve `why <SYMBOL>` sorularını yanıtlar.
- Yanıtı `cycle.contributions` ve state'ten üretilen fact bloğundan kurar.
- Fact bloğunda olmayan hiçbir sayıyı, sembolü veya aksiyonu üretemez.
- Emir gönderemez, config yazamaz, risk limiti veya mode değiştiremez.
- Model erişilemezse deterministik template yanıta düşer.
- Yalnızca `TELEGRAM_CHAT_ID` ile eşleşen chat'e cevap verir.

## Güvenlik değişmezleri

- Spot-only; short ve türev write yoktur.
- Withdraw, transfer, earn ve leverage write yasaktır.
- İşlem başına planlanan kayıp en fazla `%0.35 NAV`.
- Coin exposure en fazla `%25 NAV`; toplam exposure en fazla `%50 NAV`.
- Günlük `-%3` drawdown yeni riski durdurur; `-%5` ve 19:20 Europe/Istanbul HALT üretir.
- LIVE emirleri ayrıca `min(10 USDT, NAV × %5)` ile sınırlıdır.
- Belirsiz write sonucu aynı `client order ID` sorgulanmadan tekrarlanmaz.
- Sensor quorum `%60` altına düşerse yeni pozisyon açılmaz.
- Smart Money verisi stale ise yeni pozisyon açılmaz.
- RANGE açık biçimde işlem yapılmayan durumdur; boşluğu doldurmak için ürün icat edilmez.

## Loop engineering sözleşmesi

```text
Trigger:
  heartbeat 5s
  fast tier (orderbook, trades, ticker) 5s
  mid tier (candles, indicators) 30s
  slow tier (smart money) 120s
  account 20s
  decision 15s
  SHOCK: edge-triggered, epizod başına bir kez, 5m cooldown

Work:
  ATK MCP read → sensör skorları → cache → regime + conviction
  → playbook → risk gate → DRY_RUN simulate veya LIVE write

Verify:
  freshness → quorum → gate → order lookup → state contract

Memory:
  run/state.json + run/events.jsonl

Stop:
  HALTED, hard drawdown, safe-close, stale heartbeat veya ATK circuit-open
```

## Neden agent kullanıyoruz?

Agent, saniyelik alım-satım kararı için değil; sistemin ne yaptığını insan diliyle açıklayabilmesi
için kullanılır. Ürünün agentic değeri kontrollü araç kullanımı, açıklanabilir deterministik karar
ve konuşulabilir bir operatör arayüzüdür. Güvenlik kritik yolunda LLM bulunmaz.
