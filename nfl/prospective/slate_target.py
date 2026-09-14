#!/usr/bin/env python3
"""Resolve a live NFL slate date without silently drifting to the wrong day."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


def validate_target_local_date(value: str) -> str:
    """Return strict ISO YYYY-MM-DD or fail closed."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("target_local_date is required")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("target_local_date must be strict YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError("target_local_date must be strict YYYY-MM-DD")
    return text


def next_sunday_local(reference: datetime | date | None = None) -> str:
    """Return the next Sunday in America/Chicago, including today if Sunday."""
    if reference is None:
        local_day = datetime.now(CT).date()
    elif isinstance(reference, datetime):
        if reference.tzinfo is None:
            raise ValueError("reference datetime must be timezone-aware")
        local_day = reference.astimezone(CT).date()
    elif isinstance(reference, date):
        local_day = reference
    else:
        raise TypeError("reference must be date, datetime, or None")

    days_until_sunday = (6 - local_day.weekday()) % 7
    return (local_day + timedelta(days=days_until_sunday)).isoformat()


def resolve_target_local_date(
    manual_override: str | None,
    *,
    reference: datetime | date | None = None,
) -> str:
    """Use an explicit validated manual date; otherwise preserve Sunday fallback."""
    text = str(manual_override or "").strip()
    if text:
        return validate_target_local_date(text)
    return next_sunday_local(reference)
