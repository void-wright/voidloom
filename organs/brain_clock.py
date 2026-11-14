#!/usr/bin/env python3
"""brain_clock.py -- the brain's ONE clock: a UTC instant, written in the brain's own timestamp format.

Every timestamp column the schema fills itself (`datetime('now')`) holds UTC as 'YYYY-MM-DD HH:MM:SS'. An organ that
writes or compares a time must use the same instant and the same text, or a string comparison mixes two clocks:

  * datetime.now() is LOCAL time -- west of UTC every age is short by the offset, and a window drifts by hours;
  * .isoformat() writes a 'T' between date and time, and 'T' sorts AFTER ' ' -- so a 'T' cutoff drops every row of its
    own date, and a 'T' row sorts after every same-date row it should precede;
  * datetime.utcnow().isoformat() fixes the first and keeps the second (measured on a copy of a live brain: a two-day
    window selected 400 rows where the correct cutoff selects 593; the local-time 'T' cutoff selected 650).

Stdlib only, so every organ can import it before anything else. A time that is deliberately LOCAL (the owner's hour
of day, a calendar-date label) does not belong here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def utc_now() -> datetime:
    """The current instant in UTC, naive (the brain's stamps carry no offset)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_stamp(dt: Optional[datetime] = None) -> str:
    """`dt` (default: now) in the brain's own format, 'YYYY-MM-DD HH:MM:SS'. `dt` is a naive UTC instant."""
    return (dt if dt is not None else utc_now()).strftime(STAMP_FORMAT)


def utc_cutoff(days: float, now: Optional[datetime] = None) -> str:
    """The stamp `days` before `now` (default: now): the lower bound of a window, comparable as text with any column
    the schema stamps with datetime('now')."""
    return utc_stamp((now if now is not None else utc_now()) - timedelta(days=days))
