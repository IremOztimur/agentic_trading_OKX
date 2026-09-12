# Live MCP Smoke Test

Bu test otomatik test paketine dahil değildir.

1. Ayrı yarışma sub-account'ı, Read/Trade yetkisi ve başlangıç envanteri doğrulanır.
2. `python3 scripts/runner.py preflight` ile public market snapshot alınır; aday turunda `/desk candidate RUN_ID` custom MCP account/context read doğrular.
3. `trade_ready=true` ve bütün health alanları `READY` olmadan devam edilmez.
4. Kullanıcı açıkça `CANLI_TEST` yazar.
5. Test notional değeri `min(10 USDT, NAV * 0.001)` olur. OKX minimumu bunu aşıyorsa test `SKIPPED` kalır.
6. Minimum spot emir gönderilir, order ID ile sorgulanır ve agent-owned inventory geri kapatılır.
7. Grid minimumu sınırı aşmıyorsa create → inspect → stop denenir; aksi halde `SKIPPED` kalır.
8. Bütün sonuçlar `run/events.jsonl` içine kaydedilir.
