---
description: Regime Desk'in son kararını salt-okunur incele ve açıkla
argument-hint: inspect | explain
allowed-tools: Bash(python3 scripts/control.py status), Read
---

`$ARGUMENTS` isteğinde `run/state.json`, son `run/events.jsonl` kayıtları ve `intent.md` üzerinden mevcut durumu incele. Bu komut otomasyon, mode değişikliği veya MCP write yapmaz. Gerçek loop'un sahibi `scripts/runner.py` sürecidir.
