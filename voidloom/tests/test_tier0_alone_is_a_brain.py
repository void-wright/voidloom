# -*- coding: utf-8 -*-
"""Tier 0, alone, is a working brain — proven by building one.

THE CLAIM. The whole degradation story rests on one sentence: when Tier 1 is
unavailable, `claude_memory_lite.py` — stdlib FTS5, no torch, no models, no
sqlite_vec — still gives you a brain you can store into and recall from. The
plan's own completeness gate says of it:

  > *"And Tier-0 alone — with claude_memory_lite.py as the only engine, prove
  > keyword recall works when Tier-1 is unavailable. That last one is the claim
  > nobody has ever tested, and it is the one the whole degradation story rests
  > on."*

It had never been tested. `cli_install_suite` carries 71 checks and ZERO hits for
`tier0`, `nomic`, `WIZARD_EMBED_MODEL` or `claude_memory_lite`.

WHAT THIS BUILDS, and why the file list is the point. The fixture copies ONLY the
files Tier 0 is supposed to need:

    organs/claude_memory_lite.py   the engine
    organs/schema_migrator.py      the stdlib brain-builder install.sh falls back to
    migrations/                    schema.sql + every migration
    seed/seed_pack.json            the shipped corpus

`organs/claude_memory.py` is **deliberately absent** — not monkeypatched, not
flag-disabled. A host without the Tier-1 engine is simulated by not having it, and
the assertion that the list above is SUFFICIENT is half of what this test proves:
if Tier 0 ever grows a dependency on a file not named here, this goes red.

WHAT IT ASSERTS, and what it deliberately does not. Keyword recall over the seed
corpus RETURNS ROWS. Not that it ranks well — FTS5 is a keyword engine and ranking
is Tier 1's job. The first test uses a single term; the natural-question test below
asks the way a person does, with stopwords and a question mark. That second test
did not exist while the engine joined words with implicit AND: a natural question
then returned zero, and this docstring called that legitimate. It was the defect
(2026-10-27: the query builder now ORs the content words, and a '?' or an
apostrophe no longer raises an FTS5 syntax error).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The Tier-0 surface, declared. If this list is wrong the test fails loudly rather
# than quietly borrowing a file from the developer's tree.
TIER0_FILES = ["organs/claude_memory_lite.py", "organs/schema_migrator.py"]
TIER0_DIRS = ["migrations"]
TIER0_SEED = "seed/seed_pack.json"
BLANK = "seed/claude_memory_blank.db"


def _envelope(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


class TestTier0AloneIsABrain(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        missing = [p for p in TIER0_FILES + [TIER0_SEED, BLANK]
                   if not (ROOT / p).is_file()]
        missing += [d for d in TIER0_DIRS if not (ROOT / d).is_dir()]
        if missing:
            raise unittest.SkipTest("Tier-0 surface incomplete in this tree: "
                                    + ", ".join(missing))
        cls._tmp = tempfile.mkdtemp(prefix="vl_tier0_", dir=str(Path.home()))
        box = Path(cls._tmp)
        for rel in TIER0_FILES:
            (box / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, box / rel)
        for d in TIER0_DIRS:
            shutil.copytree(ROOT / d, box / d)
        (box / "seed").mkdir(exist_ok=True)
        shutil.copy2(ROOT / TIER0_SEED, box / TIER0_SEED)
        shutil.copy2(ROOT / BLANK, box / "claude_memory.db")
        cls.box = box

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # The brain resolvers the engines consult BEFORE their relative-self default. An operator
    # who exported one (the README's own way to point the host-bound classes at a brain) had
    # this file's `load-seed` write the seed pack INTO THAT BRAIN rather than into the box --
    # the lite engine resolves WIZARD_LIBRARY_DB first, then WIZARD_LIBRARY_HOME
    # (reference-host round, R10e). The box is the only brain this test may touch.
    _RESOLVERS = ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "CLAUDE_MEMORY_DB")

    def _run(self, *args, timeout=600):
        env = {k: v for k, v in os.environ.items() if k not in self._RESOLVERS}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run([sys.executable, *args], cwd=str(self.box), env=env,
                              capture_output=True, text=True, timeout=timeout)

    def test_the_tier1_engine_is_genuinely_absent(self):
        """The fixture must not accidentally carry the thing it excludes."""
        self.assertFalse((self.box / "organs" / "claude_memory.py").exists(),
                         "the Tier-1 engine is present in the Tier-0 fixture — "
                         "this test would prove nothing")
        self.assertTrue((self.box / "organs" / "claude_memory_lite.py").is_file())

    def test_tier0_builds_seeds_and_recalls_with_no_tier1_engine(self):
        mig = self._run("organs/schema_migrator.py", "apply", "--db", "claude_memory.db")
        self.assertEqual(mig.returncode, 0, mig.stdout[-600:] + mig.stderr[-600:])

        seed = self._run("organs/claude_memory_lite.py", "load-seed",
                         "--file", "seed/seed_pack.json")
        self.assertEqual(seed.returncode, 0, seed.stderr[-600:])
        loaded = _envelope(seed.stdout).get("loaded")
        self.assertIsInstance(loaded, int)
        self.assertGreater(loaded, 0, "the seed pack loaded nothing — every "
                                      "assertion below would be vacuous")
        # ...and it landed in THE BOX: the rows are counted where they were meant to go,
        # so a resolver leaking in from the invoker's environment is red here, not silent.
        import sqlite3
        c = sqlite3.connect(f"file:{self.box / 'claude_memory.db'}?mode=ro", uri=True)
        try:
            in_box = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(in_box, loaded, "the seed pack was loaded somewhere other than the box's brain")

        # A single term first; the natural question has its own test below.
        rec = self._run("organs/claude_memory_lite.py", "recall",
                        "--query", "memory", "--top_k", "3")
        self.assertEqual(rec.returncode, 0, rec.stderr[-600:])
        env = _envelope(rec.stdout)
        self.assertEqual(env.get("status"), "ok", rec.stdout[-400:])
        results = env.get("results") or (env.get("data") or {}).get("results") or []
        self.assertGreater(len(results), 0,
                           "Tier-0 keyword recall returned NO ROWS over a seeded "
                           "corpus — the degradation story does not hold")
        # It must also SAY it is the keyword tier: a Tier-0 answer that does not
        # name itself is the unstamped-degrade defect this cycle removed.
        mode = env.get("mode") or env.get("retrieval_path") or ""
        self.assertIn("keyword", str(mode).lower(),
                      f"Tier-0 recall did not name its mode: {mode!r}")

    def test_tier0_natural_question_finds_its_answer(self):
        """A question asked the way a person asks it -- stopwords, an apostrophe and a
        question mark -- returns rows. Under the old implicit-AND builder this raised
        an FTS5 syntax error on the '?', fell back to matching the whole question as
        one phrase, and returned nothing."""
        st = self._run("organs/claude_memory_lite.py", "store",
                       "--content", "The lighthouse keeper repainted the north tower in May",
                       "--category", "insight")
        self.assertEqual(st.returncode, 0, st.stderr[-600:])
        rec = self._run("organs/claude_memory_lite.py", "recall",
                        "--query", "When did the keeper's tower get repainted?", "--top_k", "5")
        self.assertEqual(rec.returncode, 0, rec.stderr[-600:])
        env = _envelope(rec.stdout)
        results = env.get("results") or (env.get("data") or {}).get("results") or []
        self.assertTrue(any("lighthouse keeper" in (r.get("content") or "") for r in results),
                        "a natural question did not find the memory that answers it")

    def test_tier0_can_store_as_well_as_read(self):
        """A brain you cannot write to is an archive."""
        st = self._run("organs/claude_memory_lite.py", "store",
                       "--content", "voidloom tier-0 fixture write, safe to delete",
                       "--category", "insight")
        self.assertEqual(st.returncode, 0, st.stderr[-600:])
        self.assertEqual(_envelope(st.stdout).get("status"), "ok", st.stdout[-400:])
        rec = self._run("organs/claude_memory_lite.py", "recall",
                        "--query", "fixture", "--top_k", "5")
        env = _envelope(rec.stdout)
        results = env.get("results") or (env.get("data") or {}).get("results") or []
        self.assertTrue(any("tier-0 fixture write" in (r.get("content") or "")
                            for r in results),
                        "a memory stored through the Tier-0 engine was not "
                        "recallable through it")

    def test_tier0_store_echoes_the_importance_it_persisted(self):
        """Reference-host round, iteration 3, item 5: the store envelope's importance is the value PERSISTED
        (the H4 echo contract, test_voidloom.py::test_store_importance_echo_matches_persisted). The lite engine
        returned none, so that test -- skipped only when no model is cached -- went red on every Tier-0 brain
        whose home HAS a model cached (measured, inherited from c6d7af2)."""
        st = self._run("organs/claude_memory_lite.py", "store",
                       "--content", "voidloom tier-0 importance echo, safe to delete",
                       "--category", "insight", "--importance", "0.73")
        self.assertEqual(st.returncode, 0, st.stderr[-600:])
        env = _envelope(st.stdout)
        echoed = env.get("importance", (env.get("data") or {}).get("importance"))
        self.assertIsInstance(echoed, (int, float), "the Tier-0 store envelope carries no importance: %r" % env)
        import sqlite3
        c = sqlite3.connect(f"file:{self.box / 'claude_memory.db'}?mode=ro", uri=True)
        try:
            stored = c.execute("SELECT importance FROM memories WHERE id=?",
                               (env.get("memory_id"),)).fetchone()[0]
        finally:
            c.close()
        self.assertEqual(echoed, stored, "the echo is not the persisted value")
        self.assertAlmostEqual(stored, 0.73)


if __name__ == "__main__":
    unittest.main()
