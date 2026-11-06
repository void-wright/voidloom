"""`models_present` answers for THIS brain's model, in the cache its loader reads.

An ANY over every tier said ready when another tier's model was cached and this brain's was not, and `onboarded` follows
it. Two caches are planted, so the answer can only be right if the resolver reads the loader's precedence. Snapshot
directories are empty: this is a path census and never opens them.
"""
from __future__ import annotations

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
from voidloom import _bootstrap  # noqa: E402

NOMIC_DIR = "models--nomic-ai--nomic-embed-text-v1.5"
JINA_DIR = "models--jinaai--jina-embeddings-v5-text-small"
QWEN_DIR = "models--Qwen--Qwen3-Embedding-0.6B"


def brain(d: Path, table=None) -> Path:
    db = d / "claude_memory.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
    if table:
        c.execute("INSERT INTO metadata VALUES ('active_embedding_table', ?)", (table,))
    c.commit()
    c.close()
    return db


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.cache = self.d / "hfcache"
        self.cache.mkdir()
        env = {"HF_HUB_CACHE": str(self.cache), "HOME": str(self.d / "home")}
        self.patch = mock.patch.dict(os.environ, env)
        self.patch.start()
        for k in ("HUGGINGFACE_HUB_CACHE", "HF_HOME", "WIZARD_EMBED_MODEL"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def cached(self, dirname, revision="abc", files=()):
        snap = self.cache / dirname / "snapshots" / revision
        snap.mkdir(parents=True)
        for name in files:
            (snap / name).parent.mkdir(parents=True, exist_ok=True)
            (snap / name).write_bytes(b"")

    def snapshots(self, lock):
        self.cached("models--" + lock["model_id"].replace("/", "--"), lock["revision"], lock.get("files") or ())
        if lock.get("code"):
            self.cached("models--" + lock["code"]["model_id"].replace("/", "--"), lock["code"]["revision"],
                        lock["code"].get("files") or ())


class Locked(_Case):
    def setUp(self):
        super().setUp()
        self.db = brain(self.d)
        self.lock = el.write_lock(self.d, "cpu")

    def test_control_the_locks_snapshots_are_the_answer(self):
        self.snapshots(self.lock)
        m = _bootstrap._detect_models(self.db)
        self.assertTrue(m["present"], m)
        self.assertEqual(sorted(m["lock_snapshots"].values()), [True] * len(m["lock_snapshots"]))
        self.assertIn("the lock's", m["present_semantics"])

    def test_saboteur_another_tiers_model_is_not_this_brains(self):
        self.cached(QWEN_DIR)
        m = _bootstrap._detect_models(self.db)
        self.assertFalse(m["present"], m)
        self.assertTrue(m["any_engine_tier_present"])
        self.assertTrue(m["by_model"]["qwen"])

    def test_saboteur_the_same_repo_at_another_revision_is_not_the_pin(self):
        self.cached("models--" + self.lock["model_id"].replace("/", "--"), "0" * 40)
        self.assertFalse(_bootstrap._detect_models(self.db)["present"])

    def test_saboteur_remote_code_missing_is_not_present(self):
        if not self.lock.get("code"):
            self.skipTest("this tier pins no remote code")
        self.cached("models--" + self.lock["model_id"].replace("/", "--"), self.lock["revision"], self.lock.get("files") or ())
        m = _bootstrap._detect_models(self.db)
        self.assertFalse(m["present"], m)
        self.assertEqual(sorted(m["lock_snapshots"].values()), [False, True])

    def test_saboteur_a_malformed_lock_vouches_for_nothing(self):
        self.snapshots(self.lock)
        (self.d / "embedder.lock.json").write_text("{ not json", encoding="utf-8")
        m = _bootstrap._detect_models(self.db)
        self.assertFalse(m["present"], m)
        self.assertEqual(m["lock_refusal"], "LOCK_MALFORMED")

    def test_control_onboarded_and_the_note_follow_the_answer(self):
        self.cached(QWEN_DIR)
        rep = _bootstrap.bootstrap(str(self.db))
        self.assertIs(rep["brain"]["models_present"], False)
        self.assertIs(rep["onboarded"], False)
        self.assertTrue(any("Embedding models not in the HF cache" in n for n in rep["notes"]))
        self.snapshots(self.lock)
        rep = _bootstrap.bootstrap(str(self.db))
        self.assertIs(rep["brain"]["models_present"], True)
        self.assertIs(rep["onboarded"], True)

    def test_control_a_symlinked_brain_sees_the_lock_beside_its_real_file(self):
        self.snapshots(self.lock)
        other = self.d / "elsewhere"
        other.mkdir()
        link = other / "claude_memory.db"
        link.symlink_to(self.db)
        self.assertTrue(_bootstrap._detect_models(link)["present"])


class NoLock(_Case):
    def test_control_a_jina_brain_answers_for_jina(self):
        db = brain(self.d, "memory_embeddings_v2")
        import embedding_upgrade as eu
        self.cached(JINA_DIR, eu.JINA_REVISION, eu.JINA_SNAPSHOT_FILES)
        m = _bootstrap._detect_models(db)
        self.assertIs(m["present"], True)
        self.assertEqual(m["brain_tier"], "jina")
        self.assertIn("has no lock", m["present_semantics"])

    def test_saboteur_a_folder_of_that_name_at_no_pin_is_not_the_model(self):
        db = brain(self.d, "memory_embeddings_v2")
        self.cached(JINA_DIR)
        self.assertIs(_bootstrap._detect_models(db)["present"], False)

    def test_saboteur_a_jina_brain_is_not_served_by_nomic(self):
        db = brain(self.d, "memory_embeddings_v2")
        self.cached(NOMIC_DIR)
        m = _bootstrap._detect_models(db)
        self.assertIs(m["present"], False)
        self.assertIs(m["any_engine_tier_present"], True)

    def test_control_the_variable_outranks_metadata_as_it_does_in_the_engine(self):
        db = brain(self.d, "memory_embeddings_v2")
        self.snapshots(el.catalogue_tiers()["cpu"])
        with mock.patch.dict(os.environ, {"WIZARD_EMBED_MODEL": "nomic"}):
            m = _bootstrap._detect_models(db)
        self.assertEqual(m["brain_tier"], "nomic")
        self.assertIs(m["present"], True)

    def test_control_no_recorded_table_is_the_engines_default(self):
        db = brain(self.d)
        self.cached(NOMIC_DIR)
        self.assertEqual(_bootstrap._detect_models(db)["brain_tier"], "nomic")

    def test_saboteur_a_file_that_is_no_database_vouches_for_nothing(self):
        db = self.d / "claude_memory.db"
        db.write_bytes(b"not a database" * 100)
        self.cached(NOMIC_DIR)
        m = _bootstrap._detect_models(db)
        self.assertIs(m["present"], False)
        self.assertIsNone(m["brain_tier"])

    def test_control_no_brain_on_disk_keeps_the_labelled_census(self):
        self.cached(NOMIC_DIR)
        m = _bootstrap._detect_models(self.d / "nope.db")
        self.assertIs(m["present"], True)
        self.assertIn("any", m["present_semantics"])
        self.assertNotIn("brain_tier", m)

    def test_control_no_argument_is_the_census(self):
        self.cached(NOMIC_DIR)
        self.assertIn("any", _bootstrap._detect_models()["present_semantics"])


class TheEngineUnreadable(_Case):
    def test_saboteur_a_qwen_only_cache_is_seen_when_the_engine_cannot_be_parsed(self):
        self.cached(QWEN_DIR)
        (self.d / "organs").mkdir()
        (self.d / "organs" / "claude_memory.py").write_text("this is not python(", encoding="utf-8")
        (self.d / "organs" / "embedder_lock.py").write_bytes((ROOT / "organs" / "embedder_lock.py").read_bytes())
        with mock.patch.object(_bootstrap._paths, "organ_dir", return_value=self.d / "organs"):
            m = _bootstrap._detect_models()
        self.assertIn("FALLBACK LITERAL", m["census_derived_from"])
        self.assertIs(m["by_model"]["qwen"], True)
        self.assertIs(m["present"], True)
        self.assertIn("jina", m["by_model"], "the fallback narrows nothing a personal install keeps")


class TheCache(_Case):
    def test_control_one_cache_and_the_loaders(self):
        self.assertEqual(_bootstrap._hf_cache_roots(), [el.hf_hub_cache()])
        self.assertEqual(_bootstrap._hf_cache_roots(), [self.cache])

    def test_control_the_note_names_the_shipped_gpu_tier(self):
        notes = _bootstrap._tier_notes("gpu", {"db_present": True, "models_present": True}, {"gpus": [{}]})
        self.assertIn("Qwen3-Embedding-0.6B", notes[0])
        self.assertNotIn("Jina", notes[0])


class StaysStdlib(unittest.TestCase):
    def test_saboteur_it_imports_nothing_heavy(self):
        import subprocess
        code = (f"import sys; sys.path.insert(0, {str(ROOT)!r})\n"
                "from voidloom import _bootstrap\n"
                "_bootstrap.bootstrap('/nonexistent/claude_memory.db')\n"
                "print(sorted(m for m in ('torch', 'sentence_transformers', 'claude_memory') if m in sys.modules))\n")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "[]"), r.stderr[-400:])


if __name__ == "__main__":
    unittest.main()
