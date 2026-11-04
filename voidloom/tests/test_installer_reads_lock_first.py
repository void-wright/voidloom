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
    model reader that already checks the lock first; every other site calls declared_table;
  * each of the other four sites (the record, the sanity line, Step C's declaration, the verify gate) run as install.sh
    carries it, on a brain whose metadata names jina and whose lock names nomic.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import struct
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


def heredoc_after(anchor: str) -> str:
    """The body of the first quoted heredoc that starts after `anchor` in install.sh: the block as the installer runs it."""
    src = (ROOT / "install.sh").read_text(encoding="utf-8")
    m = re.compile(r"<<'([A-Z0-9_]+)'[^\n]*\n").search(src, src.index(anchor))
    return src[m.end():src.index("\n%s\n" % m.group(1), m.end()) + 1]


def vec_ok() -> bool:
    try:
        import sqlite_vec
        c = sqlite3.connect(":memory:")
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.close()
        return True
    except Exception:
        return False


def vec_conn(db):
    import sqlite_vec
    c = sqlite3.connect(str(db))
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    return c


def add_vectors(db, table, dim, n):
    c = vec_conn(db)
    c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS %s USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[%d])" % (table, dim))
    for i in range(1, n + 1):
        c.execute("INSERT INTO %s (memory_id, embedding) VALUES (?, ?)" % table, (i, struct.pack("%df" % dim, *([0.1] * dim))))
    c.commit()
    c.close()


def meta_row(db):
    c = sqlite3.connect(str(db))
    try:
        r = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
        return r[0] if r else None
    finally:
        c.close()


class _Site(unittest.TestCase):
    """Each remaining site run as install.sh carries it, on a brain whose lock and metadata disagree. The brain says
    jina in its metadata; the lock says nomic. A site that read the row would act on the wrong table."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_site_")
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)

    def _env(self, **kw):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env.update({"ORGANS_DIR": str(ROOT / "organs")}, **kw)
        return env


class TheInstallRecordIsTheLocks(_Site):
    def _record(self, db):
        out = self.d / "record"
        p = subprocess.run([sys.executable, "-", str(out)], input=heredoc_after("write_install_record() {"),
                           capture_output=True, text=True, timeout=120, cwd=str(self.d),
                           env=self._env(REC_PKG_ROOT=str(ROOT), REC_DB=str(db), REC_SHAPE_V="venv", REC_PY=sys.executable))
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        line = [x for x in out.read_text(encoding="utf-8").splitlines() if x.startswith("active_embedding_table=")]
        self.assertEqual(len(line), 1, out.read_text(encoding="utf-8"))
        return line[0].split("=", 1)[1]

    def test_control_a_lockless_brain_is_recorded_from_its_metadata(self):
        self.assertEqual(self._record(make_brain(self.d, JINA)), "memory_embeddings_v2")

    def test_saboteur_a_locked_brain_is_recorded_from_its_lock(self):
        self.assertEqual(self._record(make_brain(self.d, JINA, lock_tier="cpu")), "memory_embeddings")

    def test_saboteur_an_unusable_lock_is_recorded_as_that_never_as_the_metadata(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        malform(self.d)
        self.assertEqual(self._record(db), "unreadable: LOCK_MALFORMED")


class TheSanityLineNamesTheLocksTable(_Site):
    def _sanity(self, db):
        return subprocess.run([sys.executable, "-", str(db), "memory_embeddings", str(ROOT), str(ROOT / "organs")],
                              input=heredoc_after("# B.2 — sanity"), capture_output=True, text=True, timeout=180,
                              cwd=str(self.d), env=self._env())

    def _full_brain(self, lock_tier=None):
        """A brain with every object this package builds, declaring jina in its metadata."""
        db = self.d / "claude_memory.db"
        shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(db))
        p = subprocess.run([sys.executable, str(ROOT / "organs" / "schema_migrator.py"), "apply", "--db", str(db), "--json"],
                           capture_output=True, text=True, timeout=120, env=self._env())
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        c = sqlite3.connect(str(db))
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', ?)", (JINA[0],))
        c.commit()
        c.close()
        if lock_tier:
            import embedder_lock
            embedder_lock.write_lock(self.d, lock_tier)
        return db

    def _built_count(self, out):
        m = re.search(r"\(>= the (\d+) this package builds\)", out)
        self.assertIsNotNone(m, out)
        return int(m.group(1))

    def test_control_a_lockless_brain_names_its_metadata(self):
        p = self._sanity(self._full_brain())
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("active embedding table = memory_embeddings_v2.", p.stdout)
        self.assertGreater(self._built_count(p.stdout), 0)

    def test_saboteur_a_locked_brain_names_its_lock(self):
        p = self._sanity(self._full_brain(lock_tier="cpu"))
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("active embedding table = memory_embeddings.", p.stdout)
        self.assertGreater(self._built_count(p.stdout), 0, "the lock's refusal slot overwrote the package's object set")

    def test_saboteur_an_unusable_lock_is_named_never_the_metadata(self):
        db = self._full_brain(lock_tier="cpu")
        malform(self.d)
        p = self._sanity(db)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("active embedding table = (unreadable: LOCK_MALFORMED).", p.stdout)
        self.assertGreater(self._built_count(p.stdout), 0)


@unittest.skipUnless(vec_ok(), "sqlite_vec is not loadable here (these blocks run in the Tier-1 venv)")
class StepCKeepsTheLocksDeclaration(_Site):
    def _declare(self, db):
        from voidloom.tests.test_stranger_tier1_upgrade import _engine_registry_tsv
        p = subprocess.run([sys.executable, "-", str(db)], input=heredoc_after("hd_t1_declare() {"), capture_output=True,
                           text=True, timeout=120, cwd=str(self.d),
                           env=self._env(EMBED_TABLE=NOMIC[0], EMBED_DIM=str(NOMIC[1]), EMBED_REPO=NOMIC[2],
                                         ENGINE_REGISTRY=_engine_registry_tsv(ROOT)))
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return p.stdout.strip().split("\t")

    def test_control_a_lockless_brain_keeps_its_own_declaration(self):
        db = make_brain(self.d, JINA)
        self.assertEqual(self._declare(db)[:3], ["OK", "kept", JINA[0]])

    def test_saboteur_a_locked_brain_keeps_the_locks_table_and_its_metadata_is_not_rewritten(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        self.assertEqual(self._declare(db)[:3], ["OK", "kept", NOMIC[0]])
        self.assertEqual(meta_row(db), JINA[0], "Step C re-declared a locked brain's metadata")

    def test_saboteur_an_unusable_lock_changes_nothing(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        malform(self.d)
        out = self._declare(db)
        self.assertEqual(out[0], "ERROR", out)
        self.assertIn("LOCK_MALFORMED", out[1])
        self.assertEqual(meta_row(db), JINA[0])


@unittest.skipUnless(vec_ok(), "sqlite_vec is not loadable here (the gate counts a vec0 table)")
class TheVerifyGateCountsTheLocksTable(_Site):
    def setUp(self):
        super().setUp()
        self.brain = self.d / "brain"
        (self.brain / "voidloom").mkdir(parents=True)
        data = {"status": "ok", "mode": "hybrid", "tier": 1, "result_count": 1, "results": [{"id": 1}]}
        (self.brain / "voidloom" / "__init__.py").write_text("", encoding="utf-8")
        (self.brain / "voidloom" / "__main__.py").write_text(
            "import json\nprint(json.dumps(%r))\n" % ({"status": "ok", "data": data},), encoding="utf-8")

    def _gate(self, db):
        return subprocess.run([sys.executable, "-"], input=heredoc_after("hd_t1_verify() {"), capture_output=True, text=True,
                              timeout=120, cwd=str(self.d),
                              env=self._env(WIZARD_LIBRARY_DB=str(db), WIZARD_LIBRARY_HOME=str(self.brain),
                                            BRAIN_DIR=str(self.brain), WIZARD_EMBED_MODEL="nomic"))

    def test_control_a_lockless_brain_is_verified_on_its_metadata_table(self):
        db = make_brain(self.d, JINA)
        add_vectors(db, JINA[0], JINA[1], 2)
        p = self._gate(db)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("VERIFIED tier=1", p.stdout)
        self.assertIn("vectors=2", p.stdout)
        self.assertIn("table=%s" % JINA[0], p.stdout)

    def test_saboteur_a_locked_brain_is_verified_on_the_locks_table(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        add_vectors(db, NOMIC[0], NOMIC[1], 1)
        add_vectors(db, JINA[0], JINA[1], 2)
        p = self._gate(db)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("vectors=1", p.stdout)
        self.assertIn("table=%s" % NOMIC[0], p.stdout)

    def test_saboteur_vectors_in_the_metadata_table_do_not_pass_an_empty_lock_table(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        add_vectors(db, NOMIC[0], NOMIC[1], 0)
        add_vectors(db, JINA[0], JINA[1], 2)
        p = self._gate(db)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("%s holds 0 vectors" % NOMIC[0], p.stderr)

    def test_saboteur_an_unusable_lock_verifies_nothing(self):
        db = make_brain(self.d, JINA, lock_tier="cpu")
        add_vectors(db, JINA[0], JINA[1], 2)
        malform(self.d)
        p = self._gate(db)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertIn("LOCK_MALFORMED", p.stderr)


if __name__ == "__main__":
    unittest.main()
