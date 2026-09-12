---
description: Regime Desk adayını bağlı custom MCP read araçlarıyla değerlendir
argument-hint: inspect | explain | preflight | candidate RUN_ID | emergency REASON
allowed-tools: Read, mcp__claude_ai_okx-agent-trade-kit__*_get_*
---

Gerçek loop'un sahibi `scripts/runner.py` sürecidir.

- `inspect|explain`: state ve event logunu salt-okunur açıkla.
- `preflight`: bağlı custom MCP ile sub-account config, balance, açık spot emirler, aktif grid ve son fill'leri salt-okunur doğrula.
- `candidate RUN_ID`: state'teki run ID birebir eşleşmiyorsa veto et. Bağlı custom MCP ile canlı sub-account balance, güncel ticker, Smart Money ve news/context verisini oku. Teknik aday veya gerekli bağlam eksik/stale/ters ise veto et.
- `emergency REASON`: custom MCP ile açık spot emir, grid ve agent-owned inventory durumunu salt-okunur değerlendir; risk azaltma önerisini döndür.

Her durumda markdown veya code fence olmadan yalnızca JSON object döndür. Bash kullanma, dosya değiştirme ve MCP write yapma. Beklenen alanlar: `candidate_id`, `approve`, `context_risk`, `reason_codes`, `rationale_tr`, `smart_money_veto`, `account` ve `freshness`. `account` içinde sayısal `nav`, `available_usdt`, `total_exposure_usdt` ile `positions`, `open_orders`, `recent_fills` listeleri bulunmalı.
