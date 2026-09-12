"""Shared wall-clock session policy for control, gate, and watchdog."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


DESK_TIMEZONE = ZoneInfo("Europe/Istanbul")
SAFE_CLOSE_HOUR = 19
SAFE_CLOSE_MINUTE = 20


def local_time(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(DESK_TIMEZONE)


def after_safe_close(now: datetime | None = None) -> bool:
    local = local_time(now)
    return (local.hour, local.minute) >= (SAFE_CLOSE_HOUR, SAFE_CLOSE_MINUTE)


def safe_close_active(session: dict, now: datetime | None = None) -> bool:
    local = local_time(now)
    override_date = session.get("safe_close_override_date")
    return after_safe_close(now) and override_date != local.date().isoformat()


def live_override_date(now: datetime | None = None) -> str | None:
    local = local_time(now)
    return local.date().isoformat() if after_safe_close(now) else None
