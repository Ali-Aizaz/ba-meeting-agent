"""Resolve spoken due dates in the meeting timezone.

``today`` is the calendar date in that timezone. "next Wednesday" means the
Wednesday after the coming one: from Sunday 25 Oct 2026 that is 4 Nov 2026.
An absolute day such as "24 Oct" stays in this year unless it is more than
seven days ago.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_DAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_DAY_PATTERN = "|".join(sorted(_DAYS, key=len, reverse=True))
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))


def today_in(timezone_name: str, now: datetime | None = None) -> date:
    moment = now or datetime.now(ZoneInfo(timezone_name))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(timezone_name))
    return moment.astimezone(ZoneInfo(timezone_name)).date()


def parse_due(text: str, *, today: date, timezone_name: str = "UTC") -> date | None:
    del timezone_name  # today is already the meeting-local date
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", cleaned)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    if re.search(r"\btomorrow\b", cleaned, re.I):
        return today + timedelta(days=1)
    if re.search(r"\btoday\b", cleaned, re.I):
        return today
    named = re.search(rf"\bnext\s+({_DAY_PATTERN})\b", cleaned, re.I)
    if named:
        return _weekday(today, _DAYS[named.group(1).lower()], following_week=True)
    named = re.search(rf"\b({_DAY_PATTERN})\b", cleaned, re.I)
    if named:
        return _weekday(today, _DAYS[named.group(1).lower()], following_week=False)
    absolute = re.search(rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})\b", cleaned, re.I)
    if absolute:
        return _absolute(int(absolute.group(1)), _MONTHS[absolute.group(2).lower()], today)
    absolute = re.search(rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})\b", cleaned, re.I)
    if absolute:
        return _absolute(int(absolute.group(2)), _MONTHS[absolute.group(1).lower()], today)
    return None


_MONTH_ABBR = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def format_date(value: date) -> str:
    return f"{value.day} {_MONTH_ABBR[value.month - 1]}"


def short_due(due_text: str, due: date | None) -> str:
    token = due_text.strip().lower()
    for name, number in _DAYS.items():
        if len(name) < 3:
            continue
        if name in token:
            return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][number]
    if due is not None and not token:
        return format_date(due)
    if due is not None and any(month in token for month in _MONTHS):
        return format_date(due)
    return due_text.strip() or (format_date(due) if due else "")


def _weekday(today: date, target: int, *, following_week: bool) -> date:
    delta = (target - today.weekday()) % 7
    if following_week:
        delta = 7 if delta == 0 else delta + 7
    return today + timedelta(days=delta)


def _absolute(day: int, month: int, today: date) -> date | None:
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return None
    if candidate < today - timedelta(days=7):
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            return None
    return candidate
