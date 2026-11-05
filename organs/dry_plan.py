#!/usr/bin/env python3
"""dry_plan -- what a scheduled unit would work over, read and printed, with nothing written.

Every armed unit answers `--dry-run` by this one route. It opens the brain read-only, counts the rows the unit would work
over, and prints one JSON line naming the unit, what a real run does, and those counts. It loads no model, reaches no
network and starts no thinker; a count it cannot read is null, never zero. Stdlib only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Optional, Union


def _count(conn: sqlite3.Connection, sql: str) -> Optional[int]:
    try:
        return int(conn.execute(sql).fetchone()[0])
    except sqlite3.Error:
        return None


def plan(unit: str, db: Optional[Union[str, Path]], would: str, counts: Optional[Dict[str, str]] = None,
         **facts: object) -> int:
    """Print the plan and return the exit code: 0 with a readable brain, 1 when `db` names none."""
    path = Path(db or os.environ.get("WIZARD_LIBRARY_DB") or Path(__file__).resolve().parent.parent / "claude_memory.db")
    out = {"status": "ok", "action": "dry-run", "unit": unit, "would": would, "writes": 0, "db": str(path), "counts": {},
           **facts}
    if not path.is_file():
        out.update(status="error", error="DB_ABSENT", message=f"no brain at {path}")
        print(json.dumps(out))
        return 1
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    try:
        queries = {"live_memories": "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived, 0) = 0", **(counts or {})}
        out["counts"] = {name: _count(conn, sql) for name, sql in queries.items()}
    finally:
        conn.close()
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(plan(sys.argv[1] if len(sys.argv) > 1 else "?", sys.argv[2] if len(sys.argv) > 2 else None,
                  "report the brain's size"))
