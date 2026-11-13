"""Resolve spoken due dates in the meeting timezone.

``today`` is the calendar date in that timezone. "next Wednesday" means the
Wednesday after the coming one: from Sunday 25 Oct 2026 that is 4 Nov 2026.
An absolute day such as "24 Oct" stays in this year unless it is more than
seven days ago.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger("ba_agent.dates")

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


_WORD_NUMBERS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def today_in(timezone_name: str, now: datetime | None = None) -> date:
    try:
        tz = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        logger.warning("Invalid timezone %r; falling back to UTC", timezone_name)
        tz = timezone.utc
    moment = now or datetime.now(tz)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment.astimezone(tz).date()


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
    if re.search(
        r"\b(?:today|tonight|this\s+evening|end\s+of\s+(?:the\s+)?day|by\s+eod|eod|cob)\b",
        cleaned,
        re.I,
    ):
        return today
    rel_days = re.search(
        r"\bin\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+days?\b",
        cleaned,
        re.I,
    )
    if rel_days:
        raw_n = rel_days.group(1).lower()
        n = int(raw_n) if raw_n.isdigit() else _WORD_NUMBERS[raw_n]
        return today + timedelta(days=n)
    rel_weeks = re.search(
        r"\bin\s+(\d+|a|an|one|two|three|four)\s+weeks?\b",
        cleaned,
        re.I,
    )
    if rel_weeks:
        raw_n = rel_weeks.group(1).lower()
        n = int(raw_n) if raw_n.isdigit() else _WORD_NUMBERS[raw_n]
        return today + timedelta(weeks=n)
    # Named weekdays MUST run before standalone "this week" / "next week" cues.
    named = re.search(rf"\bnext\s+({_DAY_PATTERN})\b", cleaned, re.I)
    if named:
        return _weekday(today, _DAYS[named.group(1).lower()], following_week=True)
    named = re.search(rf"\b(?:this\s+)?({_DAY_PATTERN})\b", cleaned, re.I)
    if named:
        return _weekday(today, _DAYS[named.group(1).lower()], following_week=False)
    if re.search(r"\b(?:end\s+of\s+(?:the\s+)?week|by\s+eow|eow|this\s+week)\b", cleaned, re.I):
        return _weekday(today, 4, following_week=False)
    if re.search(r"\bnext\s+week\b", cleaned, re.I):
        return _weekday(today, 0, following_week=True)
    absolute = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_PATTERN})\.?\b",
        cleaned,
        re.I,
    )
    if absolute:
        return _absolute(int(absolute.group(1)), _MONTHS[absolute.group(2).lower()], today)
    absolute = re.search(
        rf"\b({_MONTH_PATTERN})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
        cleaned,
        re.I,
    )
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
        if re.search(rf"\b{re.escape(name)}\b", token):
            return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][number]
    if due is not None and not token:
        return format_date(due)
    if due is not None and re.search(rf"\b(?:{_MONTH_PATTERN})\b", token):
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
