# Spot Desk Loop

Trigger: `/loop 2m /desk once`

Safety trigger: ayrı Claude Code oturumunda `/loop 10s /desk watchdog`

Her tur: state oku → OKX MCP read → observation state'e yaz → `regime.py` ile RANGE/TREND/SHOCK + proposal → deterministic gate → gerekiyorsa write → order lookup → journal → validate.

Durma koşulları: `PAUSED`, `HALTED`, günlük drawdown `<= -5%` veya saat `>= 19:20 Europe/Istanbul`.

Sınırlar: spot-only; BTC-USDT, ETH-USDT, SOL-USDT; tek turda en fazla bir yeni risk aksiyonu.
