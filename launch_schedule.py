"""PoE2 launch countdown and per-group launch reminder state."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


DEFAULT_LAUNCH_DATE = "2026-12-11"
DEFAULT_LAUNCH_UTC_OFFSET = -8.0
DEFAULT_LAUNCH_SOURCE_URL = (
    "https://www.pathofexile.com/forum/view-thread/4012710"
)


class LaunchScheduleError(ValueError):
    """Raised when the configured launch date or time is invalid."""


@dataclass(frozen=True)
class Poe2LaunchSchedule:
    launch_date: date
    launch_time: time | None
    utc_offset_hours: float
    source_url: str

    @property
    def timezone(self) -> timezone:
        return timezone(timedelta(hours=self.utc_offset_hours))

    @property
    def scheduled_at(self) -> datetime:
        return datetime.combine(
            self.launch_date,
            self.launch_time or time.min,
            tzinfo=self.timezone,
        )

    @property
    def event_id(self) -> str:
        launch_time = (
            self.launch_time.isoformat(timespec="minutes")
            if self.launch_time
            else "date-only"
        )
        return f"{self.launch_date.isoformat()}|{launch_time}|UTC{self.utc_offset_hours:+g}"


def load_launch_schedule(config: object) -> Poe2LaunchSchedule:
    """Read the launch date/time from an AstrBot config-like mapping."""
    get_value = getattr(config, "get", None)
    if not callable(get_value):
        raise LaunchScheduleError("无法读取开服倒计时配置")

    raw_date = str(get_value("poe2_launch_date", DEFAULT_LAUNCH_DATE) or "").strip()
    try:
        launch_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise LaunchScheduleError("开服日期应使用 YYYY-MM-DD 格式") from exc

    raw_time = str(get_value("poe2_launch_time", "") or "").strip()
    launch_time: time | None = None
    if raw_time:
        try:
            launch_time = datetime.strptime(raw_time, "%H:%M").time()
        except ValueError as exc:
            raise LaunchScheduleError("开服时间应使用 24 小时制 HH:MM 格式") from exc

    try:
        utc_offset_hours = float(
            get_value("poe2_launch_utc_offset", DEFAULT_LAUNCH_UTC_OFFSET)
        )
    except (TypeError, ValueError) as exc:
        raise LaunchScheduleError("开服时区 UTC 偏移必须是数字") from exc
    if not -12 <= utc_offset_hours <= 14:
        raise LaunchScheduleError("开服时区 UTC 偏移范围为 -12 到 14 小时")

    source_url = str(
        get_value("poe2_launch_source_url", DEFAULT_LAUNCH_SOURCE_URL)
        or DEFAULT_LAUNCH_SOURCE_URL
    ).strip()
    return Poe2LaunchSchedule(
        launch_date=launch_date,
        launch_time=launch_time,
        utc_offset_hours=utc_offset_hours,
        source_url=source_url,
    )


def format_launch_countdown(
    schedule: Poe2LaunchSchedule,
    now: datetime | None = None,
) -> str:
    """Format an exact countdown when available, otherwise count to the date."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    remaining = schedule.scheduled_at.astimezone(timezone.utc) - current.astimezone(
        timezone.utc
    )
    utc_offset = _format_utc_offset(schedule.utc_offset_hours)
    date_text = schedule.launch_date.isoformat()

    if schedule.launch_time is None:
        if remaining.total_seconds() > 0:
            countdown = _format_duration(remaining)
            status = f"距离官方公布的开服日期开始还有：{countdown}"
            detail = "官方目前只公布日期，具体开服时刻尚未确认；倒计时按该时区当天 00:00 计算。"
        else:
            event_local_date = current.astimezone(schedule.timezone).date()
            status = (
                "官方公布的开服日期已到。"
                if event_local_date == schedule.launch_date
                else "官方公布的开服日期已过。"
            )
            detail = "具体开服时刻尚未确认，请以官方公告和游戏内状态为准。"
        time_text = f"{date_text}（UTC{utc_offset}）"
    else:
        local_time = schedule.scheduled_at.strftime("%Y-%m-%d %H:%M")
        if remaining.total_seconds() > 0:
            status = f"距离计划开服还有：{_format_duration(remaining)}"
        else:
            status = "计划开服时间已到或已过；实际开放状态以官方公告和游戏内状态为准。"
        detail = "开服时间按配置的官方时区换算。"
        time_text = f"{local_time}（UTC{utc_offset}）"

    return "\n".join(
        (
            "《流放之路 2》开服倒计时",
            status,
            f"官方公布日期 / 配置时间：{time_text}",
            detail,
            f"官方公告：{schedule.source_url}",
        )
    )


def format_launch_reminder(schedule: Poe2LaunchSchedule) -> str:
    if schedule.launch_time is None:
        summary = (
            f"官方公布的开服日期已到：{schedule.launch_date.isoformat()} "
            f"（UTC{_format_utc_offset(schedule.utc_offset_hours)}）。"
            "官方尚未公布具体开服时刻，请以官方公告和游戏内状态为准。"
        )
    else:
        summary = (
            "《流放之路 2》计划开服时间已到："
            f"{schedule.scheduled_at:%Y-%m-%d %H:%M} "
            f"（UTC{_format_utc_offset(schedule.utc_offset_hours)}）。"
            "实际开放状态以官方公告和游戏内状态为准。"
        )
    return f"{summary}\n官方公告：{schedule.source_url}"


def _format_duration(duration: timedelta) -> str:
    seconds = max(0, int(duration.total_seconds()))
    days, seconds = divmod(seconds, 24 * 60 * 60)
    hours, seconds = divmod(seconds, 60 * 60)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days} 天 {hours} 小时 {minutes} 分钟"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟 {seconds} 秒"
    return f"{seconds} 秒"


def _format_utc_offset(offset_hours: float) -> str:
    sign = "+" if offset_hours >= 0 else "-"
    minutes = round(abs(offset_hours) * 60)
    hours, minutes = divmod(minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


class LaunchReminderStore:
    """Persist per-group delivery state so restarts do not repeat reminders."""

    def __init__(self, data_dir: str | Path) -> None:
        self.directory = Path(data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / "poe2_launch_reminder.json"
        self._event_id = ""
        self._delivered_to: set[str] = set()
        self._load()

    def prepare_event(self, event_id: str) -> None:
        if self._event_id == event_id:
            return
        self._event_id = event_id
        self._delivered_to.clear()
        self._save()

    def pending_for(self, destination: str) -> bool:
        return destination not in self._delivered_to

    def mark_delivered(self, destination: str) -> None:
        self._delivered_to.add(destination)
        self._save()

    def _load(self) -> None:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return
        if not isinstance(state, dict) or state.get("version") != 1:
            return
        event_id = state.get("event_id")
        delivered_to = state.get("delivered_to")
        if isinstance(event_id, str):
            self._event_id = event_id
        if isinstance(delivered_to, list):
            self._delivered_to = {
                destination
                for destination in delivered_to
                if isinstance(destination, str)
            }

    def _save(self) -> None:
        state = {
            "version": 1,
            "event_id": self._event_id,
            "delivered_to": sorted(self._delivered_to),
        }
        temporary_path = self.state_path.with_name(
            f".{self.state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_path, self.state_path)
        finally:
            temporary_path.unlink(missing_ok=True)
