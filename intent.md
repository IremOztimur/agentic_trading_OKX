# Regime Desk Intent

## Ürün amacı

Regime Desk, BTC-USDT, ETH-USDT ve SOL-USDT spot piyasasını RANGE, TREND ve SHOCK olarak sınıflandıran; yalnızca doğrulanmış fırsatlarda risk alan; her kararın kanıtını, veto nedenini ve execution sonucunu görünür tutan otonom bir OKX spot desk'tir.

## Temel mimari kararı

Gerçek loop'un sahibi Claude değildir. Tek yerel `runner.py` süreci trigger, work, verify, memory ve stop döngüsünü yürütür. Claude yalnızca deterministik motor actionable bir aday ürettiğinde bağlamsal karar checkpoint'i olarak çağrılır.

```text
OKX hosted custom MCP
        ↓
minimal runner
        ↓
normalize + deterministic features
        ↓
RANGE / TREND / SHOCK + hysteresis
        ├─ HOLD → log; Claude çağrılmaz
        ├─ SHOCK → deterministic risk reduction; Claude beklenmez
        └─ actionable RANGE/TREND candidate
                     ↓
               Claude checkpoint
               APPROVE veya VETO
                     ↓
              deterministic risk gate
                     ↓
            DRY_RUN simulate / LIVE write
                     ↓
              order lookup + audit
```

## Deterministik katmanın sahipliği

Runner ve Python kuralları şunların tek sahibidir:

- MCP polling, endpoint throttling ve 429 backoff
- Ticker, candles, book, trades, account, order ve fill mutabakatı
- ATR, ADX, EMA, VWAP, volume z-score ve order-flow hesapları
- RANGE/TREND/SHOCK sınıflandırması ve iki turluk hysteresis
- Smart Money kesin vetosu ve stale-provider davranışı
- Pozisyon boyutlama, exposure ve drawdown limitleri
- Heartbeat, watchdog, 19:20 güvenli kapanış ve duplicate engeli
- Allowlist tool seçimi, MCP write argümanları ve order doğrulaması
- `run/state.json` ile `run/events.jsonl` belleği

## Claude'un dar yetkisi

Claude:

- Yalnızca actionable RANGE/TREND adayında çağrılır.
- Haber ve Smart Money bağlamını teknik adayla birlikte yorumlar.
- `APPROVE` veya `VETO` ile kısa Türkçe gerekçe üretir.
- Sembolü, aksiyonu veya risk miktarını büyütemez.
- MCP tool seçemez ve order argümanı üretemez.
- Teknik aday yokken işlem başlatamaz.
- Deterministik vetoyu kaldıramaz.
- Timeout, bozuk JSON veya erişim hatasında fail-closed `HOLD` üretir.

Önerilen agent çıktısı:

```json
{
  "candidate_id": "unique-id",
  "approve": false,
  "context_risk": "HIGH",
  "reason_codes": ["BEARISH_SMART_MONEY"],
  "rationale_tr": "Teknik breakout oluştu ancak Smart Money ters yönde."
}
```

## Güvenlik değişmezleri

- Spot-only; short ve türev write yoktur.
- Withdraw, transfer, earn ve leverage write yasaktır.
- İşlem başına planlanan kayıp en fazla `%0.35 NAV`.
- Coin exposure en fazla `%25 NAV`; toplam exposure en fazla `%50 NAV`.
- Günlük `-%3` drawdown yeni riski durdurur.
- Günlük `-%5` drawdown ve 19:20 Europe/Istanbul güvenli kapanışı HALT üretir.
- SHOCK müdahalesi Claude'u beklemez.
- Belirsiz write sonucu aynı `client order ID` sorgulanmadan tekrarlanmaz.
- Smart Money eski veya yoksa yeni pozisyon açılmaz.
- Runner çalışırken ayrı bir `/loop /desk` trading döngüsü çalıştırılmaz.

## Loop engineering sözleşmesi

```text
Trigger:
  heartbeat/watchdog 10s
  ticker/book/trades 15s
  account/orders/fills 30s
  regime decision 2m
  OI/Smart Money/news 5m

Work:
  MCP read → normalize → features → regime
  → actionable candidate ise Claude → risk gate → execution

Verify:
  freshness → gate → order lookup → state contract

Memory:
  run/state.json + run/events.jsonl

Stop:
  HALTED, hard drawdown, safe-close, stale heartbeat veya MCP circuit-open
```

Claude `/loop` komutu bu mimarinin parçası değildir. `/desk` yalnızca manuel inceleme ve demo açıklaması için kalır.

## Operasyon modeli

```bash
# Terminal 1: gerçek otomasyon
python3 scripts/runner.py run

# Terminal 2: salt-okunur dashboard
./init.sh
```

Mode değişiklikleri ayrı kontrol düzlemidir:

```bash
python3 scripts/control.py status
python3 scripts/control.py live CANLI
python3 scripts/control.py pause
python3 scripts/control.py resume
python3 scripts/control.py flatten FLATTEN
```

## Neden agent kullanıyoruz?

Agent sürekli polling veya matematik için değil, yapılandırılmamış ve çelişkili bağlamı değerlendirmek için kullanılır. Ürünün agentic değeri kontrollü araç kullanımı, bağlamsal veto, açıklanabilir karar ve geri bildirimli yürütmedir. Güvenlik kritik yolunda LLM bulunmaz.
