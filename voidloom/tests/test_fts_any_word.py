# -*- coding: utf-8 -*-
"""The keyword leg matches ANY content word, in all three places that build it.

THE CLAIM UNDER TEST. Keyword (BM25) recall lets a memory match on any content
word of the query and ranks by BM25. Until 2026-10-27 the query builder joined
words with spaces, which FTS5 reads as implicit AND, so a memory had to contain
EVERY word of a question ("when" and "did" included). Measured: 2 of 152 LoCoMo
questions found any keyword candidate, and the Tier-0 engine raised an FTS5
syntax error on a '?' or an apostrophe and found nothing at all.

Three copies build the expression, because two of them must stay stdlib-only:
    organs/hybrid_retrieval.py     Tier 1 (the BM25 leg of hybrid recall)
    organs/claude_memory_lite.py   Tier 0 (FTS is its only search)
    install/self_check.py          the installer's smoke recall
They must agree exactly.

Shipped as control pairs:
    *_control_*   the shipped code, and the check must read GREEN;
    *_saboteur_*  the defect planted, and the check must read RED.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for sub in ("organs", "install"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import claude_memory_lite as lite  # noqa: E402
import hybrid_retrieval as hybrid  # noqa: E402
import self_check  # noqa: E402

QUERIES = [
    "When did Caroline go to the LGBTQ support group?",
    "what's up?",
    "Caroline's group",
    "bge-reranker-v2-m3 recall",
    "v2.1 release notes",
    "who are you",
    "a . b - c",
    "AND OR NOT",
    "",
    "   ",
    "9.8/10?",
    "Ünïcode café über",
    'quoted "phrase" inside',
    "how do I push to the public voidloom repo",
]

BUILDERS = {"hybrid": hybrid._fts_match_expr, "lite": lite._fts_match_expr,
            "self_check": self_check._fts_match_expr}


def disagreements(builders, queries):
    """Queries on which the builders do not produce the identical expression."""
    out = []
    for q in queries:
        exprs = {name: fn(q) for name, fn in builders.items()}
        if len(set(exprs.values())) != 1:
            out.append((q, exprs))
    return out


def is_any_word(expr):
    """True if a multi-term expression lets ANY term match (terms OR-joined)."""
    terms = expr.split(" OR ")
    return len(terms) > 1 and all(t and (t.startswith('"') or " " not in t) for t in terms)


def fts_hits(expr, docs):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE VIRTUAL TABLE t USING fts5(content)")
    c.executemany("INSERT INTO t VALUES (?)", [(d,) for d in docs])
    return c.execute("SELECT count(*) FROM t WHERE t MATCH ?", (expr,)).fetchone()[0]


DOCS = ["Caroline went to the LGBTQ support group on 7 May 2023",
        "Melanie painted a sunrise over the lake",
        "The bge-reranker-v2-m3 model was swapped out in v2.1"]


class FtsAnyWord(unittest.TestCase):

    # -- the three copies agree -------------------------------------------
    def test_control_the_three_builders_agree(self):
        self.assertEqual(disagreements(BUILDERS, QUERIES), [])

    def test_saboteur_a_builder_that_drifts_is_caught(self):
        drifted = dict(BUILDERS, lite=lambda q: lite._sanitize_fts_query(q))  # the old space-join
        self.assertNotEqual(disagreements(drifted, QUERIES), [])

    # -- a question becomes an any-word expression -------------------------
    def test_control_a_question_is_any_word(self):
        expr = hybrid._fts_match_expr(QUERIES[0])
        self.assertTrue(is_any_word(expr), expr)
        self.assertNotIn("when", expr.lower().split(" or "))

    def test_saboteur_the_old_space_join_is_not_any_word(self):
        self.assertFalse(is_any_word(hybrid._sanitize_fts_query(QUERIES[0])))

    # -- and it finds the memory that answers it ---------------------------
    def test_control_a_question_finds_a_partially_matching_memory(self):
        q = "When did Caroline go to the LGBTQ support group?"
        self.assertGreaterEqual(fts_hits(hybrid._fts_match_expr(q), DOCS), 1)

    def test_saboteur_every_word_required_finds_nothing(self):
        q = "When did Caroline go to the LGBTQ support group?"
        self.assertEqual(fts_hits(hybrid._sanitize_fts_query(q), DOCS), 0)

    # -- edges -------------------------------------------------------------
    def test_control_an_all_stopword_query_still_searches(self):
        self.assertEqual(hybrid._fts_match_expr("who are you"), "who OR are OR you")

    def test_control_quoted_phrases_survive_and_still_match(self):
        expr = hybrid._fts_match_expr("bge-reranker-v2-m3 recall")
        self.assertIn('"bge-reranker-v2-m3"', expr)
        self.assertGreaterEqual(fts_hits(expr, DOCS), 1)

    def test_control_punctuation_never_reaches_fts5(self):
        for q in QUERIES:
            expr = hybrid._fts_match_expr(q)
            if expr:
                fts_hits(expr, DOCS)  # raises sqlite3.OperationalError on a syntax error

    def test_control_empty_queries_build_nothing(self):
        for q in ("", "   ", "? ! ."):
            self.assertEqual(hybrid._fts_match_expr(q), "")

    def test_control_the_stopword_lists_are_one_list(self):
        self.assertEqual(hybrid.FTS_STOPWORDS, lite.FTS_STOPWORDS)
        self.assertEqual(hybrid.FTS_STOPWORDS, self_check.FTS_STOPWORDS)

    # -- a pasted page must not cost seconds ---------------------------------
    def test_control_the_expression_is_bounded(self):
        long_q = " ".join(f"word{i}" for i in range(500))
        for name, fn in BUILDERS.items():
            self.assertLessEqual(len(fn(long_q).split(" OR ")), hybrid.FTS_MAX_TERMS, name)
        self.assertEqual(hybrid.FTS_MAX_TERMS, lite.FTS_MAX_TERMS)
        self.assertEqual(hybrid.FTS_MAX_TERMS, self_check.FTS_MAX_TERMS)

    def test_saboteur_an_unbounded_builder_goes_red(self):
        long_q = " ".join(f"word{i}" for i in range(500))
        unbounded = " OR ".join(hybrid._sanitize_fts_query(long_q).split())
        self.assertGreater(len(unbounded.split(" OR ")), hybrid.FTS_MAX_TERMS)

    def test_control_a_query_under_the_cap_is_unchanged_by_it(self):
        q = "When did Caroline go to the LGBTQ support group?"
        expr = hybrid._fts_match_expr(q)
        self.assertEqual(expr, "Caroline OR go OR LGBTQ OR support OR group")

    # -- nothing searchable means no FTS5 call -------------------------------
    def test_control_tier0_and_self_check_skip_an_empty_expression(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, importance REAL, "
                  "created_at TEXT, is_archived INTEGER DEFAULT 0)")
        c.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(content)")
        self.assertEqual(self_check._recall(c, "? ! .", 5), [])

    def test_control_hybrid_skips_an_empty_expression_without_a_warning(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, is_archived INTEGER DEFAULT 0)")
        c.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(content)")
        statements = []
        c.set_trace_callback(statements.append)
        self.assertEqual(hybrid.fts_match(c, "? ! .", top_k=5), [])
        self.assertFalse([s for s in statements if "MATCH" in s.upper()], "an empty expression reached FTS5")

    def test_control_tier0_recall_skips_an_empty_expression(self):
        import argparse, tempfile, os
        d = tempfile.mkdtemp()
        db = os.path.join(d, "claude_memory.db")
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, summary TEXT, content TEXT, category TEXT, "
                  "importance REAL, created_at TEXT, is_archived INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT)")
        c.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(content)")
        c.commit(); c.close()
        statements = []
        real_connect = lite._connect
        def traced(*a, **k):
            conn = real_connect(*a, **k)
            conn.set_trace_callback(statements.append)
            return conn
        old_db, lite.DB_PATH, lite._connect = lite.DB_PATH, type(lite.DB_PATH)(db), traced
        try:
            rc = lite.cmd_recall(argparse.Namespace(query="? ! .", top_k=5))
        finally:
            lite.DB_PATH, lite._connect = old_db, real_connect
        self.assertEqual(rc, 0)
        self.assertFalse([s for s in statements if "MATCH" in s.upper()], "Tier 0 sent an empty or phrase-fallback MATCH")

    def test_control_fts_match_uses_the_any_word_expression(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, is_archived INTEGER DEFAULT 0)")
        c.execute("CREATE VIRTUAL TABLE memories_fts USING fts5(content)")
        for i, d in enumerate(DOCS, 1):
            c.execute("INSERT INTO memories (id) VALUES (?)", (i,))
            c.execute("INSERT INTO memories_fts (rowid, content) VALUES (?, ?)", (i, d))
        got = hybrid.fts_match(c, "When did Caroline go to the LGBTQ support group?", top_k=5)
        self.assertEqual([mid for mid, _ in got][:1], [1])


if __name__ == "__main__":
    unittest.main()
