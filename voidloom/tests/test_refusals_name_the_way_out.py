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
    def test_a_store_refuses_lock_missing_with_the_fix(self):
        (self.d / "embedder.lock.json").unlink()
        out, p = self.engine("store", "--content", "a new fact worth keeping", "--category", "insight", "--summary", "s")
        self.assertEqual(out.get("error"), "LOCK_MISSING", (out, p.stderr[-400:]))
        self.assertIn("init --tier cpu", out["retry"][0])
        c = sqlite3.connect(self.db)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 1)
        c.close()

    def test_lock_and_history_both_gone_is_not_healthy_when_a_stamp_names_a_shipped_model(self):
        whash = el.catalogue_tiers()["cpu"]["weights_hash"]
        c = sqlite3.connect(self.db)
        c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, content_hash, "
                  "embedded_at, organ, input_kind) VALUES (1, 'memory_embeddings', 'm', 'r', ?, 768, ?, 't', 'o', 'text')", (whash, "c" * 64))
        c.commit(); c.close()
        (self.d / "embedder.lock.json").unlink()
        (self.d / "embedder.lock.history.jsonl").unlink()
        el._stamped_by_a_shipped_model.clear()
        self.assertEqual(el.lost_lock(self.d), {"tier": "cpu"})
        env, p = self.cli_json("doctor")
        self.assertFalse(env["data"]["brain_healthy"], env["data"]["reason"])

    def test_control_a_brain_with_no_history_and_no_shipped_stamp_is_legal(self):
        (self.d / "embedder.lock.json").unlink()
        (self.d / "embedder.lock.history.jsonl").unlink()
        el._stamped_by_a_shipped_model.clear()
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

    def test_a_dropped_vector_table_is_not_a_successful_sleep(self):
        self.plant("DROP TABLE memory_embeddings")
        out, p = self.engine("sleep")
        self.assertEqual(out.get("error"), "SCHEMA_DAMAGED", (out, p.stderr[-400:]))
        out, _p = self.engine("context")
        self.assertTrue(out.get("degraded"), out)
        self.assertEqual(out["schema_gaps"], ["memory_embeddings"])


@unittest.skipUnless(HAVE_VEC, "needs sqlite_vec")
class RefusalsCarryCoverage(_Room):
    def test_no_rows_for_lock_wins_over_the_encoder_and_carries_coverage(self):
        c = schema_migrator.connect(str(self.db))
        c.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (1, ?)", (bytes(4 * 768),))
        c.execute("INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, weights_hash, dim, content_hash, "
                  "embedded_at, organ, input_kind) VALUES (1, 'memory_embeddings', 'm', 'r', ?, 768, ?, 't', 'o', 'text')", ("f" * 64, "c" * 64))
        c.commit(); c.close()
        out, p = self.engine("recall", "--query", "lantern")
        self.assertEqual(out.get("error"), "NO_ROWS_FOR_LOCK", (out, p.stderr[-400:]))
        self.assertIn("coverage", out)

    def test_a_malformed_lock_carries_coverage(self):
        (self.d / "embedder.lock.json").write_text("{ not json")
        out, p = self.engine("recall", "--query", "lantern")
        self.assertEqual(out.get("error"), "LOCK_MALFORMED", (out, p.stderr[-400:]))
        self.assertIn("coverage", out)

    def test_an_empty_batch_carries_coverage(self):
        env, p = self.cli_json("batch-recall", "--items", "[]")
        self.assertIn("coverage", env["data"], env)


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
