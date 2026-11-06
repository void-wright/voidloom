"""A hard delete edits copies of the memory's text and nothing else.

THE CLAIM UNDER TEST
  * the sweep reads an allowlist of free-text columns. `metadata`, every stamp, provenance keys and config tables are
    never edited, whatever the deleted text says: a memory whose text is a table name leaves recall working;
  * a short or common text redacts no unrelated row. A row lineage ties to the memory loses a shared run; any other row
    loses only an exact copy of a substantial text; a text another memory also holds is not this memory's to erase;
  * every text column of the fresh schema, of the tables organs make at runtime and of an older brain is classified, so a
    new column cannot join the sweep or escape it unread.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import memory_write as mw  # noqa: E402
import schema_migrator  # noqa: E402

from voidloom.tests import test_tier_set as tt  # noqa: E402

TEMPLATE = "Distilled from 3 episodic memories across the same week. Common themes:"
LONG = ("The lantern room keeper logs every tide by hand, and the brass ledger on the north wall is the only record "
        "that survived the winter storm.")


@unittest.skipUnless(tt._vec_available(), "needs sqlite_vec")
class _Brain(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name).resolve()
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), ROOT / "migrations")
        self.assertEqual(out.get("failed") or [], [], out)
        self.conn = tt._connect(self.db)
        self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings_v2 USING vec0("
                          "memory_id INTEGER PRIMARY KEY, embedding float[1024])")
        self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0("
                          "memory_id INTEGER PRIMARY KEY, embedding float[768])")
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', "
                          "'memory_embeddings_v2')")
        mw.ensure_provenance_table(self.conn)
        self.conn.commit()
        self.addCleanup(self.conn.close)

    def memory(self, text, summary=None):
        mid = self.conn.execute("INSERT INTO memories (content, summary, category, importance) VALUES (?, ?, 'insight', 0.5)",
                                (text, summary)).lastrowid
        self.conn.execute("INSERT INTO embedding_provenance VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (mid, "memory_embeddings_v2", "m", "r", "unresolved", 1024, mw.content_hash(text),
                           "2026-10-01T00:00:00+00:00", "t", "text", None))
        return mid

    def log(self, table, column, text, **extra):
        cols = {column: text, **extra}
        names = ", ".join(cols)
        marks = ", ".join("?" * len(cols))
        return self.conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", list(cols.values())).lastrowid

    def purge(self, mid):
        with mock.patch.dict(os.environ, {mw.HARD_DELETE_ENV: "1"}):
            out = mw.purge_memory(self.conn, mid)
        self.conn.commit()
        return out

    def cell(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]


class TheMachineryIsNotText(_Brain):
    def test_saboteur_a_memory_named_for_a_table_leaves_the_brain_able_to_search(self):
        keep = self.memory("An unrelated note about harbour timetables and tide charts.")
        mid = self.memory("memory_embeddings_v2")
        self.conn.execute("INSERT INTO community_summaries (community_id, summary, member_edge_hash) VALUES "
                          "(1, 'vectors live in memory_embeddings_v2 for this brain', 'h')")
        self.log("access_log", "query_text", "memory_embeddings_v2", memory_id=keep, access_type="recall")
        before = self.conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
        out = self.purge(mid)
        self.assertEqual(self.conn.execute("SELECT key, value FROM metadata ORDER BY key").fetchall(), before)
        self.assertEqual(self.cell("SELECT vec_table FROM embedding_provenance WHERE memory_id = ?", keep),
                         "memory_embeddings_v2")
        self.assertIn("memory_embeddings_v2", self.cell("SELECT summary FROM community_summaries"))
        self.assertEqual(self.cell("SELECT query_text FROM access_log"), "memory_embeddings_v2",
                         "that log row names a different memory")
        self.assertEqual(out["rows_redacted"], {}, out)

    def test_saboteur_a_text_that_quotes_a_config_value_leaves_settings_alone(self):
        self.conn.execute("INSERT INTO persona_recall_profiles (persona_slug, display_name, description) VALUES "
                          "('p', 'P', ?)", (LONG,))
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('note', ?)", (LONG,))
        mid = self.memory(LONG)
        self.purge(mid)
        self.assertEqual(self.cell("SELECT description FROM persona_recall_profiles"), LONG)
        self.assertEqual(self.cell("SELECT value FROM metadata WHERE key = 'note'"), LONG)


class ACommonTextIsNobodysCopy(_Brain):
    def test_saboteur_a_template_shared_with_a_dream_stays_in_that_dream(self):
        mid = self.memory(f"{TEMPLATE} lantern, tide, ledger, the north wall and what the winter storm left standing.")
        a, b = self.memory("one source of a dream"), self.memory("the other source of a dream")
        rows = [self.log("dream_log", "dream_content", f"{TEMPLATE} something this memory never said, number {i}.",
                         dream_type="divergent", memory_a_id=a, memory_b_id=b) for i in range(3)]
        out = self.purge(mid)
        self.assertEqual(out["rows_redacted"], {}, out)
        for rid in rows:
            self.assertIn(TEMPLATE, self.cell("SELECT dream_content FROM dream_log WHERE id = ?", rid))

    def test_saboteur_a_short_text_redacts_no_unrelated_row(self):
        mid = self.memory("the north wall")
        self.log("lexical_miss_log", "query", "what is behind the north wall", ts="2026-01-01", fts_hits=0)
        self.assertEqual(self.purge(mid)["rows_redacted"], {})

    def test_saboteur_text_another_memory_also_holds_is_not_this_memorys_to_erase(self):
        mid = self.memory(LONG)
        twin = self.memory(LONG, "a note someone else kept")
        self.log("lexical_miss_log", "query", LONG, ts="2026-01-01", fts_hits=0)
        out = self.purge(mid)
        self.assertEqual(self.cell("SELECT content FROM memories WHERE id = ?", twin), LONG)
        self.assertEqual(self.cell("SELECT query FROM lexical_miss_log"), LONG)
        self.assertEqual((out["other_memories_sharing_text"], out["unrelated_rows_left_as_shared_text"]), (1, True))

    def test_control_the_whole_text_in_an_unrelated_row_goes(self):
        mid = self.memory(LONG)
        self.log("lexical_miss_log", "query", f"why does it say: {LONG}", ts="2026-01-01", fts_hits=0)
        self.purge(mid)
        self.assertNotIn(LONG, self.cell("SELECT query FROM lexical_miss_log"))

    def test_control_a_cell_cut_from_the_text_goes(self):
        mid = self.memory(LONG)
        self.log("lexical_miss_log", "query", LONG[10:80], ts="2026-01-01", fts_hits=0)
        self.purge(mid)
        self.assertEqual(self.cell("SELECT query FROM lexical_miss_log"), mw.REDACTED)

    def test_control_a_row_lineage_ties_loses_a_shared_run(self):
        mid = self.memory(LONG)
        run = LONG[20:75]
        derived = self.memory(f"Written from it: {run} and goes on.")
        self.conn.execute("INSERT INTO memory_provenance (derived_memory_id, source_memory_id, transformation_type, agent) "
                          "VALUES (?, ?, 'consolidation', 'test')", (derived, mid))
        row = self.log("lexical_miss_log", "query", f"shares a run, ties to nothing: {run}", ts="2026-01-01", fts_hits=0)
        self.purge(mid)
        self.assertNotIn(run, self.cell("SELECT content FROM memories WHERE id = ?", derived))
        self.assertIn(run, self.cell("SELECT query FROM lexical_miss_log WHERE rowid = ?", row))

    def test_control_a_summary_of_the_community_it_sat_in_loses_a_shared_run(self):
        mid = self.memory(LONG)
        run = LONG[20:75]
        self.conn.execute("INSERT INTO memory_community_members (community_id, epoch, memory_id) VALUES (7, 1, ?)", (mid,))
        self.conn.execute("INSERT INTO community_summaries (community_id, summary, member_edge_hash) VALUES (7, ?, 'h')",
                          (f"This community says: {run}, among other things.",))
        self.purge(mid)
        self.assertNotIn(run, self.cell("SELECT summary FROM community_summaries"))


class AnotherMemoryHoldsOneOfItsTexts(_Brain):
    """Sharing is a fact about one text. A summary two memories hold says nothing about the content only one of them holds."""
    SUMMARY = "A note about the harbour wall that two memories carry word for word, more than long enough."
    OTHER = "Something else entirely about a different coastline, with its own words and its own reasons."

    def plant(self, table_text):
        self.conn.execute("INSERT INTO lexical_miss_log (query) VALUES (?)", (table_text,))
        self.conn.execute("INSERT INTO whisper_observations (observation_type, observation) VALUES ('insight', ?)", (table_text,))

    def left(self, text):
        return sum(self.cell(f"SELECT COUNT(*) FROM {t} WHERE {c} LIKE ?", "%" + text[:40] + "%")
                   for t, c in (("lexical_miss_log", "query"), ("whisper_observations", "observation")))

    def test_saboteur_a_shared_summary_leaves_the_content_to_the_sweep(self):
        mid = self.memory(LONG, self.SUMMARY)
        self.memory(self.OTHER, self.SUMMARY)
        self.plant(LONG)
        out = self.purge(mid)
        self.assertEqual(self.left(LONG), 0, out)
        self.assertEqual(out["other_memories_sharing_text"], 1, out)

    def test_saboteur_another_memory_that_merely_opens_with_the_summary_is_the_same_case(self):
        mid = self.memory(LONG, self.SUMMARY)
        self.memory(self.SUMMARY + " " + self.OTHER, "its own summary")
        self.plant(LONG)
        out = self.purge(mid)
        self.assertEqual(self.left(LONG), 0, out)

    def test_control_the_shared_text_itself_stays_in_unrelated_rows(self):
        mid = self.memory(LONG, self.SUMMARY)
        self.memory(self.OTHER, self.SUMMARY)
        self.plant(self.SUMMARY)
        out = self.purge(mid)
        self.assertEqual(self.left(self.SUMMARY), 2, out)
        self.assertIs(out["unrelated_rows_left_as_shared_text"], True, out)

    def test_control_a_shared_content_stays_everywhere_and_is_counted_once_per_memory(self):
        mid = self.memory(LONG, "one")
        self.memory(LONG, "two")
        self.memory(LONG, "three")
        self.plant(LONG)
        out = self.purge(mid)
        self.assertEqual(self.left(LONG), 2, out)
        self.assertEqual(out["other_memories_sharing_text"], 2, out)

    def test_control_with_nothing_shared_the_flag_stays_down(self):
        mid = self.memory(LONG, self.SUMMARY)
        self.plant(LONG)
        out = self.purge(mid)
        self.assertEqual((self.left(LONG), out["unrelated_rows_left_as_shared_text"]), (0, False), out)


class EveryColumnIsClassified(_Brain):
    def test_saboteur_a_new_text_column_fails_here_until_someone_reads_it(self):
        self.assertEqual(mw._copy_columns(self.conn)[1], [], "classify each in memory_write._TEXT_COPY_COLUMNS or "
                         "_NEVER_SWEPT_COLUMNS")

    def test_saboteur_tables_an_organ_makes_at_runtime_are_classified_too(self):
        """A fresh schema does not hold them, so the test above cannot see them: read their DDL from the organs that own them."""
        have = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = sorted(t for t in {*mw._TEXT_COPY_COLUMNS, *mw._NEVER_SWEPT_COLUMNS} if t not in have)
        self.assertTrue(missing, "every mapped table is on the fresh schema now: this test can go")
        found = []
        for py in sorted((ROOT / "organs").glob("*.py")) + sorted((ROOT / "migrations").glob("*.sql")):
            text = py.read_text(encoding="utf-8", errors="replace")
            for name in missing:
                m = re.search(r"CREATE TABLE (?:IF NOT EXISTS )?%s\s*\(" % name, text)
                if not m or name in found:
                    continue
                depth, i = 0, m.end() - 1
                while True:
                    depth += {"(": 1, ")": -1}.get(text[i], 0)
                    i += 1
                    if depth == 0:
                        break
                self.conn.execute(text[m.start():i])
                found.append(name)
        self.assertTrue(found, "no organ creates a mapped table any more: read the map")
        self.assertEqual(mw._copy_columns(self.conn)[1], [])     # the rest are made by nothing here: older brains hold them

    def test_saboteur_columns_an_older_brain_still_holds_are_classified(self):
        self.conn.execute("ALTER TABLE memories ADD COLUMN skill_tier TEXT") if "skill_tier" not in mw._columns(self.conn, "memories") else None
        self.conn.execute("ALTER TABLE council_sessions ADD COLUMN substrate_version TEXT") \
            if "substrate_version" not in mw._columns(self.conn, "council_sessions") else None
        self.assertEqual(mw._copy_columns(self.conn)[1], [])

    def test_saboteur_a_review_that_quotes_the_memory_loses_the_quote(self):
        mid = self.memory(LONG)
        self.conn.execute("INSERT INTO self_eval_reports (month, health_scorecard, proposed_enhancements, proposed_deprecations, "
                          "threshold_assessment, drew_approved) VALUES ('2026-10', '{}', '[]', '[]', '{}', ?)",
                          (json.dumps({"approved": [LONG], "rejected": []}),))
        self.purge(mid)
        self.assertNotIn(LONG[:30], self.conn.execute("SELECT drew_approved FROM self_eval_reports").fetchone()[0])

    def test_control_an_unclassified_column_is_left_alone_and_named(self):
        self.conn.execute("CREATE TABLE made_at_runtime (note TEXT)")
        self.conn.execute("INSERT INTO made_at_runtime VALUES (?)", (LONG,))
        mid = self.memory(LONG)
        out = self.purge(mid)
        self.assertEqual(self.cell("SELECT note FROM made_at_runtime"), LONG)
        self.assertIn("made_at_runtime.note", out["text_columns_not_classified"])

    def test_control_no_key_column_is_in_the_allowlist(self):
        for table, cols in mw._TEXT_COPY_COLUMNS.items():
            self.assertNotIn(table, ("metadata", "schema_migrations", mw.PROVENANCE_TABLE))
            self.assertFalse(set(cols) & set(mw._NEVER_SWEPT_COLUMNS.get(table, ())), table)


if __name__ == "__main__":
    unittest.main()
