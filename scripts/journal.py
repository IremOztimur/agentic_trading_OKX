#!/usr/bin/env python3
"""Atomic state and append-only event journal for Regime Desk."""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = ROOT / "run"
STATE_PATH = RUN_DIR / "state.json"
EVENTS_PATH = RUN_DIR / "events.jsonl"
LOCK_PATH = RUN_DIR / ".lock"
DASHBOARD_PATH = ROOT / "static" / "data" / "dashboard.json"
SENSITIVE = ("token", "secret", "api_key", "apikey", "passphrase", "authorization", "password")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_state() -> dict[str, Any]:
    now = utc_now()
    return {
        "schema_version": 1,
        "updated_at": now,
        "session": {
            "id": datetime.now().strftime("%Y%m%d"),
            "mode": "DRY_RUN",
            "heartbeat_at": now,
            "starting_nav": None,
            "starting_inventory": {},
            "health": {"mcp": "UNKNOWN", "account": "UNKNOWN", "market": "UNKNOWN", "watchdog": "UNKNOWN", "trade_ready": False},
        },
        "account": {"nav": None, "available_usdt": None, "drawdown_pct": 0.0, "account_age_seconds": None, "total_exposure_usdt": 0.0, "positions": []},
        "observation_symbols": [],
        "symbols": [],
        "cycle": {
            "run_id": None,
            "proposal": {},
            "gate": {"verdict": "HOLD", "allowed_notional_usdt": 0.0, "reason_codes": ["NOT_EVALUATED"]},
            "execution": {"status": "NOT_SENT", "tool": None, "client_order_id": None, "order_id": None},
        },
        "watchdog": {"status": "UNKNOWN", "last_check_at": None, "last_action": None},
        "recent_events": [],
    }


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("<redacted>" if any(term in key.lower() for term in SENSITIVE) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@contextmanager
def locked():
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.touch(exist_ok=True)
    with LOCK_PATH.open("r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return default_state()
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def validate_state(state: dict[str, Any]) -> None:
    required = {"schema_version", "updated_at", "session", "account", "observation_symbols", "symbols", "cycle", "watchdog", "recent_events"}
    missing = required - state.keys()
    if missing:
        raise ValueError(f"state alanları eksik: {sorted(missing)}")
    if state["schema_version"] != 1:
        raise ValueError("desteklenmeyen schema_version")
    if state["session"].get("mode") not in {"DRY_RUN", "LIVE", "PAUSED", "DEGRADED", "HALTED", "DISCONNECTED"}:
        raise ValueError("geçersiz session mode")
    if not isinstance(state["symbols"], list) or not isinstance(state["observation_symbols"], list) or not isinstance(state["recent_events"], list):
        raise ValueError("observation_symbols, symbols ve recent_events liste olmalı")


def deep_merge(target: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(target)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def save_state(state: dict[str, Any]) -> dict[str, Any]:
    clean = redact(state)
    clean["updated_at"] = utc_now()
    validate_state(clean)
    with locked():
        _atomic_write(STATE_PATH, clean)
        _atomic_write(DASHBOARD_PATH, clean)
    return clean


def merge_state(patch: dict[str, Any]) -> dict[str, Any]:
    with locked():
        state = deep_merge(load_state(), redact(patch))
        state["updated_at"] = utc_now()
        validate_state(state)
        _atomic_write(STATE_PATH, state)
        _atomic_write(DASHBOARD_PATH, state)
    return state


def append_event(event_type: str, level: str, message: str, data: dict[str, Any] | None = None, run_id: str | None = None) -> dict[str, Any]:
    event = redact({"ts": utc_now(), "run_id": run_id, "type": event_type, "level": level, "message": message, "data": data or {}})
    with locked():
        with EVENTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        state = load_state()
        state["recent_events"] = ([event] + state.get("recent_events", []))[:30]
        state["updated_at"] = utc_now()
        validate_state(state)
        _atomic_write(STATE_PATH, state)
        _atomic_write(DASHBOARD_PATH, state)
    return event


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("validate")
    merge = sub.add_parser("merge")
    merge.add_argument("file")
    event = sub.add_parser("event")
    event.add_argument("--type", required=True, choices=("MCP_READ", "DECISION", "GATE", "MCP_WRITE", "WATCHDOG", "SYSTEM"))
    event.add_argument("--level", default="INFO", choices=("INFO", "WARN", "ERROR"))
    event.add_argument("--message", required=True)
    event.add_argument("--run-id")
    event.add_argument("--data", default="{}")
    args = parser.parse_args()

    if args.command == "init":
        if not STATE_PATH.exists():
            save_state(default_state())
        else:
            save_state(deep_merge(default_state(), load_state()))
        EVENTS_PATH.touch(exist_ok=True)
    elif args.command == "validate":
        validate_state(load_state())
        print("state: valid")
    elif args.command == "merge":
        merge_state(json.loads(Path(args.file).read_text(encoding="utf-8")))
    elif args.command == "event":
        append_event(args.type, args.level, args.message, json.loads(args.data), args.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
