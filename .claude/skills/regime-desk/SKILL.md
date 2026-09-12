---
name: regime-desk
description: OKX MCP ile risk-gated spot desk turu, durum komutları ve watchdog kontrolü yürütür.
---

# Regime Desk

## Başlangıç

1. `progress.md`, `feature_list.json`, `loops/spot-desk.md` ve `run/state.json` dosyalarını oku.
2. State kontratını `python3 scripts/journal.py validate` ile doğrula.
3. Talep `once`, `watchdog`, `live CANLI`, `pause`, `resume` veya `flatten FLATTEN` değilse işlem yapma.

## `once`

1. OKX MCP üzerinden account balance, BTC/ETH/SOL ticker, candles, order book ve gerekli bağlam verilerini oku. Tool şemasındaki `simulatedTrading` gibi required alanları açıkça sağla.
2. Her MCP çağrısını süre, başarı ve redakte edilmiş input ile journal'a yaz.
3. Normalize edilmiş ham market verisini `observation_symbols` alanına koyan patch'i `/tmp/regime-desk-update.json` altında hazırla ve `python3 scripts/journal.py merge /tmp/regime-desk-update.json` ile uygula. Önceki sınıflandırılmış `symbols` alanını değiştirme; hysteresis bunu kullanır.
4. `python3 scripts/regime.py` çalıştır. Bu script RANGE/TREND hysteresis, anlık SHOCK önceliği, tek-grid seçimi ve spot-only aşağı trend davranışını uygular.
5. Claude deterministik sonucu ve kanıtları Türkçe olarak açıklayabilir; regime, action veya sayısal özellikleri değiştiremez. Güncel MCP write aracını ve argümanlarını proposal içindeki `mcp_call` alanına ekler.
6. `python3 scripts/risk_gate.py check` çalıştır.
7. Gate `ALLOW` değilse write çağrısı yapma.
8. `DRY_RUN` modunda gerçek write yapma; execution durumunu `SIMULATED` olarak journal'a yaz.
9. `LIVE` modunda yalnızca gate içindeki `approved_call` değerini MCP'ye gönder. Sonucu aynı client order ID ile sorgula ve doğrula.
10. Son state'i `python3 scripts/journal.py validate` ile doğrula.

## Durum komutları

- `live CANLI`: ancak bütün health alanları READY ve watchdog heartbeat tazeyse mode değerini LIVE yap.
- `pause`: mode değerini PAUSED yap; yeni risk açma.
- `resume`: mode değerini DRY_RUN yap. LIVE için yeniden `CANLI` gerekir.
- `flatten FLATTEN`: açık entry emirlerini iptal et, grid'i durdur, yalnızca agent-owned inventory'yi sat ve HALTED yap.

## Watchdog

`watchdog` komutunda `python3 scripts/watchdog.py once` çalıştır. Çıktıdaki her emergency action için güncel MCP aracını seç, 90 saniyelik proposal oluştur ve action alanını `CANCEL`, `STOP_GRID` veya `FLATTEN` yap. Sonra normal akıştaki `risk_gate.py check` ve PreToolUse hook üzerinden çağır. Ayrı bir bypass kullanma. Buy, create grid veya exposure artıran write çağrısı yapma.
