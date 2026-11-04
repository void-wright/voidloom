"""`voidloom doctor`: the brain's verdict comes from the brain's own checks, read through the provenance reader.

Real brains (every migration in this tree), real locks, the real verb. The hook join is the one thing stubbed, so a
host fact can be planted beside a sound brain. A brain with no lock is legal and must read healthy; the same brain
under a lock is held to what the lock promised. Legacy rows are a declared state and never decide the verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))
sys.path.insert(0, str(ROOT))

import embedder_lock as el  # noqa: E402
import memory_write as mw  # noqa: E402
import schema_migrator  # noqa: E402
from voidloom import cli  # noqa: E402

MIGRATIONS = ROOT / "migrations"
FOREIGN = "b" * 64
NOMIC, NOMIC_DIM = "memory_embeddings", 768
JINA, JINA_DIM = "memory_embeddings_v2", 1024
LONG_AGO = "2020-01-01 00:00:00"


def _report_double(conn):
    """The report's archive signature, for a branch that does not carry it yet."""
    return {"lock_weights_hash": None, "basis": "double", "tables": {}, "stale_derived": 0, "unstamped": 0,
            "current": 0, "total": 0, "unmeasured": [], "line": "derived layer: 0 stale"}


def setUpModule():
    if not hasattr(mw, "derived_layer_report"):
        mw.derived_layer_report = _report_double


def tearDownModule():
    if getattr(mw, "derived_layer_report", None) is _report_double:
        del mw.derived_layer_report


class _Brain(unittest.TestCase):
    LOCK = "cpu"
    TABLE, DIM = NOMIC, NOMIC_DIM

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), MIGRATIONS)
        self.assertEqual(out.get("failed") or [], [], out)
        self.lock = el.write_lock(self.d, self.LOCK) if self.LOCK else None
        self.h = self.lock["weights_hash"] if self.lock else None
        self.conn = self.connect()
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {NOMIC} USING vec0("
                          f"memory_id INTEGER PRIMARY KEY, embedding FLOAT[{NOMIC_DIM}])")
        self.conn.execute("DELETE FROM metadata WHERE key LIKE 'active_embedding%'")
        if not self.LOCK:
            self.declare(self.TABLE, self.DIM)
        self.conn.commit()
        self._env = {k: os.environ.pop(k, None) for k in ("VOIDLOOM_TIER0_ONLY",)}

    def declare(self, table, dim):
        self.conn.executemany("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
                              [("active_embedding_table", table), ("active_embedding_dim", str(dim))])

    def connect(self):
        import sqlite_vec
        c = sqlite3.connect(str(self.db))
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
        return c

    def tearDown(self):
        self.conn.close()
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
        self.tmp.cleanup()

    def memory(self, text="a thing", *, whash=None, vector=True, at=None, model=None):
        cur = self.conn.execute("INSERT INTO memories (content, summary, category, importance) "
                                "VALUES (?, 's', 'insight', 0.5)", (text,))
        mid = int(cur.lastrowid)
        if at:
            self.conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (at, mid))
        if vector:
            self.conn.execute(f"INSERT INTO {self.TABLE} (memory_id, embedding) VALUES (?, ?)",
                              (mid, bytes(4 * self.DIM)))
        if whash:
            self.stamp(mid, whash, model=model)
        self.conn.commit()
        return mid

    def stamp(self, mid, whash, *, model=None, table=None, dim=None):
        ident = mw.model_identity(self.TABLE)
        self.conn.execute(
            f"INSERT OR REPLACE INTO {mw.PROVENANCE_TABLE} ({', '.join(mw.PROVENANCE_COLUMNS)}) "
            "VALUES (?, ?, ?, 'ref', ?, ?, ?, '2026-01-01T00:00:00+00:00', 'test', 'text', NULL)",
            (mid, table or self.TABLE, model or ident["model_id"], whash, dim or self.DIM, "d" * 64))
        self.conn.commit()

    def doctor(self, host_warning=None):
        plats = {"claude": {"state": "NOT_INSTALLED", "ours": [], "config_warning": host_warning}}
        with mock.patch.object(cli, "doctor_hook_join", return_value={"platforms": plats}):
            env = cli.verb_doctor(argparse.Namespace(db=str(self.db), cwd=None, platform=None, probe_platform=False))
        self.assertEqual(env["status"], "ok", env)
        return env["data"]

    def coherence(self, data=None):
        return (data or self.doctor())["checks"]["embedding_coherence"]


class TheHostDoesNotDecideTheBrain(_Brain):
    def test_control_a_sound_brain_on_a_bad_host_reads_two_answers(self):
        self.memory(whash=self.h)
        d = self.doctor(host_warning="settings.local.json: 0 bytes, not JSON")
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertIs(d["host_healthy"], False)
        self.assertIsNone(d["reason"])
        self.assertEqual(len(d["warnings"]), 1)

    def test_saboteur_a_bad_brain_on_a_sound_host_reads_two_answers(self):
        self.memory(whash=self.h)
        (self.d / "embedder.lock.json").write_text("{ not json", encoding="utf-8")
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIs(d["host_healthy"], True)
        self.assertIn("LOCK_MALFORMED", d["reason"])

    def test_control_a_real_settings_file_that_will_not_parse_reaches_host_healthy_only(self):
        """No stub: the verb in a child, under a HOME whose hook settings are not JSON. (A 0-byte file is {} now.)"""
        import subprocess
        self.memory(whash=self.h)
        home = self.d / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "settings.local.json").write_text("not json", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k not in ("WIZARD_LIBRARY_DB", "CLAUDE_MEMORY_DB")}
        env.update({"HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": "none"})
        code = ("import sys, json\n"
                f"sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(ROOT / 'organs')!r})\n"
                "import memory_write as mw\n"
                "if not hasattr(mw, 'derived_layer_report'):\n"
                "    mw.derived_layer_report = lambda c: {'stale_derived': 0}\n"
                "from voidloom import cli\n"
                f"sys.exit(cli.dispatch(['doctor', '--db', {str(self.db)!r}, '--json']))\n")
        r = subprocess.run([sys.executable, "-c", code], cwd=str(self.d), env=env, capture_output=True, text=True,
                           timeout=120)
        data = json.loads(r.stdout)["data"]
        self.assertIs(data["host_healthy"], False, data["warnings"])
        self.assertIs(data["brain_healthy"], True, data["reason"])
        self.assertTrue(data["warnings"])

    def test_an_absent_brain_says_so(self):
        self.conn.close()
        self.db.unlink()
        self.conn = sqlite3.connect(":memory:")
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("no brain at", d["reason"])


class LockedBrain(_Brain):
    def test_control_clean_reads_the_readers_figures(self):
        self.memory(whash=self.h)
        self.memory(whash=self.h)
        self.memory(vector=False)
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertIsNone(d["reason"])
        self.assertEqual((coh["coverage"]["matched"], coh["coverage"]["total"]), (2, 3))
        self.assertEqual(coh["lock"]["weights_hash"], self.h)
        self.assertEqual(coh["lock_path"], str(self.d / "embedder.lock.json"))
        self.assertIs(coh["shipped"], True)
        self.assertIs(coh["lockless"], False)
        self.assertEqual(coh["catalogue_hash"], el.catalogue_sha256())
        self.assertIn(coh["snapshot_present"], (True, False))
        self.assertIs(coh["agrees"], True)
        self.assertEqual(coh["observed_dim"], NOMIC_DIM)
        self.assertIn("1 of 3", coh["coverage_note"])

    def test_saboteur_an_unstamped_vector_written_under_the_lock(self):
        self.memory(whash=self.h)
        self.memory()
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("1 vector(s) without a stamp", d["reason"])
        self.assertIs(coh["agrees"], False)
        self.assertEqual(coh["provenance_defects"], {"vectors_without_stamp": 1})
        self.assertEqual(coh["coverage"]["matched"], 2, "the row is legacy to the reader; only the verdict is stricter")

    def test_control_legacy_rows_are_declared_and_searchable_not_defects(self):
        self.memory(whash=self.h)
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED)
        self.memory(at=LONG_AGO)
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertEqual(coh["coverage"]["matched"], 3)
        self.assertEqual(coh["coverage"]["legacy_unverified"]["legacy"], 2)
        self.assertIn("legacy_unverified: 2 of 3", coh["coverage_note"])
        self.assertNotIn("provenance_defects", coh)

    def test_saboteur_a_lock_with_no_readable_time_counts_every_unstamped_row(self):
        self.memory(whash=self.h)
        self.memory(at=LONG_AGO)
        lock = json.loads((self.d / "embedder.lock.json").read_text(encoding="utf-8"))
        lock["locked_at"] = "whenever"
        (self.d / "embedder.lock.json").write_text(json.dumps(lock), encoding="utf-8")
        self.assertIs(self.doctor()["brain_healthy"], False)

    def test_control_a_brain_of_nothing_but_legacy_rows_is_healthy(self):
        for _ in range(3):
            self.memory(at=LONG_AGO)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertEqual(self.coherence(d)["coverage"]["legacy_unverified"]["legacy"], 3)

    def test_saboteur_a_stamp_under_another_model(self):
        self.memory(whash=self.h)
        self.memory(whash=FOREIGN)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("1 under another model", d["reason"])
        self.assertEqual(self.coherence(d)["coverage"]["matched"], 1)

    def test_saboteur_a_stamp_without_a_vector(self):
        self.memory(whash=self.h)
        self.memory(whash=self.h, vector=False)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("1 stamp(s) without a vector", d["reason"])

    def test_saboteur_every_vector_under_another_model_is_named_by_the_refusal(self):
        self.memory(whash=FOREIGN)
        self.memory(whash=FOREIGN)
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("NO_ROWS_FOR_LOCK", d["reason"])
        self.assertEqual(coh["coverage"]["matched"], 0)
        self.assertEqual(coh["present_hashes"], {FOREIGN: 2})

    def test_saboteur_a_row_count_cannot_read_as_coverage(self):
        """The row-counting doctor saw 2 vectors for 2 memories and printed no note at all."""
        self.memory(whash=FOREIGN)
        self.memory(whash=FOREIGN)
        coh = self.coherence()
        self.assertNotIn("vectors_in_active_table", coh)
        self.assertEqual((coh["coverage"]["matched"], coh["coverage"]["total"]), (0, 2))

    def test_saboteur_the_lock_checksum_on_another_table(self):
        mid = self.memory(whash=self.h)
        self.stamp(mid, self.h, table="memory_embeddings_v3", dim=1024)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("STAMP_INCONSISTENT", d["reason"])

    def _unship(self):
        lock = json.loads((self.d / "embedder.lock.json").read_text(encoding="utf-8"))
        lock["weights_hash"] = "c" * 64
        (self.d / "embedder.lock.json").write_text(json.dumps(lock), encoding="utf-8")
        return lock

    def test_saboteur_a_lock_naming_a_checksum_the_catalogue_does_not_ship(self):
        lock = self._unship()
        self.memory(whash=lock["weights_hash"])
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(coh["shipped"], False)
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("LOCK_NOT_SHIPPED", d["reason"])

    def test_saboteur_an_unshipped_lock_on_an_empty_brain(self):
        self._unship()
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("LOCK_NOT_SHIPPED", d["reason"])

    def test_saboteur_a_malformed_lock_still_carries_coverage_and_the_derived_count(self):
        self.memory(whash=self.h)
        (self.d / "embedder.lock.json").write_text("{ not json", encoding="utf-8")
        coh = self.coherence()
        self.assertEqual(coh["refusal"]["error"], "LOCK_MALFORMED")
        self.assertEqual((coh["coverage"]["matched"], coh["coverage"]["total"]), (0, 1))
        self.assertIn("stale_derived", coh["derived_layer"])

    def test_saboteur_a_lock_that_is_there_but_unreadable_is_not_a_lockless_brain(self):
        self.memory(whash=self.h)
        lock = self.d / "embedder.lock.json"
        os.chmod(lock, 0)
        try:
            d = self.doctor()
        finally:
            os.chmod(lock, 0o600)
        self.assertIs(self.coherence(d)["lockless"], False)
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("embedder lock", d["reason"])

    def test_control_an_empty_brain_is_healthy_and_says_nothing_was_compared(self):
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertIsNone(coh["agrees"])
        self.assertIn("no vector to measure", coh["reason"])

    def test_control_the_derived_count_ships_on_a_healthy_brain(self):
        self.memory(whash=self.h)
        self.assertIn("stale_derived", self.coherence()["derived_layer"])

    def test_saboteur_a_file_that_is_no_database_is_not_healthy(self):
        self.conn.close()
        self.db.write_bytes(b"not a database" * 100)
        self.conn = sqlite3.connect(":memory:")
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertTrue(d["reason"])

    def test_control_a_path_with_uri_characters_opens(self):
        self.memory(whash=self.h)
        self.conn.close()
        d = self.d / "a dir #1?x"
        d.mkdir()
        db = d / "claude_memory.db"
        db.write_bytes(self.db.read_bytes())
        el.write_lock(d, "cpu")
        self.conn = self.connect()
        coh = cli._embedding_coherence(str(db))
        self.assertEqual(coh["coverage"]["matched"], 1, coh)


class TheWidthIsReadFromTheSearchSet(_Brain):
    """vec0 fixes its width, so the probe's choice of row only shows on a plain table."""

    def setUp(self):
        super().setUp()
        self.conn.execute(f"DROP TABLE {self.TABLE}")
        self.conn.execute(f"CREATE TABLE {self.TABLE} (memory_id INTEGER PRIMARY KEY, embedding BLOB)")
        self.conn.commit()

    def wide(self, mid, dim):
        self.conn.execute(f"UPDATE {self.TABLE} SET embedding = ? WHERE memory_id = ?", (bytes(4 * dim), mid))
        self.conn.commit()

    def test_saboteur_a_stale_row_outside_the_set_does_not_set_the_width(self):
        self.wide(self.memory(whash=FOREIGN), 1024)
        self.memory(whash=self.h)
        coh = self.coherence()
        self.assertEqual(coh["observed_dim"], NOMIC_DIM, "the first row in the table is outside the search set")

    def test_saboteur_a_wrong_width_inside_the_set_is_a_fault(self):
        self.wide(self.memory(whash=self.h), 1024)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("1024-dim", d["reason"])


class NoLock(_Brain):
    """A jina brain never touches the lock path, and a pre-lock brain is legal until `init` pins it."""
    LOCK = None
    TABLE, DIM = JINA, JINA_DIM

    def test_control_the_shape_of_a_long_lived_jina_brain_reads_healthy(self):
        """Measured on a copy of a real one: stamps all 'unresolved', most vectors unstamped."""
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED)
        self.memory()
        self.memory()
        self.memory(vector=False)
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertIsNone(d["reason"])
        self.assertIs(coh["lockless"], True)
        self.assertIsNone(coh["lock"])
        self.assertNotIn("refusal", coh)
        self.assertEqual(coh["coverage"]["basis"], "lockless")
        self.assertEqual(coh["coverage"]["vectors_without_stamp"], 2)
        self.assertEqual(coh["table_source"], "metadata")
        self.assertIs(coh["agrees"], True)
        self.assertIn("whole of memory_embeddings_v2 is searched", coh["coverage_note"])

    def test_control_a_table_with_no_stamps_at_all_is_legal(self):
        self.memory()
        d = self.doctor()
        coh = self.coherence(d)
        self.assertEqual(coh["coverage"]["error"], "NO_ROWS_FOR_MODEL")
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertNotIn("coverage_refusal", coh)

    def test_saboteur_an_orphan_stamp_is_still_a_defect(self):
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED)
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED, vector=False)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("1 stamp(s) without a vector", d["reason"])

    def test_saboteur_another_models_stamp_is_still_a_defect(self):
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED)
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED, model="someone/else")
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("1 under another model", d["reason"])

    def test_saboteur_nothing_but_another_models_stamps_is_refused(self):
        self.memory(whash=mw.WEIGHTS_HASH_UNRESOLVED, model="someone/else")
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("NO_ROWS_FOR_MODEL", d["reason"])


class NoLockNoTier(_Brain):
    LOCK = None

    def setUp(self):
        super().setUp()
        self.conn.execute("DELETE FROM metadata WHERE key LIKE 'active_embedding%'")
        self.conn.commit()

    def test_control_an_empty_shell_is_not_a_fault(self):
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])

    def test_control_a_tier_0_brain_with_memories_is_a_working_brain(self):
        """What a seeded Tier-0 install holds: memories, no lock, no recorded tier, no vectors. Keyword recall works."""
        self.memory(vector=False)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertIn("keyword-only", self.coherence(d)["coverage_note"])

    def test_saboteur_vectors_stranded_by_a_lost_tier_are_a_fault(self):
        self.TABLE, self.DIM = JINA, JINA_DIM
        self.memory()
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("provenance reader", d["reason"])
        self.assertEqual(coh["stranded_vectors"], [JINA])

    def test_control_keyword_only_is_its_whole_promise(self):
        self.memory(vector=False)
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        self.conn.commit()
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertIs(self.coherence(d)["tier0_only"], True)


class KeywordOnlyUnderALock(_Brain):
    def test_control_no_vectors_on_a_declared_keyword_brain(self):
        self.memory(vector=False)
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        self.conn.commit()
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])

    def test_saboteur_the_same_brain_undeclared_refuses(self):
        self.memory(vector=False)
        d = self.doctor()
        self.assertIs(d["brain_healthy"], False)
        self.assertIn("NO_ROWS_FOR_LOCK", d["reason"])

    def test_saboteur_a_keyword_brain_holding_foreign_vectors_still_refuses(self):
        self.memory(whash=FOREIGN)
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        self.conn.commit()
        self.assertIs(self.doctor()["brain_healthy"], False)


class ReaderShape(unittest.TestCase):
    def test_the_verdicts_reports_take_no_per_row_exists(self):
        import inspect
        import re
        for fn in (cli._tier_coherence, cli._unstamped_since_lock, cli._provenance_defects, cli._derived_layer):
            self.assertIsNone(re.search(r"\bEXISTS\s*\(", inspect.getsource(fn)), fn.__name__)


if __name__ == "__main__":
    unittest.main()
