"""A lone '.' or '-' in a recall query must not break the BM25 channel (round 2; FTS5 raised a syntax error)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "organs"))
import hybrid_retrieval as hr  # noqa: E402


def test_lone_punctuation_tokens_are_dropped():
    assert hr._sanitize_fts_query(".") == ""
    assert hr._sanitize_fts_query("a . b - c") == "a b c"
    assert hr._sanitize_fts_query("v2.1 release") == '"v2.1" release'


def test_every_sanitized_query_is_valid_fts5():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("INSERT INTO t VALUES ('gradle build cache daemon')")
    for q in (".", "a . b", "gradle . cache", "x - y", "-- .", "what is gradle.build?", "node.js 3.14"):
        s = hr._sanitize_fts_query(q)
        if s:
            conn.execute("SELECT rowid FROM t WHERE t MATCH ?", (s,)).fetchall()
