"""Fixed 10-day period definitions for budget and expenses."""

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class PeriodDefinition:
    index: int
    name: str
    start_day: int


PERIOD_DEFINITIONS: tuple[PeriodDefinition, ...] = (
    PeriodDefinition(1, "Days 1-10", 1),
    PeriodDefinition(2, "Days 11-20", 11),
    PeriodDefinition(3, "Days 21-end", 21),
)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def period_end_day(year: int, month: int, period_index: int) -> int:
    last_day = days_in_month(year, month)
    if period_index == 1:
        return min(10, last_day)
    if period_index == 2:
        return min(20, last_day)
    return last_day


def period_date_range(year: int, month: int, period_index: int) -> tuple[date, date]:
    period = next(p for p in PERIOD_DEFINITIONS if p.index == period_index)
    end_day = period_end_day(year, month, period_index)
    return date(year, month, period.start_day), date(year, month, end_day)


def period_index_for_day(day: int) -> int:
    if day <= 10:
        return 1
    if day <= 20:
        return 2
    return 3


def period_label(period_index: int) -> str:
    return next(p.name for p in PERIOD_DEFINITIONS if p.index == period_index)
