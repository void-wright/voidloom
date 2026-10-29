"""The stamp's `weights_hash` is the checksum the resident embedder COMPUTED, read
against the install lock beside the database FIRST (owner decision D2).

THE CLAIM UNDER TEST: a provenance stamp never names bytes nobody checked.
    locked brain    the stamp carries the resident embedder's computed checksum,
                    which must equal the lock's; nothing resident, a different
                    checksum, or a write into a table the lock does not name is a
                    typed refusal and the vector does not land.
    pre-lock brain  a shipped tier carries the computed checksum when a verified
                    embedder is resident (compared to the shipped catalogue entry),
                    else the declared absence 'unresolved'; a tier the catalogue
                    does not ship (jina, personal installs) is 'unresolved'.
And `init` writes the lock for a shipped tier, never for jina, and never re-points
one; the engine's tier read takes the lock first.

Every check ships as a control/saboteur pair (the names carry the pairing). No
model is loaded: the "computed" checksum is registered through the same process
register the one loader writes (embedder_lock.register_loaded), with the shipped
catalogue's value; that the loader computes it from the bytes is
test_embedder_lock's L4/L9.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

NOMIC_TABLE, NOMIC_DIM = "memory_embeddings", 768
JINA_TABLE, JINA_DIM = "memory_embeddings_v2", 1024


def _stub(dim):
    def enc(text: str) -> bytes:
        h = hashlib.sha256(text.encode()).digest()
        return struct.pack(f"{dim}f", *[((h[i % 32] / 255.0) - 0.5) for i in range(dim)])
    return enc


def _vec_available() -> bool:
    try:
        import sqlite_vec  # noqa: F401
        return True
    except Exception:
        return False


def _brain(path: Path, table: str, dim: int) -> sqlite3.Connection:
    import sqlite_vec
    c = sqlite3.connect(str(path))
    c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
    c.executescript(f"""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO metadata VALUES ('active_embedding_table', '{table}');
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                               is_archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
        CREATE VIRTUAL TABLE {table} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{dim}]);
    """)
    import memory_write as mw
    mw.ensure_provenance_table(c)
    c.commit()
    return c


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class TestStampWeightsHash(unittest.TestCase):
    def setUp(self):
        import embedder_lock as el
        self.el = el
        self.cpu = el.catalogue_tiers()["cpu"]
        el.clear_loaded()
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self.el.clear_loaded()
        self._td.cleanup()

    def _register(self, weights_hash=None, table=NOMIC_TABLE, dim=NOMIC_DIM):
        self.el.register_loaded(model_id=self.cpu["model_id"], revision=self.cpu["revision"],
                                weights_hash=weights_hash or self.cpu["weights_hash"],
                                dim=dim, vec_table=table, tier="cpu")

    def _embed(self, conn, table, dim, text="a memory about tides"):
        import memory_write as mw
        mid = conn.execute("INSERT INTO memories (content) VALUES (?)", (text,)).lastrowid
        res = mw.attach_embedding(conn, mid, text, encoder=_stub(dim), policy="require", organ="test")
        return mid, res

    def _stamp(self, conn, mid):
        return conn.execute("SELECT weights_hash, vec_table, dim FROM embedding_provenance "
                            "WHERE memory_id = ?", (mid,)).fetchone()

    def _vectors(self, conn, table):
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # --- locked brain -------------------------------------------------------
    def test_locked_control_stamp_carries_the_computed_checksum(self):
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        self.el.write_lock(self.dir, "cpu")
        self._register()
        mid, res = self._embed(conn, NOMIC_TABLE, NOMIC_DIM)
        conn.commit()
        self.assertTrue(res.get("embedded"), res)
        self.assertEqual(self._stamp(conn, mid), (self.cpu["weights_hash"], NOMIC_TABLE, NOMIC_DIM))
        self.assertRegex(self.cpu["weights_hash"], r"^[0-9a-f]{64}$")

    def test_locked_saboteur_nothing_resident_refuses_and_no_vector_lands(self):
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        self.el.write_lock(self.dir, "cpu")
        with self.assertRaises(Exception) as cm:
            self._embed(conn, NOMIC_TABLE, NOMIC_DIM)
        self.assertIn("WEIGHTS_MISMATCH", str(cm.exception))
        self.assertEqual(self._vectors(conn, NOMIC_TABLE), 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM embedding_provenance").fetchone()[0], 0)

    def test_locked_saboteur_a_different_checksum_refuses(self):
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        self.el.write_lock(self.dir, "cpu")
        self._register(weights_hash="f" * 64)
        with self.assertRaises(Exception) as cm:
            self._embed(conn, NOMIC_TABLE, NOMIC_DIM)
        self.assertIn("WEIGHTS_MISMATCH", str(cm.exception))
        self.assertEqual(self._vectors(conn, NOMIC_TABLE), 0)

    def test_locked_saboteur_a_table_the_lock_does_not_name_refuses(self):
        import memory_write as mw
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        conn.execute(f"CREATE VIRTUAL TABLE {JINA_TABLE} USING vec0(memory_id INTEGER PRIMARY KEY, "
                     f"embedding float[{JINA_DIM}])")
        self.el.write_lock(self.dir, "cpu")
        self._register(table=JINA_TABLE, dim=JINA_DIM)
        mid = conn.execute("INSERT INTO memories (content) VALUES ('x')").lastrowid
        with self.assertRaises(self.el.EmbedderLockError) as cm:
            mw._stamp_provenance(conn, mid, JINA_TABLE, JINA_DIM, "x", "test")
        self.assertEqual(cm.exception.code, "STAMP_INCONSISTENT")

    def test_locked_saboteur_a_malformed_lock_is_never_read_as_absent(self):
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        (self.dir / self.el.LOCK_NAME).write_text("{not json", encoding="utf-8")
        with self.assertRaises(Exception) as cm:
            self._embed(conn, NOMIC_TABLE, NOMIC_DIM)
        self.assertIn("LOCK_MALFORMED", str(cm.exception))
        self.assertEqual(self._vectors(conn, NOMIC_TABLE), 0)

    # --- pre-lock brain (D2) -------------------------------------------------
    def test_prelock_control_resident_shipped_embedder_stamps_its_checksum(self):
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        self._register()
        mid, res = self._embed(conn, NOMIC_TABLE, NOMIC_DIM)
        self.assertEqual(self._stamp(conn, mid)[0], self.cpu["weights_hash"])
        self.assertFalse((self.dir / self.el.LOCK_NAME).exists())

    def test_prelock_control_nothing_resident_states_the_absence(self):
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        mid, res = self._embed(conn, NOMIC_TABLE, NOMIC_DIM)
        self.assertEqual(self._stamp(conn, mid)[0], "unresolved")

    def test_prelock_saboteur_resident_checksum_off_the_catalogue_refuses(self):
        conn = _brain(self.dir / "claude_memory.db", NOMIC_TABLE, NOMIC_DIM)
        self._register(weights_hash="e" * 64)
        with self.assertRaises(Exception) as cm:
            self._embed(conn, NOMIC_TABLE, NOMIC_DIM)
        self.assertIn("WEIGHTS_MISMATCH", str(cm.exception))
        self.assertEqual(self._vectors(conn, NOMIC_TABLE), 0)

    def test_prelock_control_jina_tier_is_unresolved_even_with_an_embedder_resident(self):
        conn = _brain(self.dir / "claude_memory.db", JINA_TABLE, JINA_DIM)
        self._register()                          # a nomic embedder resident: not jina's bytes
        mid, res = self._embed(conn, JINA_TABLE, JINA_DIM)
        self.assertEqual(self._stamp(conn, mid)[0], "unresolved")


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class TestInitWritesTheLock(unittest.TestCase):
    """`claude_memory.py init`, run as the installer runs it: a child process in a
    package box (the engine resolves its brain beside its own organs/)."""

    @classmethod
    def setUpClass(cls):
        import shutil
        cls._tmp = tempfile.TemporaryDirectory(prefix="vl_lockinit_")
        box = Path(cls._tmp.name) / "pkg"
        out = subprocess.run(["git", "ls-files"], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise unittest.SkipTest("git unavailable; cannot build a package box")
        for rel in [p for p in out.stdout.split("\n") if p.strip()]:
            if not (ROOT / rel).is_file():
                continue
            dst = box / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, dst)
        cls.box = box

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        for f in list(self.box.iterdir()):
            if f.name.startswith(("claude_memory.db", "embedder.lock")):
                f.unlink()

    def _env(self, tier_env=None):
        env = {k: v for k, v in os.environ.items() if k not in ("WIZARD_EMBED_MODEL", "WIZARD_LIBRARY_DB")}
        env.update({"CUDA_VISIBLE_DEVICES": "", "PYTHONDONTWRITEBYTECODE": "1"})
        if tier_env is not None:
            env["WIZARD_EMBED_MODEL"] = tier_env
        return env

    def _init(self, d, tier_env=None, *extra):
        return subprocess.run([sys.executable, "organs/claude_memory.py", "init", *extra], cwd=str(self.box),
                              capture_output=True, text=True, env=self._env(tier_env), timeout=300)

    def test_control_nomic_init_writes_a_cpu_lock(self):
        import embedder_lock as el
        if True:
            d = self.box
            r = self._init(d, "nomic")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            lock = el.read_lock(d / el.LOCK_NAME)
            self.assertEqual((lock["tier"], lock["vec_table"], lock["weights_hash"]),
                             ("cpu", NOMIC_TABLE, el.catalogue_tiers()["cpu"]["weights_hash"]))
            self.assertEqual(json.loads(r.stdout)["embedder_lock"]["weights_hash"], lock["weights_hash"])

    def test_control_jina_init_writes_no_lock(self):
        import embedder_lock as el
        if True:
            d = self.box
            r = self._init(d, "jina")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            self.assertFalse((d / el.LOCK_NAME).exists())
            self.assertIsNone(json.loads(r.stdout)["embedder_lock"])

    def test_saboteur_a_rerun_naming_another_tier_does_not_repoint_the_lock(self):
        import embedder_lock as el
        if True:
            d = self.box
            self.assertEqual(self._init(d, "nomic").returncode, 0)
            before = (d / el.LOCK_NAME).read_bytes()
            r = self._init(d, "qwen")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("WEIGHTS_MISMATCH", r.stdout + r.stderr)
            self.assertEqual((d / el.LOCK_NAME).read_bytes(), before)

    def test_tier_read_takes_the_lock_first_and_refuses_a_disagreeing_variable(self):
        if True:
            d = self.box
            self.assertEqual(self._init(d, "nomic").returncode, 0)
            # a metadata row cannot move a locked brain
            c = sqlite3.connect(str(d / "claude_memory.db"))
            c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES "
                      "('active_embedding_table', 'memory_embeddings_v3')")
            c.commit(); c.close()
            code = ("import sys; sys.path.insert(0, 'organs'); import claude_memory as cm; "
                    "print(cm._active_embed_model())")
            env = self._env()
            r = subprocess.run([sys.executable, "-c", code], cwd=str(d), capture_output=True, text=True,
                               env=env, timeout=120)
            self.assertEqual(r.stdout.strip().splitlines()[-1], "nomic", r.stderr[-1500:])
            env["WIZARD_EMBED_MODEL"] = "qwen"
            r = subprocess.run([sys.executable, "-c", code], cwd=str(d), capture_output=True, text=True,
                               env=env, timeout=120)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("WEIGHTS_MISMATCH", r.stderr)


if __name__ == "__main__":
    unittest.main()
