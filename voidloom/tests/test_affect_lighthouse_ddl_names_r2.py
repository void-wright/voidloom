# -*- coding: utf-8 -*-
"""Round 2, lane R2c, finding M4 (its two runtime strings): affect_lighthouse's quarantine DDL carried two comments
naming the owner ("a short human note for Drew", "for Drew's later reading"). A comment inside a CREATE TABLE string
is not inert: SQLite stores the statement text in sqlite_master, so every stranger's brain would carry the name in its
schema. RED on 6bb45ee, GREEN on lane/R2c."""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import affect_lighthouse as AL  # noqa: E402


def test_the_stored_schema_names_no_owner(tmp_path):
    c = sqlite3.connect(tmp_path / "b.db")
    AL.ensure_quarantine_table(c)
    sql = c.execute("SELECT sql FROM sqlite_master WHERE name = ?", (AL.QUARANTINE_TABLE,)).fetchone()[0]
    assert not re.search(r"\b(drew|eddie)\b", sql, re.I), sql
    assert "for the owner" in sql
