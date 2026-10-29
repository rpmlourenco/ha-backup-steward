"""One app-owned calendar, DST-safe per-date deduplication and checksum catch-up."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def checksum_due(now, cfg, last_success):
    local = now.astimezone(ZoneInfo(cfg["timezone"]))
    frequency = cfg["checksum_frequency"]
    if frequency == "disabled":
        return False
    if frequency == "weekly":
        days = (local.weekday() - cfg["checksum_weekday"]) % 7
        target = local.date() - timedelta(days=days)
    else:
        first = local.date().replace(day=1)
        target = first + timedelta(days=(cfg["checksum_weekday"] - first.weekday()) % 7)
        target += timedelta(weeks=cfg["checksum_month_week"] - 1)
        if local.date() < target:
            return False
    if not last_success:
        return True
    last = datetime.fromisoformat(last_success).astimezone(ZoneInfo(cfg["timezone"]))
    return last.date() < target


def due(now, cfg, state):
    if not cfg["schedule_enabled"]:
        return False
    not_before = state.get("schedule_not_before")
    if not_before and now < datetime.fromisoformat(not_before):
        return False
    local = now.astimezone(ZoneInfo(cfg["timezone"]))
    if local.strftime("%H:%M") < cfg["schedule_time"]:
        return False
    return state.get("last_schedule_date") != local.date().isoformat()


def next_run(now, cfg):
    if not cfg["schedule_enabled"]:
        return None
    zone = ZoneInfo(cfg["timezone"])
    local = now.astimezone(zone)
    hour, minute = map(int, cfg["schedule_time"].split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate = (local + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate.astimezone(timezone.utc).isoformat()
