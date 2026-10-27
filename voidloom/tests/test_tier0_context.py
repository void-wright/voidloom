"""R4 (reference-host round): `voidloom context` works on a Tier-0 brain.

Measured at c6d7af2: `organs/claude_memory_lite.py` registered a `context` subparser
("alias of prime") but defined only `cmd_prime`, and the facade resolves an engine verb
as `getattr(engine, "cmd_<verb>")` -- so on a brain that declares `tier0_only=1` (what
`install.sh --tier0-only` records) `voidloom context` answered status=error, "engine has
no cmd_context". Binding the NAME to cmd_prime would not close it: prime prints a text
preamble and the facade parses the engine's last JSON line (measured: the prime output
does not parse as an envelope).

Executed here, each in a subprocess so no module-global tier cache leaks between tests:
  * `python -m voidloom context --json` against a scratch brain declaring tier0_only=1
    -> status ok, code context_ok, data.action == "context", stamped tier 0 by the lite
    engine, rows bounded by --limit, and the brain file BYTE-IDENTICAL afterwards
    (the Tier-0 context is read-only, as the lite engine's recall is);
  * the lite CLI's own `context` subcommand prints the same JSON envelope.
The brain is the packaged blank seed brought up to this package's schema by the
migrator and loaded with the packaged seed pack -- a Tier-0 install, built from the tree.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
SEED_DB = ROOT / "seed" / "claude_memory_blank.db"
SEED_PACK = ROOT / "seed" / "seed_pack.json"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _last_json(text: str):
    for line in reversed([l for l in text.splitlines() if l.strip()]):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    try:
        return json.loads(text)
    except ValueError:
        return None


class TestContextOnATier0Brain(unittest.TestCase):
    def setUp(self):
        for need in (SEED_DB, SEED_PACK, ORGANS / "claude_memory_lite.py", ORGANS / "schema_migrator.py"):
            if not need.is_file():
                raise unittest.SkipTest("tree lacks %s (not a package tree)" % need)
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="t0ctx_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        # an INSTALLED brain's layout: the DB with organs/ beside it (the facade resolves the
        # engine from the DB's directory, as it does on every install); the organs are the tree's own
        os.symlink(str(ORGANS), str(self.tmp / "organs"))
        self.db = self.tmp / "claude_memory.db"
        shutil.copy2(str(SEED_DB), str(self.db))
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "CLAUDE_MEMORY_DB",
                                 "VOIDLOOM_TIER0_ONLY", "PYTHONPATH")}
        self.env.update({"HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1",
                         "WIZARD_LIBRARY_DB": str(self.db), "VOIDLOOM_TESTS_QUIET": "1"})
        mig = subprocess.run([sys.executable, "-B", str(ORGANS / "schema_migrator.py"), "apply", "--db", str(self.db)],
                             capture_output=True, text=True, env=self.env, timeout=300)
        self.assertEqual(mig.returncode, 0, mig.stdout[-800:] + mig.stderr[-800:])
        seed = subprocess.run([sys.executable, "-B", str(ORGANS / "claude_memory_lite.py"), "load-seed",
                               "--file", str(SEED_PACK)], capture_output=True, text=True, env=self.env, timeout=300)
        self.assertEqual(seed.returncode, 0, seed.stderr[-800:])
        c = sqlite3.connect(str(self.db))
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        c.commit()
        self.n = c.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
        c.close()
        self.assertGreater(self.n, 3, "the seed pack loaded too little for a bounded read to mean anything")

    def test_voidloom_context_is_ok_on_a_tier0_brain(self):
        before = _sha(self.db)
        p = subprocess.run([sys.executable, "-B", "-m", "voidloom", "context", "--limit", "3", "--json"],
                           capture_output=True, text=True, env=self.env, cwd=str(ROOT), timeout=300)
        env = _last_json(p.stdout)
        self.assertIsNotNone(env, "no JSON envelope: %r / %r" % (p.stdout[-600:], p.stderr[-600:]))
        self.assertEqual(env.get("status"), "ok", env)
        self.assertEqual(env.get("code"), "context_ok", env)
        data = env.get("data") or {}
        self.assertEqual(data.get("action"), "context", data)
        self.assertEqual(data.get("tier"), 0, "the answer must be stamped as the Tier-0 engine's: %r" % data)
        self.assertEqual(len(data.get("results") or []), 3, data)
        self.assertEqual(data.get("total_memories"), self.n, data)
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        self.assertEqual(before, _sha(self.db), "a Tier-0 context wrote to the brain")

    def test_the_lite_cli_context_prints_the_envelope(self):
        p = subprocess.run([sys.executable, "-B", str(ORGANS / "claude_memory_lite.py"), "context", "--limit", "2"],
                           capture_output=True, text=True, env=self.env, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr[-600:])
        env = _last_json(p.stdout)
        self.assertIsNotNone(env, p.stdout[-600:])
        self.assertEqual((env.get("status"), env.get("action")), ("ok", "context"), env)
        self.assertEqual(env.get("memories_loaded"), 2, env)
        for r in env["results"]:
            self.assertTrue({"id", "content", "summary", "category", "importance", "tags"} <= set(r), r)


if __name__ == "__main__":
    unittest.main()
