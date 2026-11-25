#!/usr/bin/env python3
"""access_event -- the access log's row for one recall, without the words that were asked.

A recall used to write the text of its question beside every memory it surfaced, so a private question became a row that
other organs read, and one of them turned it into a research topic. No question is written any more, on any path, and no
setting turns it back on. Two facts about the question are kept, because two readers need them and neither is the text:

  event_id  a random id the rows of one recall share, so "one recall" can be counted;
  machine   whether the question was one of the scheduled sweeps the spike detector leaves out. It is worked out here,
            from the text, and then the text is dropped.

Rows written before this carried text. They are left as they are: they are the owner's.
"""
from __future__ import annotations

import sqlite3
import uuid
from typing import Any, NamedTuple, Optional, Union


class Event(NamedTuple):
    id: str
    machine: bool


def new(query: Optional[str]) -> Event:
    try:
        import quickening
        machine = quickening.is_machine_query(query)
    except Exception:  # noqa: BLE001 -- a detector that cannot load never costs a recall its log row
        machine = False
    return Event(uuid.uuid4().hex, machine)


def of(value: Union[Event, str, None]) -> Event:
    """The event itself, or a new one for a caller that still holds the question."""
    return value if isinstance(value, Event) else new(value)


def has_columns(conn: sqlite3.Connection) -> bool:
    try:
        return {"event_id", "machine"} <= {r[1] for r in conn.execute("PRAGMA table_info(access_log)").fetchall()}
    except sqlite3.Error:
        return False


def log(conn: sqlite3.Connection, memory_id: int, access_type: str, score: Any, event: Event,
        with_event: Optional[bool] = None) -> None:
    """One access row. `with_event` is `has_columns`, when the caller has already asked."""
    if has_columns(conn) if with_event is None else with_event:
        conn.execute("INSERT INTO access_log (memory_id, access_type, relevance_score, event_id, machine) "
                     "VALUES (?, ?, ?, ?, ?)", (memory_id, access_type, score, event.id, int(event.machine)))
    else:
        conn.execute("INSERT INTO access_log (memory_id, access_type, relevance_score) VALUES (?, ?, ?)",
                     (memory_id, access_type, score))
