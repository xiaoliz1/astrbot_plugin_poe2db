"""Fixed Beijing-time refresh schedule for cached PoE2 price tables."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

BEIJING_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
REFRESH_HOURS = (0, 6, 12, 18)


def next_price_refresh_at(now: datetime | None = None) -> datetime:
    """Return the next 00:00/06:00/12:00/18:00 Beijing-time refresh slot."""
    current = now or datetime.now(BEIJING_TIMEZONE)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING_TIMEZONE)
    current = current.astimezone(BEIJING_TIMEZONE)

    for day_offset in range(2):
        date = current.date() + timedelta(days=day_offset)
        for hour in REFRESH_HOURS:
            candidate = datetime.combine(
                date, time(hour=hour), tzinfo=BEIJING_TIMEZONE
            )
            if candidate > current:
                return candidate

    raise RuntimeError("无法计算下一个物价表刷新时间")
