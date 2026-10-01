"""Automatic sync schedules: friendly presets or a cron expression."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time
from typing import Any

from cronsim import CronSim, CronSimError
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CRON,
    CONF_SCHEDULE,
    CONF_TIME,
    CONF_WEEKDAY,
    DEFAULT_TIME,
    DEFAULT_WEEKDAY,
    MIN_CRON_INTERVAL,
    SCHEDULE_CRON,
    SCHEDULE_DAILY,
    SCHEDULE_MANUAL,
    SCHEDULE_WEEKLY,
    WEEKDAYS,
)


def cron_expression(options: Mapping[str, Any]) -> str | None:
    """Return the cron expression of the configured schedule, or None for manual sync."""
    schedule = options.get(CONF_SCHEDULE, SCHEDULE_DAILY)
    if schedule == SCHEDULE_MANUAL:
        return None
    if schedule == SCHEDULE_CRON:
        return str(options[CONF_CRON]).strip()
    at = dt_util.parse_time(options.get(CONF_TIME, DEFAULT_TIME)) or time(4)
    day = (
        WEEKDAYS[options.get(CONF_WEEKDAY, DEFAULT_WEEKDAY)] if schedule == SCHEDULE_WEEKLY else "*"
    )
    return f"{at.minute} {at.hour} * * {day}"


def next_run(expression: str, after: datetime) -> datetime:
    """Return the first scheduled time strictly after an aware datetime."""
    return next(CronSim(expression, after))


def previous_run(expression: str, before: datetime) -> datetime:
    """Return the last scheduled time before an aware datetime."""
    return next(CronSim(expression, before, reverse=True))


def cron_error(expression: str, now: datetime) -> str | None:
    """Return a translation key when a cron expression is invalid or too frequent."""
    if len(expression.split()) != 5:
        return "invalid_cron"
    try:
        runs = CronSim(expression.strip(), now)
        # Inspect all minute/hour combinations, including the midnight wrap.
        # Calendar fields cannot make within-day close occurrences safe.
        slots = sorted(hour * 60 + minute for hour in runs.hours for minute in runs.minutes)
        gaps = [b - a for a, b in zip(slots, slots[1:], strict=False)]
        gaps.append(slots[0] + 1440 - slots[-1])
        if min(gaps) < MIN_CRON_INTERVAL.total_seconds() / 60:
            return "too_frequent"
        next(runs)  # Reject impossible dates; CronSim bounds its search.
    except CronSimError, StopIteration, ValueError:
        return "invalid_cron"
    return None
