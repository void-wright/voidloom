"""The installer reads the brain's embedding table LOCK FIRST, at every site, through one function
(organs/embed_resolver.declared_table).

install.sh decides the tier the brain keeps (the ambient gate and the tier post-condition), records it, checks the
brain's sanity, declares a Tier-0-born brain's tier and verifies Tier 1. Each of those read
metadata.active_embedding_table directly, so on a locked brain whose metadata disagrees (an interrupted
`voidloom tier set` leaves exactly that) the installer acted on another model's table.
  * control: a lockless brain is read from its metadata, as before;
  * saboteur: a locked brain whose metadata names another table is read from the LOCK;
  * saboteur: a lock that cannot be used reads as its refusal code, never as the metadata row;
  * static: install.sh reads metadata.active_embedding_table in exactly one place, the lockless branch of the
    model reader that already checks the lock first; every other site calls declared_table.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT / "organs"))

NOMIC = ("memory_embeddings", 768, "nomic-ai/nomic-embed-text-v1.5")
JINA = ("memory_embeddings_v2", 1024, "jinaai/jina-embeddings-v5-text-nano")


def make_brain(d: Path, rows, lock_tier=None) -> Path:
    db = d / "claude_memory.db"
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
    if rows is not None:
        c.execute("INSERT INTO metadata(key, value) VALUES ('active_embedding_table', ?)", (rows[0],))
    c.execute("INSERT INTO metadata(key, value) VALUES ('tier0_only', '0')")
    c.commit()
    c.close()
    if lock_tier:
        import embedder_lock
        embedder_lock.write_lock(d, lock_tier)
    return db


def malform(d: Path) -> None:
    lp = d / "embedder.lock.json"
    lock = json.loads(lp.read_text(encoding="utf-8"))
    lock["vec_table"] = "memory_embeddings_v9"
    lp.write_text(json.dumps(lock), encoding="utf-8")


class DeclaredTableIsLockFirst(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_decl_")
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        import embed_resolver
        self.er = embed_resolver

    def _read(self, db):
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return self.er.declared_table(c)
        finally:
            c.close()

    def _brain_decl(self, db) -> str:
        """install.sh's own brain_decl, run as the installer runs it."""
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        i = sh.index("brain_decl() {")
        j = sh.index("\nPYEOF\n}\n", i) + len("\nPYEOF\n}\n")
        script = "%s\nPY_SYS=%s\nPKG_ROOT=%s\nDB_PATH=%s\nbrain_decl\n" % (sh[i:j], sys.executable, ROOT, db)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_control_a_lockless_brain_reads_its_metadata(self):
        db = make_brain(self.d, JINA)
        self.assertEqual(self._read(db), ("memory_embeddings_v2", "metadata", ""))
        self.assertEqual(self._brain_decl(db), "memory_embeddings_v2|0")

    def test_saboteur_a_locked_brain_with_other_metadata_reads_the_lock(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        self.assertEqual(self._read(db), ("memory_embeddings", "lock", ""))
        self.assertEqual(self._brain_decl(db), "memory_embeddings|0")

    def test_saboteur_an_unusable_lock_is_its_refusal_never_the_metadata(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        malform(self.d)
        self.assertEqual(self._read(db), ("", "lock", "LOCK_MALFORMED"))
        self.assertEqual(self._brain_decl(db), "LOCK_MALFORMED|0")

    def test_control_no_declaration_anywhere_reads_empty(self):
        db = make_brain(self.d, None)
        self.assertEqual(self._read(db), ("", "metadata", ""))
        self.assertEqual(self._brain_decl(db), "|0")


class InstallerHasOneMetadataTableRead(unittest.TestCase):
    def test_static_every_other_site_goes_through_declared_table(self):
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        raw = [m.start() for m in re.finditer(r"SELECT value FROM metadata WHERE key='active_embedding_table'", sh)]
        self.assertEqual(len(raw), 1, "install.sh reads metadata.active_embedding_table at %d sites" % len(raw))
        f = sh.rindex("hd_embed_model_from_db() {", 0, raw[0])
        self.assertLess(sh.index("embedder.lock.json", f), raw[0], "the one raw read is not behind the lock check")
        self.assertEqual(sh.count("embed_resolver.declared_table(c)"), 5)


if __name__ == "__main__":
    unittest.main()
