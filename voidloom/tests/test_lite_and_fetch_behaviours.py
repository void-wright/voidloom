"""Lines that used to be proved by their text alone, run for what they do.

THE CLAIM UNDER TEST
  * the keyword engine flags what it loads from the pack: seed = 1 and an origin naming the pack record;
  * its `seed-scrub` verb exists and refuses nothing on a clean pack;
  * `model_fetch.py --no-fetch` on an empty cache refuses MODELS_ABSENT and downloads nothing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

import embedder_lock as el  # noqa: E402
import schema_migrator  # noqa: E402


class _Brain(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        self.db = self.d / "claude_memory.db"
        schema_migrator.apply(str(self.db), ROOT / "migrations")

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "TRANSFORMERS_", "WIZARD_", "VOIDLOOM_"))}
        env.update(HOME=str(self.d), HF_HUB_CACHE=str(self.d / "hub"), WIZARD_LIBRARY_DB=str(self.db),
                   PYTHONDONTWRITEBYTECODE="1", **extra)
        return env

    def lite(self, *argv):
        return subprocess.run([sys.executable, str(ROOT / "organs" / "claude_memory_lite.py"), *argv], capture_output=True,
                              text=True, timeout=120, env=self.env(), cwd=str(self.d))


class TheKeywordEngineFlagsTheSeed(_Brain):
    def test_saboteur_every_row_it_loads_carries_seed_and_origin(self):
        p = self.lite("load-seed", "--file", str(ROOT / "seed" / "seed_pack.json"))
        self.assertEqual(p.returncode, 0, p.stdout[-400:] + p.stderr[-400:])
        c = sqlite3.connect(self.db)
        total, flagged, origins = c.execute("SELECT COUNT(*), SUM(seed), COUNT(origin) FROM memories").fetchone()
        sample = c.execute("SELECT origin FROM memories WHERE origin IS NOT NULL LIMIT 1").fetchone()[0]
        c.close()
        self.assertGreater(total, 0)
        self.assertEqual((flagged, origins), (total, total))
        self.assertTrue(sample.startswith("seed_pack:"), sample)

    def test_saboteur_the_scrub_verb_runs_and_passes_the_shipped_pack(self):
        p = self.lite("seed-scrub", "--file", str(ROOT / "seed" / "seed_pack.json"))
        self.assertEqual(p.returncode, 0, p.stdout[-400:] + p.stderr[-400:])


class TheFetchOnWindows(unittest.TestCase):
    def test_saboteur_one_file_at_a_time_where_a_symlink_may_need_a_privilege(self):
        self.assertEqual(el.fetch_options("win32"), {"max_workers": 1})

    def test_control_elsewhere_the_library_keeps_its_own_default(self):
        self.assertEqual(el.fetch_options("linux"), {})


class AFetchThatMayNotFetch(_Brain):
    def test_saboteur_no_fetch_refuses_and_nothing_lands(self):
        el.write_lock(self.d, "cpu", brain=self.db)
        p = subprocess.run([sys.executable, str(ROOT / "organs" / "model_fetch.py"), "--db", str(self.db), "--no-fetch", "--json"],
                           capture_output=True, text=True, timeout=120, env=self.env(), cwd=str(self.d))
        self.assertEqual(p.returncode, 3, p.stdout[-400:] + p.stderr[-400:])
        self.assertEqual(json.loads(p.stdout)["error"], "MODELS_ABSENT")
        self.assertFalse((self.d / "hub").exists() and any((self.d / "hub").iterdir()))


class TheEnginesPinIsLoadableOffline(unittest.TestCase):
    """jina's remote code asks for its adapters with no revision. A fetch by commit writes no `refs/main`, so a cache that
    holds the pinned bytes still failed the load: measured on real bytes, `recall` read `unhandled`."""

    def setUp(self):
        import embedding_upgrade as eu
        self.eu = eu
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.hub = Path(self.td.name)
        self.repo = self.hub / ("models--" + eu.JINA_MODEL_NAME.replace("/", "--"))
        p = mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(self.hub)})
        p.start()
        self.addCleanup(p.stop)

    def snapshot(self):
        (self.repo / "snapshots" / self.eu.JINA_REVISION).mkdir(parents=True)

    def test_saboteur_a_fetch_by_commit_leaves_a_ref_the_adapters_can_resolve(self):
        self.snapshot()
        self.eu.fetch_jina_at_pin()
        self.assertEqual((self.repo / "refs" / "main").read_text(encoding="utf-8"), self.eu.JINA_REVISION)

    def test_control_the_hubs_own_ref_is_left(self):
        self.snapshot()
        (self.repo / "refs").mkdir()
        (self.repo / "refs" / "main").write_text("a" * 40, encoding="utf-8")
        self.eu.fetch_jina_at_pin()
        self.assertEqual((self.repo / "refs" / "main").read_text(encoding="utf-8"), "a" * 40)

    def test_saboteur_a_load_that_cannot_resolve_offline_is_typed_not_unhandled(self):
        import embedder_lock as _el
        fake = types.ModuleType("sentence_transformers")

        def boom(*a, **k):
            raise FileNotFoundError("Cannot find an appropriate cached snapshot folder")
        fake.SentenceTransformer = boom
        self.eu._jina_model_cache.clear()
        with mock.patch.dict(sys.modules, {"sentence_transformers": fake}), \
                mock.patch.dict(os.environ, {"HF_HUB_OFFLINE": "1"}):
            with self.assertRaises(_el.EmbedderLockError) as cm:
                self.eu.get_jina_model() if hasattr(self.eu, "get_jina_model") else self.eu._get_jina_model()
        self.assertEqual(cm.exception.code, "SNAPSHOT_ABSENT")
        self.assertIn("model_fetch.py", cm.exception.detail["retry"][0])


if __name__ == "__main__":
    unittest.main()
