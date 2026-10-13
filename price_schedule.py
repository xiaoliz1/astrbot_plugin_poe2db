"""Hourly PoE2 price collection and configurable notification schedules."""

from __future__ import annotations

import math
import re
from datetime import datetime, time, timedelta, timezone, tzinfo

BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
DEFAULT_REFRESH_TIMES = (time(0, 0), time(6, 0), time(12, 0), time(18, 0))


def parse_daily_refresh_times(value: object) -> tuple[time, ...]:
    """Parse a comma/newline-separated list of HH:MM refresh slots."""
    if isinstance(value, (list, tuple)):
        raw_values = list(value)
    elif isinstance(value, str):
        raw_values = [
            item.strip()
            for item in re.split(r"[\s,，;；]+", value)
            if item.strip()
        ]
    else:
        raw_values = []

    parsed: set[time] = set()
    for raw in raw_values:
        if isinstance(raw, time):
            parsed.add(time(raw.hour, raw.minute))
            continue
        raw = str(raw).strip()
        if raw == "24:00":
            raw = "00:00"
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", raw):
            continue
        hour, minute = (int(part) for part in raw.split(":"))
        parsed.add(time(hour, minute))

    return tuple(sorted(parsed)) if parsed else DEFAULT_REFRESH_TIMES


def normalize_utc_offset_hours(value: object, default: float = 8.0) -> float:
    """Return a finite UTC offset bounded to the supported -12..+14 range."""
    try:
        offset = float(value)
    except (TypeError, ValueError):
        offset = default
    if not math.isfinite(offset):
        offset = default
    return max(-12.0, min(14.0, offset))


def timezone_for_utc_offset(value: object = 8.0) -> tzinfo:
    offset = normalize_utc_offset_hours(value)
    minutes = round(offset * 60)
    sign = "+" if minutes >= 0 else "-"
    absolute_minutes = abs(minutes)
    name = f"UTC{sign}{absolute_minutes // 60:02d}:{absolute_minutes % 60:02d}"
    return timezone(timedelta(minutes=minutes), name=name)


def next_price_refresh_at(
    now: datetime | None = None,
    times: object = DEFAULT_REFRESH_TIMES,
    utc_offset_hours: object = 8.0,
) -> datetime:
    """Return the next configured notification slot in the configured offset."""
    target_timezone = timezone_for_utc_offset(utc_offset_hours)
    current = now or datetime.now(target_timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=target_timezone)
    current = current.astimezone(target_timezone)

    slots = parse_daily_refresh_times(times)
    for day_offset in range(2):
        date = current.date() + timedelta(days=day_offset)
        for slot in slots:
            candidate = datetime.combine(date, slot, tzinfo=target_timezone)
            if candidate > current:
                return candidate

    raise RuntimeError("无法计算下一个物价表刷新时间")


def next_hourly_price_refresh_at(
    now: datetime | None = None,
    utc_offset_hours: object = 8.0,
) -> datetime:
    """Return the next top-of-hour price collection time in the configured offset."""
    target_timezone = timezone_for_utc_offset(utc_offset_hours)
    current = now or datetime.now(target_timezone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=target_timezone)
    current = current.astimezone(target_timezone)
    return current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
