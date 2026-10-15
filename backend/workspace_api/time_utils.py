"""One date/time policy for Daily and Todo projections."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


BUSINESS_TIMEZONE_NAME = os.getenv("PAW_WORKSPACE_TIMEZONE", "Asia/Shanghai")
try:
    BUSINESS_TIMEZONE = ZoneInfo(BUSINESS_TIMEZONE_NAME)
except Exception:
    BUSINESS_TIMEZONE = timezone(timedelta(hours=8))


def now_business() -> datetime:
    return datetime.now(BUSINESS_TIMEZONE)


def business_today() -> date:
    return now_business().date()


def parse_date(value: str, *, field: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must use YYYY-MM-DD") from exc


def parse_month(value: str) -> tuple[date, date]:
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        raise ValueError("month must use YYYY-MM")
    try:
        start = date(int(value[:4]), int(value[5:7]), 1)
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use YYYY-MM") from exc
    next_month = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, next_month


def business_date(value: str | datetime | date) -> date:
    """Return the business-local date for an ISO timestamp or date."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if len(raw) == 10:
            return parse_date(raw, field="timestamp")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(BUSINESS_TIMEZONE).date()


def business_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(BUSINESS_TIMEZONE)


def date_window_end(inclusive_end: date) -> date:
    return inclusive_end + timedelta(days=1)


def as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
