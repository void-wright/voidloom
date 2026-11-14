"""A fault is a typed answer with a way out, on every surface, and `doctor` reads it.

Each class below drives a real engine or CLI process over a real schema, in a scratch HOME: an absent or half-downloaded
model, a brain that lost its lock, a brain that lost a table, a foreign env directory.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
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

try:
    import sqlite_vec  # noqa: F401
    HAVE_VEC = True
except Exception:
    HAVE_VEC = False


class _Room(unittest.TestCase):
    """A laid brain directory: the engine finds its database beside its own organs."""
    LOCK = "cpu"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = Path(self.tmp.name).resolve()
        for name in ("organs", "migrations", "voidloom", "install"):
            (self.d / name).symlink_to(ROOT / name)
        self.db = self.d / "claude_memory.db"
        schema_migrator.apply(str(self.db), ROOT / "migrations")
        c = schema_migrator.connect(str(self.db))
        c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768])")
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings')")
        c.execute("INSERT INTO memories (content, summary, category, importance) VALUES ('the lantern room keeper', 's', 'insight', 0.5)")
        c.commit()
        c.close()
        if self.LOCK:
            el.write_lock(self.d, self.LOCK)

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TRANSFORMERS_", "WIZARD_", "VOIDLOOM_"))}
        env.update(HOME=str(self.d), HF_HUB_CACHE=str(self.d / "hub"), WIZARD_ACCELERATOR="cpu", WIZARD_LIBRARY_DB=str(self.db),
                   PYTHONDONTWRITEBYTECODE="1", **extra)
        return env

    def engine(self, *argv, **extra):
        p = subprocess.run([sys.executable, str(self.d / "organs" / "claude_memory.py"), *argv], capture_output=True, text=True,
                           timeout=240, env=self.env(**extra), cwd=str(self.d))
        lines = [ln for ln in p.stdout.strip().splitlines() if ln.startswith("{")]
        return (json.loads(lines[-1]) if lines else {}), p

    def cli(self, *argv, **extra):
        return subprocess.run([sys.executable, "-m", "voidloom", *argv], capture_output=True, text=True, timeout=240,
                              env=self.env(PYTHONPATH=str(ROOT), **extra), cwd=str(self.d))

    def cli_json(self, *argv, **extra):
        p = self.cli(*argv, "--json", **extra)
        return json.loads(p.stdout), p


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class OnlyInstallAndTierVerbsFetch(_Room):
    def test_a_store_refuses_snapshot_absent_and_prints_a_runnable_fix(self):
        out, p = self.engine("store", "--content", "a new fact worth keeping", "--category", "insight", "--summary", "s")
        self.assertEqual(out.get("error"), "SNAPSHOT_ABSENT", (out, p.stderr[-500:]))
        self.assertIn("only an install", out["message"])
        self.assertIn("model_fetch.py --db", out["retry"][0])
        self.assertFalse((self.d / "hub").exists() and any((self.d / "hub").iterdir()), "nothing was fetched")

    def test_the_front_door_store_refuses_too_and_does_not_keyword_store(self):
        env, p = self.cli_json("store", "--content", "a new fact worth keeping", "--category", "insight")
        self.assertIn("SNAPSHOT_ABSENT", json.dumps(env), p.stderr[-500:])
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
        c.close()


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class ABrainWithNoLockFetchesNothingEither(_Room):
    LOCK = None

    def test_a_store_refuses_typed_and_nothing_lands_in_the_cache(self):
        out, p = self.engine("store", "--content", "a new fact worth keeping", "--category", "insight", "--summary", "s")
        self.assertEqual(out.get("error"), "SNAPSHOT_ABSENT", (out, p.stderr[-500:]))
        self.assertFalse((self.d / "hub").exists() and any((self.d / "hub").iterdir()))

    def test_a_keyword_store_stands_at_the_front_door_with_no_lock_promised(self):
        env, p = self.cli_json("store", "--content", "a new fact worth keeping", "--category", "insight")
        self.assertEqual(env["status"], "ok", (env, p.stderr[-400:]))
        self.assertEqual(env["data"].get("tier"), 0, env)


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class AHalfDownloadedModel(_Room):
    def setUp(self):
        super().setUp()
        lock = el.read_lock(self.d / "embedder.lock.json")
        repo = "models--" + lock["model_id"].replace("/", "--")
        self.snap = self.d / "hub" / repo / "snapshots" / lock["revision"]
        self.snap.mkdir(parents=True)
        (self.d / "hub" / repo / "refs").mkdir()
        (self.snap / "config.json").write_text("{}")
        self.lock = lock

    def test_the_loader_says_snapshot_absent_not_a_changed_byte(self):
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(self.d / "hub")}):
            with self.assertRaises(el.EmbedderLockError) as cm:
                el.verify_snapshot_against_lock(self.lock)
        self.assertEqual(cm.exception.code, "SNAPSHOT_ABSENT")
        self.assertIn("interrupted", str(cm.exception))

    def test_recall_degrades_to_keyword_and_says_why(self):
        env, p = self.cli_json("recall", "--query", "lantern keeper")
        self.assertEqual(env["data"]["tier"], 0, env)
        self.assertIn("SNAPSHOT_ABSENT", json.dumps(env["data"]))

    def test_doctor_is_not_healthy_and_names_the_fetch(self):
        env, p = self.cli_json("doctor")
        self.assertFalse(env["data"]["brain_healthy"])
        self.assertIn("incomplete", env["data"]["reason"])
        self.assertIn("model_fetch.py", env["data"]["reason"])
        self.assertEqual(p.returncode, 4)
        self.assertFalse(env["data"]["checks"]["models_present"])

    def test_a_fetch_completes_it_rather_than_taking_the_directory_as_present(self):
        calls = []
        import huggingface_hub
        online = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
        with mock.patch.dict(os.environ, {**online, "HF_HUB_CACHE": str(self.d / "hub")}, clear=True), \
                mock.patch.object(huggingface_hub, "snapshot_download", side_effect=lambda *a, **k: calls.append(k) or None):
            with self.assertRaises(el.EmbedderLockError), el.fetch_allowed():
                el.verify_snapshot_against_lock(self.lock)
        self.assertEqual(len(calls), 1, "the partial snapshot went to the download")


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class ALostLock(_Room):
    def test_saboteur_recall_refuses_on_every_read_surface_with_the_way_out(self):
        (self.d / "embedder.lock.json").unlink()
        out, p = self.engine("recall", "--query", "lantern")
        self.assertEqual((p.returncode, out.get("status"), out.get("error")), (1, "error", "LOCK_MISSING"), (out, p.stderr[-400:]))
        self.assertNotIn("results", out)
        self.assertIn("matched", out["coverage"], out)
        self.assertIn("init --tier cpu", out["retry"][0])
        env, p = self.cli_json("recall", "--query", "lantern")
        self.assertEqual((p.returncode, env["code"]), (1, "LOCK_MISSING"), env)
        self.assertIn("coverage", env["data"]["detail"], env)
        env, p = self.cli_json("batch-recall", "--items", '["lantern"]')
        self.assertEqual((p.returncode, env["code"]), (4, "batch_recall_incomplete"), env)
        item = env["data"]["results"][0]
        self.assertEqual((item["status"], item["code"]), ("error", "LOCK_MISSING"), item)
        self.assertIn("matched", item["coverage"], item)
        self.assertIn("init --tier cpu", self.cli("recall", "--query", "lantern").stdout)

    def test_saboteur_anticipate_refuses_as_it_does_for_a_malformed_lock(self):
        (self.d / "embedder.lock.json").unlink()
        out, p = self.engine("anticipate", "--top-k", "3")
        self.assertEqual((p.returncode, out.get("error")), (1, "LOCK_MISSING"), (out, p.stderr[-400:]))
        self.assertIn("coverage", out, out)

    def test_control_the_floor_still_answers_by_keyword_and_says_so(self):
        (self.d / "embedder.lock.json").unlink()
        p = subprocess.run([sys.executable, str(self.d / "organs" / "claude_memory_lite.py"), "recall", "--query", "lantern"],
                           capture_output=True, text=True, timeout=120, env=self.env(), cwd=str(self.d))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertTrue(out["results"], out)
        self.assertEqual(out["degraded"]["reason"], "LOCK_MISSING", out)

    def test_a_store_refuses_lock_missing_with_the_fix(self):
        (self.d / "embedder.lock.json").unlink()
        out, p = self.engine("store", "--content", "a new fact worth keeping", "--category", "insight", "--summary", "s")
        self.assertEqual(out.get("error"), "LOCK_MISSING", (out, p.stderr[-400:]))
        self.assertIn("init --tier cpu", out["retry"][0])
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
        c.close()

    def test_lock_and_history_both_gone_is_seen_from_the_row_the_lock_left(self):
        c = sqlite3.connect(self.db)
        el.mark_brain(c, "cpu")
        c.commit(); c.close()
        (self.d / "embedder.lock.json").unlink()
        (self.d / "embedder.lock.history.jsonl").unlink()
        self.assertEqual(el.lost_lock(self.d), {"tier": "cpu"})
        env, p = self.cli_json("doctor")
        self.assertFalse(env["data"]["brain_healthy"], env["data"]["reason"])

    def test_control_a_stamp_under_a_shipped_model_with_no_record_is_a_legal_brain(self):
        whash = el.catalogue_tiers()["cpu"]["weights_hash"]
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, content_hash, "
                  "embedded_at, organ, input_kind) VALUES (1, 'memory_embeddings', 'm', 'r', ?, 768, ?, 't', 'o', 'text')", (whash, "c" * 64))
        c.execute("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
        c.commit(); c.close()
        (self.d / "embedder.lock.json").unlink()
        (self.d / "embedder.lock.history.jsonl").unlink()
        self.assertIsNone(el.lost_lock(self.d))


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class SchemaDamage(_Room):
    def plant(self, sql):
        c = schema_migrator.connect(str(self.db))
        c.execute(sql)
        c.commit(); c.close()

    def test_a_dropped_index_is_typed_on_every_writing_verb(self):
        self.plant("DROP TABLE memories_fts")
        for argv in (("update", "--id", "1", "--summary", "changed"), ("delete", "--id", "1")):
            out, p = self.engine(*argv)
            self.assertNotIn("Traceback", p.stderr, (argv, p.stderr[-500:]))
            self.assertEqual(out.get("error"), "SCHEMA_DAMAGED", (argv, out))
            self.assertIn("coverage", out)

    def test_the_front_door_does_not_call_a_damaged_brain_uninitialized(self):
        self.plant("DROP TABLE memories_fts")
        p = self.cli("recall", "--query", "lantern", "--json")
        self.assertNotIn("has no schema", p.stdout)
        self.assertNotIn("Traceback", p.stderr, p.stderr[-500:])

    def test_saboteur_the_front_door_refusal_carries_its_coverage(self):
        self.plant("DROP TABLE memories_fts")
        from voidloom import cli
        env = cli._db_uninitialized_error(str(self.db), verb="recall", exc=sqlite3.OperationalError("no such table: memories_fts"))
        self.assertEqual(env["code"], "SCHEMA_DAMAGED", env)
        self.assertIn("coverage", env["data"]["detail"], env)

    def test_a_dropped_vector_table_is_not_a_successful_sleep(self):
        self.plant("DROP TABLE memory_embeddings")
        out, p = self.engine("sleep")
        self.assertEqual(out.get("error"), "SCHEMA_DAMAGED", (out, p.stderr[-400:]))
        out, _p = self.engine("context")
        self.assertTrue(out.get("degraded"), out)
        self.assertEqual(out["schema_gaps"], ["memory_embeddings"])


class _Planted(_Room):
    def plant_vector(self, whash):
        c = schema_migrator.connect(str(self.db))
        c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (1, ?)", (bytes(4 * 768),))
        c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, content_hash, "
                  "embedded_at, organ, input_kind) VALUES (1, 'memory_embeddings', 'm', 'r', ?, 768, ?, 't', 'o', 'text')", (whash, "c" * 64))
        c.commit(); c.close()


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class RefusalsCarryCoverage(_Planted):
    def assert_refused(self, out, code, p, *way_out):
        """A refusal is the answer: typed, rc 1, no rows, its coverage measured, and a way out in its own words."""
        self.assertEqual((p.returncode, out.get("status"), out.get("error")), (1, "error", code), (out, p.stderr[-400:]))
        self.assertNotIn("results", out)
        self.assertIn("matched", out["coverage"], out)
        for words in way_out:
            self.assertIn(words, out["message"])

    def set_lock_hash(self, whash):
        lock = json.loads((self.d / "embedder.lock.json").read_text())
        lock["weights_hash"] = whash
        (self.d / "embedder.lock.json").write_text(json.dumps(lock))

    def test_the_way_out_of_a_malformed_lock_runs_where_it_is_printed(self):
        (self.d / "embedder.lock.json").write_text("{ not json")
        out, p = self.engine("init")
        self.assertEqual(out.get("error"), "LOCK_MALFORMED", (out, p.stderr[-400:]))
        fix = out["retry"][0]
        run = subprocess.run(fix, shell=True, cwd="/", capture_output=True, text=True, timeout=120,
                             env={**self.env(), "WIZARD_LIBRARY_DB": str(self.db)})
        self.assertEqual(run.returncode, 0, run.stdout[-400:] + run.stderr[-400:])
        self.assertEqual(json.loads((self.d / "embedder.lock.json").read_text())["tier"], "cpu")
        self.assertTrue((self.d / "embedder.lock.json.malformed").exists(), "the bad file is kept to read")

    def test_saboteur_every_verb_a_malformed_lock_stops_names_the_way_out(self):
        (self.d / "embedder.lock.json").write_text("{ not json")
        env, p = self.cli_json("tier", "set", "qwen")
        self.assertEqual(env["code"], "LOCK_MALFORMED", env)
        self.assertIn("mv ", env["message"])
        env, p = self.cli_json("doctor")
        self.assertIn("mv ", json.dumps(env["data"]["checks"]["embedding_coherence"]), env["data"]["reason"])
        self.assertIn("mv ", env["data"]["reason"], "the reason a person reads carries the way out")
        self.assertIn("LOCK_MALFORMED", env["data"]["reason"])

    def test_saboteur_init_leaves_the_row_a_deleted_history_cannot_take(self):
        (self.d / "embedder.lock.json").unlink()
        (self.d / "embedder.lock.history.jsonl").unlink()
        c = sqlite3.connect(self.db); c.execute("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,)); c.commit(); c.close()
        out, p = self.engine("init", "--tier", "cpu")
        self.assertEqual(out.get("status"), "ok", (out, p.stderr[-400:]))
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT value FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,)).fetchone()[0], "cpu")
        c.close()

    def test_the_front_door_names_a_typed_refusal(self):
        (self.d / "embedder.lock.json").unlink()
        env, p = self.cli_json("store", "--content", "after the lock went", "--category", "insight")
        self.assertEqual(env["code"], "LOCK_MISSING", env)

    def test_saboteur_a_lock_no_row_answers_to_refuses_on_every_read_surface(self):
        """The check a person can run: sixty-four `f`s in the lock, a well-formed hash no row carries."""
        self.plant_vector(el.catalogue_tiers()["cpu"]["weights_hash"])
        self.set_lock_hash("f" * 64)
        out, p = self.engine("recall", "--query", "lantern")
        self.assert_refused(out, "NO_ROWS_FOR_LOCK", p, "re-embed")
        env, p = self.cli_json("recall", "--query", "lantern")
        self.assertEqual((p.returncode, env["code"]), (1, "NO_ROWS_FOR_LOCK"), env)
        self.assertIn("coverage", env["data"]["detail"], env)
        env, p = self.cli_json("batch-recall", "--items", '["lantern"]')
        self.assertEqual((p.returncode, env["code"]), (4, "batch_recall_incomplete"), env)
        item = env["data"]["results"][0]
        self.assertEqual((item["status"], item["code"]), ("error", "NO_ROWS_FOR_LOCK"), item)
        self.assertIn("matched", item["coverage"], item)

    def test_saboteur_a_person_reads_the_refusal_and_its_way_out(self):
        (self.d / "embedder.lock.json").write_text("{ not json")
        for argv in (("recall", "--query", "lantern"), ("batch-recall", "--items", '["lantern"]')):
            p = self.cli(*argv)
            self.assertIn("LOCK_MALFORMED", p.stdout, p.stdout + p.stderr)
            self.assertIn("mv ", p.stdout, p.stdout + p.stderr)
            self.assertNotIn("[ok]", p.stdout)

    def test_saboteur_a_malformed_lock_refuses_and_prints_its_way_out(self):
        (self.d / "embedder.lock.json").write_text("{ not json")
        out, p = self.engine("recall", "--query", "lantern")
        self.assert_refused(out, "LOCK_MALFORMED", p, "mv ")
        self.assertTrue(out["retry"], out)
        env, p = self.cli_json("recall", "--query", "lantern")
        self.assertEqual((p.returncode, env["code"]), (1, "LOCK_MALFORMED"), env)
        self.assertIn("coverage", env["data"]["detail"], "the front door carries what the engine's door does")
        self.assertIn("matched", env["data"]["detail"]["coverage"], env)

    def test_saboteur_a_hash_the_catalogue_does_not_ship_refuses(self):
        self.plant_vector("e" * 64)
        self.set_lock_hash("e" * 64)
        out, p = self.engine("recall", "--query", "lantern")
        self.assert_refused(out, "LOCK_NOT_SHIPPED", p)

    def test_saboteur_a_stamp_on_another_table_refuses(self):
        c = schema_migrator.connect(str(self.db))
        c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (1, ?)", (bytes(4 * 768),))
        c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, content_hash, "
                  "embedded_at, organ, input_kind) VALUES (1, 'memory_embeddings_v3', 'm', 'r', ?, 1024, ?, 't', 'o', 'text')",
                  (el.catalogue_tiers()["cpu"]["weights_hash"], "c" * 64))
        c.commit(); c.close()
        out, p = self.engine("recall", "--query", "lantern")
        self.assert_refused(out, "STAMP_INCONSISTENT", p)

    def test_control_a_brain_holding_no_vector_is_not_refused(self):
        out, p = self.engine("recall", "--query", "lantern")
        self.assertEqual((p.returncode, out.get("status")), (0, "ok"), (out, p.stderr[-400:]))
        self.assertTrue(out["results"], "the keyword arm answers an unembedded brain")
        self.assertIn("degraded", out, "a keyword answer says it is one")

    def test_control_an_empty_brain_is_not_refused(self):
        c = schema_migrator.connect(str(self.db)); c.execute("DELETE FROM memories"); c.commit(); c.close()
        out, p = self.engine("recall", "--query", "lantern")
        self.assertEqual((p.returncode, out.get("status"), out["result_count"]), (0, "ok", 0), (out, p.stderr[-400:]))

    def test_control_a_keyword_recall_asked_for_is_not_a_refusal(self):
        (self.d / "embedder.lock.json").write_text("{ not json")
        out, p = self.engine("recall", "--query", "lantern", "--bm25-only")
        self.assertEqual((p.returncode, out.get("status")), (0, "ok"), (out, p.stderr[-400:]))
        self.assertNotIn("degraded", out)

    def test_an_empty_batch_carries_coverage(self):
        env, p = self.cli_json("batch-recall", "--items", "[]")
        self.assertIn("coverage", env["data"], env)


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class ADegradedAnswerReachesAPerson(_Planted):
    """Absent model bytes are not wrong bytes: the words answer, the flag is at the top of the answer, and a person reads it."""

    def setUp(self):
        super().setUp()
        self.plant_vector(el.catalogue_tiers()["cpu"]["weights_hash"])

    def assert_flagged(self, data):
        self.assertTrue(data["results"], data)
        self.assertIs(data["degraded"], True, data)
        self.assertEqual(data["degradation"]["reason"], "SNAPSHOT_ABSENT", data)
        self.assertEqual(data["tier"], 0, data)
        self.assertEqual((data["coverage"]["vector_arm"], data["coverage"]["degraded"]), ("refused", True), data)

    def test_saboteur_the_engine_and_the_front_door_agree_when_the_model_is_absent(self):
        out, p = self.engine("recall", "--query", "lantern")
        self.assertEqual((p.returncode, out.get("status")), (0, "ok"), (out, p.stderr[-400:]))
        self.assert_flagged(out)
        self.assertEqual(out["code"], "recall_degraded", out)
        env, p = self.cli_json("recall", "--query", "lantern")
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assert_flagged(env["data"])
        self.assertEqual((env["status"], env["degraded"], env["code"]), ("ok", True, "recall_degraded"), env)

    def test_saboteur_every_batch_item_says_what_kind_of_answer_it_is(self):
        env, p = self.cli_json("batch-recall", "--items", '["lantern"]')
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        self.assert_flagged(env["data"]["results"][0])
        self.assertEqual((env["status"], env["degraded"], env["code"]), ("ok", True, "batch_recall_degraded"), env)

    def test_saboteur_the_banner_prints_on_the_human_surface(self):
        for argv in (("recall", "--query", "lantern"), ("batch-recall", "--items", '["lantern"]')):
            p = self.cli(*argv)
            self.assertIn("DEGRADED", p.stdout, p.stdout + p.stderr)
            self.assertIn("SNAPSHOT_ABSENT", p.stdout, p.stdout)

    def test_control_vector_only_asks_for_no_keyword_so_it_refuses(self):
        out, p = self.engine("recall", "--query", "lantern", "--vector-only")
        self.assertEqual((p.returncode, out.get("error")), (1, "SNAPSHOT_ABSENT"), (out, p.stderr[-400:]))

    def test_saboteur_a_locked_store_refuses_on_both_doors(self):
        out, p = self.engine("store", "--content", "a new fact worth keeping", "--category", "insight", "--summary", "s")
        self.assertEqual((p.returncode, out.get("error")), (1, "SNAPSHOT_ABSENT"), out)
        env, p = self.cli_json("store", "--content", "a new fact worth keeping", "--category", "insight")
        self.assertEqual((p.returncode, env["code"]), (1, "SNAPSHOT_ABSENT"), env)

    def test_saboteur_the_floor_answers_a_broken_lock_and_says_what_it_is(self):
        def lite():
            p = subprocess.run([sys.executable, str(self.d / "organs" / "claude_memory_lite.py"), "recall", "--query", "lantern"],
                               capture_output=True, text=True, timeout=120, env=self.env(), cwd=str(self.d))
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            return json.loads(p.stdout.strip().splitlines()[-1])
        self.assertNotIn("degraded", lite(), "a sound lock is no fault of the floor's")
        lock = json.loads((self.d / "embedder.lock.json").read_text())
        (self.d / "embedder.lock.json").write_text(json.dumps({**lock, "weights_hash": "f" * 64}))
        out = lite()
        self.assertEqual((out["degraded"]["reason"], out["coverage"]["degraded"]), ("NO_ROWS_FOR_LOCK", True), out)
        (self.d / "embedder.lock.json").write_text("{ not json")
        out = lite()
        self.assertEqual(out["degraded"]["reason"], "LOCK_MALFORMED", out)
        self.assertTrue(out["results"], "Tier 0 cannot fail")

    def test_control_lite_is_the_floor_and_keeps_storing(self):
        p = subprocess.run([sys.executable, str(self.d / "organs" / "claude_memory_lite.py"), "store", "--content",
                            "kept by the floor of the house", "--category", "insight"],
                           capture_output=True, text=True, timeout=120, env=self.env(), cwd=str(self.d))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class ABrainWithNoLockKeepsServing(_Room):
    """The same absent file with no record of a lock behind it: legal, and it answers. Only a lost lock refuses."""
    LOCK = None

    def assert_served(self):
        out, p = self.engine("recall", "--query", "lantern")
        self.assertEqual((p.returncode, out.get("status")), (0, "ok"), (out, p.stderr[-400:]))
        self.assertTrue(out["results"], out)
        self.assertNotEqual((out.get("degradation") or {}).get("reason"), "LOCK_MISSING", out)
        env, p = self.cli_json("recall", "--query", "lantern")
        self.assertEqual((p.returncode, env["status"]), (0, "ok"), env)

    def test_control_a_brain_that_never_had_a_lock(self):
        self.assert_served()

    def test_control_a_rollback_to_no_lock(self):
        c = sqlite3.connect(self.db)
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, '')", (el.BRAIN_LOCK_KEY,))
        c.commit(); c.close()
        self.assert_served()

    def test_control_a_history_that_only_ever_re_embedded(self):
        (self.d / el.LOCK_HISTORY_NAME).write_text(json.dumps({"action": "re-embed", "tier": "cpu"}) + "\n", encoding="utf-8")
        self.assert_served()

    def test_control_a_jina_brain_with_no_lock(self):
        c = sqlite3.connect(self.db)
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v2')")
        c.commit(); c.close()
        self.assert_served()


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class AnInitRunTwice(_Room):
    LOCK = None

    def setUp(self):
        super().setUp()
        self.db.unlink()

    def test_saboteur_a_second_init_leaves_the_brain_byte_identical(self):
        for _ in (1, 2):
            out, p = self.engine("init", "--tier", "cpu")
            self.assertEqual(out.get("status"), "ok", (out, p.stderr[-400:]))
            if _ == 1:
                first = self.db.read_bytes()
        self.assertEqual(self.db.read_bytes(), first, "the lock's row was written again")

    def test_saboteur_a_brain_with_no_memory_keeps_no_backup_of_itself(self):
        out, p = self.engine("init", "--tier", "cpu")
        self.assertEqual(out.get("status"), "ok", (out, p.stderr[-400:]))
        self.assertEqual([f.name for f in self.d.iterdir() if f.name.endswith(".bak")], [])


class TheKeywordMigrationsBackup(unittest.TestCase):
    """The migration's own step, on a stand-in schema: what matters here is the file beside the brain, not the index."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        self.db = self.d / "b.db"
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT)")
        c.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
        c.commit(); c.close()
        self.sql = self.d / "m.sql"
        self.sql.write_text("CREATE TABLE memories_fts (x);", encoding="utf-8")

    def run_it(self):
        import hybrid_retrieval
        return hybrid_retrieval.apply_fts5_migration(str(self.db), str(self.sql))

    def test_control_a_brain_with_a_memory_is_copied_first(self):
        c = sqlite3.connect(self.db); c.execute("INSERT INTO memories (content) VALUES ('kept')"); c.commit(); c.close()
        out = self.run_it()
        self.assertEqual(out["steps"]["backup"]["note"], "created", out)
        self.assertTrue((self.d / "b.db.pre-hybrid-retrieval.bak").is_file())

    def test_saboteur_a_brain_with_none_is_not(self):
        out = self.run_it()
        self.assertEqual(out["steps"]["backup"]["note"], "no memory to keep", out)
        self.assertFalse((self.d / "b.db.pre-hybrid-retrieval.bak").exists())

    def test_saboteur_a_brain_that_cannot_be_read_is_copied_on_the_safe_side(self):
        import hybrid_retrieval
        bad = self.d / "bad.db"
        bad.write_bytes(b"not a database, long enough to be read as a header of one")
        self.assertIs(hybrid_retrieval._holds_memories(bad), True)


# A recall whose query encoder is stood in for (no model is loaded): the question is what each recall surface SAYS when
# its vector arm is out, not what it does with no model. `voidloom` imports the same `claude_memory` module object.
_STUB_ENCODE = ("import sys, runpy; sys.path.insert(0, 'organs'); import claude_memory as c; "
                "c._encode = lambda *a, **k: bytes(4 * 768); ")


class _RecallSurfaces:
    """The four recall surfaces, each answered over this room's brain with the encoder stood in for."""

    def _child(self, code, argv):
        p = subprocess.run([sys.executable, "-c", code, *argv], capture_output=True, text=True, timeout=240,
                           env=self.env(PYTHONPATH=str(ROOT)), cwd=str(self.d))
        try:
            return json.loads(p.stdout), p                      # voidloom --json prints one (indented) document
        except ValueError:
            lines = [ln for ln in p.stdout.strip().splitlines() if ln.startswith("{")]
            return (json.loads(lines[-1]) if lines else {}), p

    def organ(self, *argv):
        return self._child(_STUB_ENCODE + "sys.argv = ['claude_memory.py'] + sys.argv[1:]; c.main()", argv)

    def voidloom(self, *argv):
        return self._child(_STUB_ENCODE + "sys.argv = ['voidloom'] + sys.argv[1:]; "
                           "runpy.run_module('voidloom', run_name='__main__')", [*argv, "--json"])

    def surfaces(self):
        return {"recall": self.organ("recall", "--query", "lantern", "--top_k", "3"),
                "anticipate": self.organ("anticipate", "--top-k", "3"),
                "voidloom recall": self.voidloom("recall", "--query", "lantern", "--top-k", "3"),
                "batch-recall": self.voidloom("batch-recall", "--items", '["lantern", "keeper"]', "--top-k", "3")}


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class EveryRecallSurfaceFlagsAMissingVectorTableAtTheTop(_RecallSurfaces, _Planted):
    """The vector table dropped: every surface answers by keyword, keeps `status: ok`, and carries `degraded: true` and
    a code naming the degradation at the TOP of its own envelope -- the batch's own envelope too, not only its records."""
    CODES = {"recall": "recall_degraded", "anticipate": "anticipate_degraded", "voidloom recall": "recall_degraded",
             "batch-recall": "batch_recall_degraded"}

    def setUp(self):
        super().setUp()
        self.plant_vector(el.catalogue_tiers()["cpu"]["weights_hash"])

    def test_control_an_intact_brain_answers_plain_ok_on_every_surface(self):
        got = {name: (p.returncode, out.get("status"), out.get("code"), out.get("degraded"))
               for name, (out, p) in self.surfaces().items()}
        self.assertEqual(got, {"recall": (0, "ok", None, None), "anticipate": (0, "ok", None, None),
                               "voidloom recall": (0, "ok", "recall_ok", None),
                               "batch-recall": (0, "ok", "batch_recall_ok", None)})

    def test_saboteur_the_vector_table_dropped_is_flagged_at_the_top_of_every_surface(self):
        c = schema_migrator.connect(str(self.db)); c.execute("DROP TABLE memory_embeddings"); c.commit(); c.close()
        surfaces = self.surfaces()
        got = {name: (p.returncode, out.get("status"), out.get("code"), out.get("degraded"))
               for name, (out, p) in surfaces.items()}
        self.assertEqual(got, {name: (0, "ok", code, True) for name, code in self.CODES.items()},
                         {n: (o, p.stderr[-300:]) for n, (o, p) in surfaces.items()})
        out, _p = surfaces["recall"]
        self.assertEqual(out["degradation"]["reason"], "PROVENANCE_UNREADABLE", out)
        self.assertTrue(out["results"], "the keyword arm still answers")
        env, _p = surfaces["batch-recall"]
        self.assertTrue(all(r.get("degraded") is True for r in env["data"]["results"]), env)


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class AnAnswerOverAMissingTableSaysSo(_Room):
    def setUp(self):
        super().setUp()
        c = schema_migrator.connect(str(self.db)); c.execute("DROP TABLE memory_embeddings"); c.commit(); c.close()

    def test_saboteur_anticipate_carries_the_flag_and_the_code(self):
        """Its encoder is stood in for: this asks what it says over a missing table, not what it does with no model."""
        child = ("import sys; sys.argv = ['claude_memory.py', 'anticipate', '--top-k', '3']; sys.path.insert(0, 'organs'); "
                 "import claude_memory as c; c._encode = lambda *a, **k: bytes(4 * 768); c.main()")
        p = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=240, env=self.env(), cwd=str(self.d))
        lines = [ln for ln in p.stdout.strip().splitlines() if ln.startswith("{")]
        out = json.loads(lines[-1]) if lines else {}
        self.assertEqual(out.get("status"), "ok", (out, p.stderr[-400:]))
        self.assertIs(out["coverage"]["degraded"], True, out)
        self.assertEqual(out["coverage"]["error"], "PROVENANCE_UNREADABLE", out)
        self.assertEqual(out["degradation"]["reason"], "PROVENANCE_UNREADABLE", out)
        self.assertEqual((out["degraded"], out["code"]), (True, "anticipate_degraded"), out)

    def test_saboteur_status_carries_the_flag_and_a_person_reads_it(self):
        env, p = self.cli_json("status")
        self.assertIs(env["data"]["coverage"]["degraded"], True, env)
        self.assertEqual(env["data"]["degraded"]["reason"], "PROVENANCE_UNREADABLE", env)
        self.assertIn("DEGRADED", self.cli("status").stdout)

    def test_saboteur_stats_carries_the_flag(self):
        out, p = self.engine("stats")
        self.assertIs(out["degraded"]["reason"] == "PROVENANCE_UNREADABLE", True, out)


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class TheTierVerbAndDoctorOnAPersonsTerminal(_Room):
    def test_tier_status_prints_something_a_person_can_use(self):
        p = self.cli("tier", "status")
        self.assertNotIn("null", p.stdout.splitlines()[-1])
        self.assertIn("memory_embeddings", p.stdout)
        self.assertIn("tier_status", p.stdout)

    def test_a_refusal_prints_its_code_and_message(self):
        p = self.cli("tier", "set", "jina")
        self.assertEqual(p.returncode, 1)
        self.assertIn("LOCK_NOT_SHIPPED", p.stdout + p.stderr)
        self.assertNotIn("None", (p.stdout + p.stderr).splitlines()[-1])

    def test_doctor_exits_4_on_an_unhealthy_brain_and_status_stays_0(self):
        (self.d / "embedder.lock.json").write_text("{ not json")
        self.assertEqual(self.cli("doctor", "--json").returncode, 4)
        self.assertEqual(self.cli("status", "--json").returncode, 0)


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class AForeignEnvDirectory(_Room):
    def setUp(self):
        super().setUp()
        d = self.d / ".voidloom"
        d.mkdir()
        (d / ".voidloom-owner").write_text(json.dumps({"schema": "voidloom.env-owner/1", "root": "/somewhere/else"}))

    def test_doctor_is_not_healthy_and_says_how_to_claim_it(self):
        env, p = self.cli_json("doctor")
        self.assertFalse(env["data"]["brain_healthy"])
        self.assertIn("ENV_DIR_FOREIGN", env["data"]["reason"])
        self.assertIn("--mark", env["data"]["reason"])

    def test_a_store_refuses_rather_than_lose_its_vector(self):
        env, p = self.cli_json("store", "--content", "a fact", "--category", "insight")
        self.assertIn("ENV_DIR_FOREIGN", json.dumps(env), p.stderr[-400:])
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
        c.close()


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class ModelFetchOnABrainWithNoLock(_Room):
    LOCK = None

    def _fetch(self, *argv):
        p = subprocess.run([sys.executable, str(self.d / "organs" / "model_fetch.py"), "--db", str(self.db), "--json", *argv],
                           capture_output=True, text=True, timeout=120, env=self.env(HF_HUB_OFFLINE="1"))
        return json.loads(p.stdout.strip().splitlines()[-1]), p

    def test_it_serves_the_tier_the_brain_already_uses(self):
        out, p = self._fetch("--plan")
        self.assertEqual(out["status"], "ok", (out, p.stderr[-300:]))
        self.assertEqual(out["tier"], "cpu")

    def test_the_plan_marks_a_reranker_the_gate_will_not_load(self):
        out, _p = self._fetch("--plan")
        rr = [m for m in out["needed"] if m["what"] == "reranker"]
        self.assertTrue(rr and "gate" in rr[0], rr)

    def test_an_absent_reranker_alone_says_recall_is_unaffected(self):
        import model_fetch
        self.assertFalse(model_fetch._reranker_gate_on() and False)


if __name__ == "__main__":
    unittest.main()
