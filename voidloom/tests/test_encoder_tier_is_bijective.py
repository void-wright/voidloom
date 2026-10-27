# -*- coding: utf-8 -*-
"""The encoder's tier must be the table's tier — for ALL THREE tiers.

THE DEFECT. `memory_write.default_encoder` chose the encoder with

    claude_memory._active_model_cache["model"] = (
        "jina" if table == "memory_embeddings_v2" else "nomic")

a TWO-tier answer to a THREE-tier question. `memory_embeddings_v3` — Qwen3-
Embedding-0.6B, which `embed_resolver` itself annotates as "the SHIPPED tier" —
mapped to NOMIC, so a qwen brain would encode 768-wide vectors for a 1024-wide
table. `assert_width` catches that, so nothing was ever corrupted; what it MEANT
is that a qwen-tier brain could not embed AT ALL, every write tagged
`embedding-missing`.

Requested as verification by a downstream review, which recorded the fix "as
reported, not re-tested". This file is the re-test.

TWO LAYERS, DELIBERATELY.

  * The MAPPING layer is model-free and always runs. It asserts the bijection is
    total over `EMBED_TIERS` and that each tier's dim matches its table's — and it
    reproduces the PARENT expression to show it was wrong for exactly one tier.
    This is the layer that would have caught the bug, and it costs milliseconds.

  * The END-TO-END layer builds a real qwen-tier brain and embeds into it, gated
    on the model being cached. It is the layer that proves the claim rather than
    the arithmetic. A skip here says so in its reason: an absent model is a fact
    about the host, and "not measured" is not "measured and fine".
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "organs"))

HF_HUB = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def _cached(repo: str) -> bool:
    return (HF_HUB / ("models--" + repo.replace("/", "--"))).is_dir()


class TestEncoderTierIsBijective(unittest.TestCase):
    """The layer that would have caught it. No model, no network, no brain."""

    def setUp(self):
        import claude_memory as cm
        self.cm = cm

    def test_the_map_is_total_over_every_declared_tier(self):
        tiers = self.cm.EMBED_TIERS
        table_to_tier = self.cm._TABLE_TO_TIER
        self.assertGreaterEqual(len(tiers), 3, "fewer than three tiers — this test's "
                                               "whole subject is the third one")
        for tier, (table, dim, repo) in tiers.items():
            self.assertIn(table, table_to_tier,
                          f"tier {tier!r} declares table {table!r} and the inverse "
                          "map does not know it")
            self.assertEqual(table_to_tier[table], tier,
                             f"the map is not bijective at {table!r}")
            self.assertGreater(dim, 0)
            self.assertTrue(repo, f"tier {tier!r} has no repo id")

    def test_same_width_is_not_the_same_space(self):
        """jina and qwen are BOTH 1024. Dimension cannot identify a tier."""
        dims = {}
        for tier, (_t, dim, _r) in self.cm.EMBED_TIERS.items():
            dims.setdefault(dim, []).append(tier)
        collide = {d: t for d, t in dims.items() if len(t) > 1}
        self.assertTrue(collide, "no two tiers share a width — if that becomes true, "
                                 "this test's warning is obsolete, but until then a "
                                 "width check is NOT a compatibility check")
        for d, tiers in collide.items():
            repos = {self.cm.EMBED_TIERS[t][2] for t in tiers}
            self.assertEqual(len(repos), len(tiers),
                             f"tiers {tiers} share width {d} AND a repo id")

    def test_the_parent_expression_was_wrong_for_exactly_one_tier(self):
        """Reproduce the defect, so the fix is anchored to what it replaced."""
        wrong = []
        for table, tier in self.cm._TABLE_TO_TIER.items():
            parent = "jina" if table == "memory_embeddings_v2" else "nomic"
            if parent != tier:
                wrong.append((table, parent, tier))
        self.assertEqual(len(wrong), 1,
                         "the parent binary expression is expected to be wrong for "
                         f"exactly one tier; got {wrong}")
        table, parent, actual = wrong[0]
        self.assertEqual(table, "memory_embeddings_v3")
        parent_dim = self.cm.EMBED_TIERS[parent][1]
        actual_dim = self.cm.EMBED_TIERS[actual][1]
        self.assertNotEqual(parent_dim, actual_dim,
                            "the parent bug is only harmless if the widths agree; "
                            "they must not, or this test is not describing it")


@unittest.skipUnless(_cached("Qwen/Qwen3-Embedding-0.6B"),
                     "Qwen/Qwen3-Embedding-0.6B is not in this host's HF cache — "
                     "the end-to-end qwen embed is NOT MEASURED here, which is not "
                     "the same as measured and fine")
class TestQwenTierEmbedsAtItsOwnWidth(unittest.TestCase):
    """The layer that proves the claim: a real qwen brain, really embedded."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="vl_qwen_", dir=str(Path.home()))
        box = Path(cls._tmp) / "pkg"
        out = subprocess.run(["git", "ls-files"], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise unittest.SkipTest("git unavailable; cannot build a package clone")
        for rel in [p for p in out.stdout.split("\n") if p.strip()]:
            dst = box / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dst)
        cls.box = box
        env = {**os.environ, "WIZARD_EMBED_MODEL": "qwen",
               "PYTHONDONTWRITEBYTECODE": "1"}
        env["CUDA_VISIBLE_DEVICES"] = "1"   # FORCED, never setdefault (#15708): GPU 0 drives the display
        cls.env = env
        r = subprocess.run([sys.executable, "organs/claude_memory.py", "init"],
                           cwd=str(box), capture_output=True, text=True,
                           timeout=1800, env=env)
        if r.returncode != 0:
            raise unittest.SkipTest("qwen-tier init failed: " + r.stderr[-400:])
        subprocess.run([sys.executable, "organs/claude_memory_lite.py", "load-seed",
                        "--file", "seed/seed_pack.json"],
                       cwd=str(box), capture_output=True, text=True,
                       timeout=600, env=env)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _conn(self):
        c = sqlite3.connect(str(self.box / "claude_memory.db"))
        try:
            import sqlite_vec
            c.enable_load_extension(True); sqlite_vec.load(c)
            c.enable_load_extension(False)
        except Exception:
            pass
        return c

    def test_the_brain_declares_the_qwen_tier(self):
        c = self._conn()
        try:
            got = {k: v for k, v in c.execute(
                "SELECT key, value FROM metadata WHERE key LIKE 'active_embedding%'")}
        finally:
            c.close()
        self.assertEqual(got.get("active_embedding_table"), "memory_embeddings_v3")
        self.assertEqual(got.get("active_embedding_dim"), "1024")
        self.assertIn("Qwen", got.get("active_embedding_model", ""))

    def test_the_backfill_writes_1024_wide_vectors_into_v3(self):
        r = subprocess.run([sys.executable, "organs/embed_backfill.py",
                            "claude_memory.db", "--limit", "3", "--json"],
                           cwd=str(self.box), capture_output=True, text=True,
                           timeout=2400, env=self.env)
        self.assertEqual(r.returncode, 0,
                         "the qwen-tier backfill did not succeed:\n"
                         + r.stdout[-900:] + r.stderr[-900:])
        import json as _j, re as _re
        m = _re.search(r"\{.*\}", r.stdout, _re.S)
        env = _j.loads(m.group(0)) if m else {}
        self.assertEqual(env.get("table"), "memory_embeddings_v3", env)
        self.assertEqual(env.get("dim"), 1024, env)
        self.assertGreater(env.get("embedded", 0), 0,
                           "nothing was embedded, so the width assertion below "
                           "would be vacuous")
        self.assertEqual(env.get("failed"), 0, env)

        # THE CLAIM ITSELF, from the bytes: float32 * 1024 = 4096.
        c = self._conn()
        try:
            n = c.execute("SELECT COUNT(*) FROM memory_embeddings_v3").fetchone()[0]
            widths = {len(row[0]) for row in c.execute(
                "SELECT embedding FROM memory_embeddings_v3 LIMIT 3")}
        finally:
            c.close()
        self.assertGreater(n, 0)
        self.assertEqual(widths, {4096},
                         "a qwen-tier brain stored vectors that are not 1024 "
                         f"float32 wide: byte lengths {sorted(widths)}. 3072 bytes "
                         "is the 768-wide nomic vector the parent expression would "
                         "have produced.")


if __name__ == "__main__":
    unittest.main()
