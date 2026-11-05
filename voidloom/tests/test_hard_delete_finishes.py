"""A hard delete finishes, whatever names the memory, and its text leaves with it.

THE CLAIM UNDER TEST
  * under VOIDLOOM_ALLOW_HARD_DELETE every foreign key into `memories` is settled in one
    transaction: the row goes, or stays with the reference nulled where other rows hang from it;
  * the deleted text survives nowhere: no table, no full-text storage, no page of the file;
  * the receipt is counts per table and carries no content;
  * a failure part way leaves everything as it was;
  * without the gate nothing changes: a soft archive with every log row in place;
  * a different memory that says the same thing is counted, never edited.
"""
from __future__ import annotations

import json
import os
import sqlite3
from unittest import mock

from voidloom.tests.test_engine_reads_the_search_set import JINA, NOMIC, QWEN, _Engine, _unit

WORD = "zephyrquartz"
SENTENCE = f"The {WORD} valve on the north manifold must be bled before the third cycle or the gauge reads false."
CONTENT = f"Opening line for context. {SENTENCE} A closing remark that names the lantern room and its keeper."
SUMMARY = f"Bleed the {WORD} valve before the third cycle, or the gauge on the north manifold reads false."
GATE = {"VOIDLOOM_ALLOW_HARD_DELETE": "1"}
NOT_NULL_FKS = {"consolidation_sources", "counterfactual_decisions", "cross_tier_bridges", "dream_log",
                "memory_provenance", "resurfacing_log"}


class _Brain(_Engine):
    def setUp(self):
        super().setUp()
        add = self.memory
        self.mid = add(CONTENT, SUMMARY)
        self.other = add("A separate note about the harbour timetable, unrelated to anything else here.", "harbour")
        self.consolidated = add(f"Distilled from 2 episodic memories. Core insight: {SUMMARY}. Supporting: harbour.",
                                f"Consolidated 2 episodic memories: {SUMMARY[:60]}")
        self.dream = add(f"A dream on both: {SENTENCE}", "a dream")
        c = self.conn
        c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) "
                  "VALUES (?, ?, 1)", (self.consolidated, self.mid))
        c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) "
                  "VALUES (?, ?, 1)", (self.consolidated, self.other))
        c.execute("INSERT INTO counterfactual_decisions (decision_memory_id, topic, chosen_path, alternative_paths, "
                  "captured_at, captured_by, status) VALUES (?, ?, ?, '[]', '2026-01-01', 'manual', 'active')",
                  (self.mid, SENTENCE, SENTENCE))
        c.execute("INSERT INTO cross_tier_bridges (local_memory_id, remote_tier, remote_db_path, remote_memory_id, "
                  "bridge_type) VALUES (?, 'global', '/elsewhere.db', 9, 'same_topic')", (self.mid,))
        c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_content, dream_memory_id, "
                  "is_meaningful) VALUES ('divergent', ?, ?, ?, ?, 1)", (self.mid, self.other, SENTENCE, self.dream))
        c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_content, dream_memory_id, "
                  "is_meaningful) VALUES ('divergent', ?, ?, ?, ?, 1)", (self.other, self.other, CONTENT, self.mid))
        c.execute("INSERT INTO memory_provenance (derived_memory_id, source_memory_id, transformation_type, agent) "
                  "VALUES (?, ?, 'consolidation', 'test')", (self.consolidated, self.mid))
        c.execute("INSERT INTO resurfacing_log (memory_id, reason) VALUES (?, 'foundation_score 0.90')", (self.mid,))
        c.execute("INSERT INTO self_eval_reports (month, health_scorecard, proposed_enhancements, report_memory_id) "
                  "VALUES ('2026-01', '{}', '[]', ?)", (self.mid,))
        report = c.execute("SELECT id FROM self_eval_reports").fetchone()[0]
        c.execute("INSERT INTO self_eval_enacted_proposals (report_id, proposal_title, proposal_json, enacted_memory_id) "
                  "VALUES (?, 'keep this title', '{}', ?)", (report, self.mid))
        c.execute("INSERT INTO whisper_digests (digest_memory_id, observations_count, period_start, period_end) "
                  "VALUES (?, 3, '2026-01-01', '2026-01-07')", (self.mid,))
        c.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, 'keepsake')", (self.mid,))
        c.execute("INSERT INTO access_log (memory_id, access_type, query_text) VALUES (?, 'recall', 'gauge')", (self.mid,))
        c.execute("INSERT INTO belief_scan_queue (memory_id) VALUES (?)", (self.mid,))
        # Copies in tables with no foreign key at all: whole, cut short, and escaped inside JSON.
        quoted = CONTENT.replace("Opening line", 'Opening "line')
        c.execute("INSERT INTO lexical_miss_log (query, ts, fts_hits) VALUES (?, '2026-01-01', 0)", (quoted[:90],))
        c.execute("UPDATE memories SET content = ? WHERE id = ?", (quoted, self.mid))
        c.execute("INSERT INTO dream_phase_log (phase_name, status, result_json) VALUES ('p', 'ok', ?)",
                  (json.dumps({"note": quoted}),))
        c.commit()
        self.quoted = quoted

    def memory(self, content, summary):
        cur = self.conn.execute("INSERT INTO memories (content, summary, category, importance) "
                                "VALUES (?, ?, 'insight', 0.5)", (content, summary))
        return cur.lastrowid

    def delete(self, env=GATE):
        with mock.patch.dict(os.environ, env):
            return self.verb("delete", "--id", str(self.mid), "--hard")

    def count(self, sql, *args):
        return self.conn.execute(sql, args).fetchone()[0]

    def cells_holding(self, needle):
        """Every (table, column) with `needle` in a cell, full-text storage included."""
        found = []
        for (table,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            if table.startswith(("memory_embeddings", "sqlite_")) or table == "memories_fts":
                continue
            cols = [r[1] for r in self.conn.execute(f'PRAGMA table_info("{table}")')]
            for col in cols:
                for (cell,) in self.conn.execute(f'SELECT "{col}" FROM "{table}"'):
                    data = cell if isinstance(cell, bytes) else str(cell).encode("utf-8", "replace")
                    if needle.encode() in data:
                        found.append(f"{table}.{col}")
        return found


class HardDeleteFinishes(_Brain):
    def test_control_every_foreign_key_is_settled_and_the_memory_is_gone(self):
        rc, out = self.delete()
        self.assertEqual((rc, out["hard_delete"]), (0, True), out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM memories WHERE id = ?", self.mid), 0)
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        removed = out["receipt"]["rows_removed"]
        self.assertTrue(NOT_NULL_FKS <= set(removed), removed)
        self.assertEqual((removed["dream_log"], removed["consolidation_sources"]), (2, 1), removed)
        self.assertEqual(self.count("SELECT COUNT(*) FROM consolidation_sources WHERE source_memory_id = ?", self.other), 1)

    def test_control_a_row_others_hang_from_survives_with_the_reference_nulled(self):
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        for table, column in (("self_eval_reports", "report_memory_id"), ("whisper_digests", "digest_memory_id"),
                              ("self_eval_enacted_proposals", "enacted_memory_id")):
            self.assertEqual(self.count(f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL"), 1, table)
            self.assertEqual(out["receipt"]["references_cleared"][table], 1, out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM self_eval_enacted_proposals WHERE proposal_title = "
                                    "'keep this title'"), 1)

    def test_saboteur_the_deleted_text_survives_in_no_table(self):
        self.assertTrue(self.cells_holding(WORD), "the fixture must hold the text before the delete")
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.cells_holding(WORD), [])
        self.assertEqual(self.cells_holding(SENTENCE[:47]), [])
        self.assertEqual(self.cells_holding(CONTENT[:47]), [])

    def test_saboteur_the_deleted_text_survives_on_no_page_of_the_file(self):
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        self.conn.close()
        self.conn = self.open()
        self.assertTrue(out["receipt"]["wal_emptied"], out)
        for name in os.listdir(self.d):
            if name.startswith("claude_memory.db"):
                with open(self.d / name, "rb") as f:
                    self.assertNotIn(WORD.encode(), f.read(), name)

    def test_saboteur_the_keyword_index_no_longer_finds_it(self):
        self.assertGreater(self.count("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?", WORD), 0)
        self.delete()
        self.assertEqual(self.count("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?", WORD), 0)

    def test_control_what_the_memory_was_written_from_keeps_its_own_words(self):
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        kept = self.conn.execute("SELECT content FROM memories WHERE id = ?", (self.consolidated,)).fetchone()[0]
        self.assertIn("Distilled from 2 episodic memories", kept)
        self.assertIn("Supporting: harbour", kept)
        self.assertIn("[removed]", kept)
        self.assertEqual(out["receipt"]["rows_redacted"]["memories"], 2, out)

    def test_saboteur_the_receipt_is_counts_and_no_content(self):
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        text = json.dumps(out)
        for needle in (WORD, "manifold", "lantern", "harbour"):
            self.assertNotIn(needle, text)
        for key in ("rows_removed", "references_cleared", "rows_redacted"):
            self.assertTrue(all(isinstance(v, int) for v in out["receipt"][key].values()), key)

    def test_saboteur_a_failure_part_way_rolls_everything_back(self):
        self.conn.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON memories BEGIN SELECT RAISE(ABORT, 'planted'); END")
        self.conn.commit()
        with self.assertRaises(sqlite3.Error):
            self.delete()
        self.assertEqual(self.count("SELECT COUNT(*) FROM memories WHERE id = ?", self.mid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM dream_log"), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM self_eval_reports WHERE report_memory_id = ?", self.mid), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM counterfactual_decisions"), 1)

    def test_control_without_the_gate_it_is_a_soft_archive_and_every_log_stays(self):
        env = {k: v for k, v in os.environ.items() if k != "VOIDLOOM_ALLOW_HARD_DELETE"}
        with mock.patch.dict(os.environ, env, clear=True):
            rc, out = self.verb("delete", "--id", str(self.mid), "--hard")
        self.assertEqual((rc, out["hard_delete"], self.is_archived(self.mid)), (0, False, 1), out)
        self.assertNotIn("receipt", out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM dream_log"), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM memory_tags WHERE memory_id = ?", self.mid), 1)
        self.assertIn("memories.content", self.cells_holding(WORD))

    def test_control_a_soft_delete_is_unchanged(self):
        rc, out = self.delete_soft()
        self.assertEqual((rc, out["hard_delete"], self.is_archived(self.mid)), (0, False, 1), out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM resurfacing_log"), 1)

    def delete_soft(self):
        with mock.patch.dict(os.environ, GATE):
            return self.verb("delete", "--id", str(self.mid))


class ADifferentMemoryIsNotEdited(_Brain):
    def test_control_a_separate_memory_saying_the_same_thing_is_counted_and_left(self):
        twin = self.memory(self.quoted, "a note someone else kept")
        self.conn.commit()
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.conn.execute("SELECT content FROM memories WHERE id = ?", (twin,)).fetchone()[0], self.quoted)
        self.assertEqual(out["receipt"]["other_memories_sharing_text"], 1, out)


class ACopyInsideJson(_Brain):
    LINES = "\n".join(f"line {i} of the {WORD} ledger, short" for i in range(12))

    def test_saboteur_escapes_every_few_words_do_not_hide_the_text(self):
        """Each newline becomes two characters in JSON, so no run of the plain text reaches a copy's length."""
        self.conn.execute("UPDATE memories SET content = ? WHERE id = ?", (self.LINES, self.mid))
        self.conn.execute("INSERT INTO dream_phase_log (phase_name, status, result_json) VALUES ('j', 'ok', ?)",
                          (json.dumps({"note": self.LINES}),))
        self.conn.commit()
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM dream_phase_log WHERE phase_name = 'j' AND result_json LIKE ?",
                                    f"%{WORD}%"), 0)


class ASharedLabelIsNotACopy(_Brain):
    TAG = "north-manifold-maintenance-log-for-the-lantern-room-keeper"

    def test_saboteur_a_tag_the_text_mentions_stays_on_every_other_memory(self):
        self.conn.execute("UPDATE memories SET content = content || ? WHERE id = ?", (f" Filed under {self.TAG}.", self.mid))
        self.conn.execute("INSERT INTO memory_tags (memory_id, tag) VALUES (?, ?)", (self.other, self.TAG))
        self.conn.commit()
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM memory_tags WHERE memory_id = ? AND tag = ?", self.other, self.TAG), 1)

    def test_saboteur_a_phrase_shorter_than_a_copy_stays_in_a_log(self):
        phrase = SENTENCE[4:34]
        self.conn.execute("INSERT INTO lexical_miss_log (query, ts, fts_hits) VALUES (?, '2026-01-02', 0)", (phrase,))
        self.conn.commit()
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.count("SELECT COUNT(*) FROM lexical_miss_log WHERE query = ?", phrase), 1)


class TheVectorsGoToo(_Brain):
    def test_control_every_tier_and_every_stamp(self):
        for table, width in ((NOMIC, 768), (JINA, 1024), (QWEN, 1024)):
            self.conn.execute(f"INSERT INTO {table} (memory_id, embedding) VALUES (?, ?)", (self.mid, _unit(width, (0, 1.0))))
        self.conn.commit()
        rc, out = self.delete()
        self.assertEqual(rc, 0, out)
        for table in (NOMIC, JINA, QWEN):
            self.assertEqual(self.count(f"SELECT COUNT(*) FROM {table} WHERE memory_id = ?", self.mid), 0, table)
            self.assertEqual(out["receipt"]["rows_removed"][table], 1, out)
