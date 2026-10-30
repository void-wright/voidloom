"""`tier set`: a brain changes embedding model only by re-embedding every memory.

THE CLAIM UNDER TEST
  * every memory -- archived ones included -- gets a vector in the TARGET table with a
    stamp naming the target's checksum and its current text, while the old tier keeps
    serving; the switch (lock + metadata rows, together) happens only at 100%;
  * the OLD vectors and stamps are kept, byte for byte (the rollback path);
  * a refusal (unshipped target, a loader that cannot verify its bytes) and an
    interrupted run leave the lock and the metadata rows exactly as they were;
  * rollback restores the old lock (or its absence) and rows; cleanup is separate,
    explicit and confirmed, and retires the rollback.
Sources covered: nomic -> qwen, qwen -> nomic, jina -> qwen. Every check is a
control/saboteur pair (the names carry the pairing).

No model is loaded. The two seams tier_set exposes (`_load`, `_encode`) are replaced
by a stand-in that registers the target's SHIPPED checksum in the same process
register the real loader writes, and encodes deterministically per model id, so a
vector's producer is recoverable from its bytes. That the real loader computes the
checksum from the bytes on disk is test_embedder_lock's L4/L9; one saboteur below
runs the REAL loader against an empty, offline model cache.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

TABLES = {"nomic": ("memory_embeddings", 768, "nomic-ai/nomic-embed-text-v1.5", "cpu"),
          "jina": ("memory_embeddings_v2", 1024, "jinaai/jina-embeddings-v5-text-small", None),
          "qwen": ("memory_embeddings_v3", 1024, "Qwen/Qwen3-Embedding-0.6B", "gpu")}


def _vec_available() -> bool:
    try:
        import sqlite_vec  # noqa: F401
        return True
    except Exception:
        return False


def _vec(model_id: str, text: str, dim: int) -> bytes:
    h = hashlib.sha256((model_id + "\0" + text).encode()).digest()
    return struct.pack(f"{dim}f", *[((h[i % 32] / 255.0) - 0.5) for i in range(dim)])


def fake_load(lock):
    import embedder_lock as el
    el.clear_loaded()
    el.register_loaded(model_id=lock["model_id"], revision=lock["revision"], weights_hash=lock["weights_hash"],
                       dim=int(lock["dim"]), vec_table=lock["vec_table"], tier=lock.get("tier"))


def fake_encode(texts, lock):
    return [_vec(lock["model_id"], t, int(lock["dim"])) for t in texts]


def _connect(path: Path) -> sqlite3.Connection:
    import sqlite_vec
    c = sqlite3.connect(str(path))
    c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
    return c


def make_brain(d: Path, source: str, n: int = 7, archived=(2, 5)) -> Path:
    """A small brain embedded under `source`: every memory has a source vector and a
    stamp; a shipped source carries an install lock, jina (D2) carries none."""
    import embedder_lock as el
    import memory_write as mw
    table, dim, model, cat = TABLES[source]
    db = d / "claude_memory.db"
    c = _connect(db)
    c.executescript(f"""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                               is_archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
        CREATE VIRTUAL TABLE memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768]);
        CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{dim}]);
    """)
    for k, v in (("active_embedding_table", table), ("active_embedding_model", model),
                 ("active_embedding_dim", str(dim))):
        c.execute("INSERT INTO metadata VALUES (?, ?)", (k, v))
    mw.ensure_provenance_table(c)
    for i in range(1, n + 1):
        text = f"memory {i} about tides and lanterns"
        c.execute("INSERT INTO memories (content, is_archived) VALUES (?, ?)", (text, int(i in archived)))
        c.execute(f"INSERT INTO {table} (memory_id, embedding) VALUES (?, ?)", (i, _vec(model, text, dim)))
        c.execute("INSERT INTO embedding_provenance VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (i, table, model, "r", "unresolved" if cat is None else el.catalogue_tiers()[cat]["weights_hash"],
                   dim, mw.content_hash(text), "2026-10-01T00:00:00+00:00", "seed", "text", None))
    c.commit(); c.close()
    if cat:
        el.write_lock(d, cat)
    return db


def snapshot(db: Path, table: str):
    c = _connect(db)
    try:
        vecs = c.execute(f"SELECT memory_id, embedding FROM {table} ORDER BY memory_id").fetchall()
        stamps = c.execute("SELECT * FROM embedding_provenance WHERE vec_table = ? ORDER BY memory_id",
                           (table,)).fetchall()
        meta = c.execute("SELECT key, value FROM metadata ORDER BY key").fetchall()
    finally:
        c.close()
    return vecs, stamps, meta


def lock_bytes(d: Path):
    p = d / "embedder.lock.json"
    return p.read_bytes() if p.exists() else None


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class TestTierSet(unittest.TestCase):
    def setUp(self):
        import embedder_lock as el
        import tier_set
        self.el, self.ts = el, tier_set
        el.clear_loaded()
        self._seams = (tier_set._load, tier_set._encode)
        tier_set._load, tier_set._encode = fake_load, fake_encode
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self.ts._load, self.ts._encode = self._seams
        self.el.clear_loaded()
        self._td.cleanup()

    # --- helpers -------------------------------------------------------------
    def _assert_switched(self, db: Path, target: str, n: int):
        table, dim, model, cat = TABLES[target]
        lock = self.el.resolve(db)
        self.assertEqual((lock["tier"], lock["vec_table"], lock["dim"]), (cat, table, dim))
        vecs, stamps, meta = snapshot(db, table)
        self.assertEqual(len(vecs), n, "every memory, archived included, has a target vector")
        c = _connect(db)
        texts = dict(c.execute("SELECT id, content FROM memories").fetchall())
        c.close()
        for mid, blob in vecs:
            self.assertEqual(blob, _vec(model, texts[mid], dim), f"memory {mid}: produced by the target model")
        import memory_write as mw
        want_hash = self.el.catalogue_tiers()[cat]["weights_hash"]
        for s in stamps:
            self.assertEqual((s[2], s[4], s[5], s[6], s[9]),
                             (model, want_hash, dim, mw.content_hash(texts[s[0]]), "text"))
        self.assertEqual(dict(meta)["active_embedding_table"], table)
        self.assertEqual(dict(meta)["active_embedding_model"], model)
        self.assertEqual(dict(meta)["active_embedding_dim"], str(dim))
        import embed_resolver
        c = _connect(db)
        self.assertEqual(embed_resolver.resolve(c), (table, dim), "lock-first readers moved")
        c.close()

    def _move(self, source: str, target: str):
        db = make_brain(self.dir, source)
        old_table = TABLES[source][0]
        before = snapshot(db, old_table)
        env = self.ts.set_tier(db, target, batch_size=3)
        self.assertEqual(env["status"], "ok", env)
        self._assert_switched(db, target, 7)
        after = snapshot(db, old_table)
        self.assertEqual(after[0], before[0], "old vectors kept byte for byte (the rollback path)")
        self.assertEqual(after[1], before[1], "old stamps kept")
        hist = [json.loads(x) for x in (self.dir / "embedder.lock.history.jsonl").read_text().splitlines()]
        self.assertEqual(hist[-1]["action"], "tier-set")
        self.assertEqual(hist[-1]["embedded"], 7)
        self.assertIn("not recomputed", hist[-1]["derived_layer"])
        self.assertTrue((self.dir / "embedder.lock.preimage.json").exists())
        return db, env

    # --- nomic -> qwen ---------------------------------------------------------
    def test_nomic_to_qwen_control_re_embeds_everything_and_switches(self):
        db, env = self._move("nomic", "qwen")
        self.assertEqual(env["receipt"]["from"]["had_lock"], True)
        self.assertEqual(env["receipt"]["coverage"], {"total": 7, "matched": 7, "stamps_without_memory": 0})

    def test_nomic_to_qwen_saboteur_unshipped_target_refuses_and_writes_nothing(self):
        db = make_brain(self.dir, "nomic")
        lock0, file0 = lock_bytes(self.dir), db.read_bytes()
        for bad in ("jina", "qwen3", ""):
            env = self.ts.run(["--db", str(db), "set", bad]) if bad else self.ts.run(["--db", str(db), "set", " "])
            self.assertEqual(env["error"], "LOCK_NOT_SHIPPED", env)
        self.assertEqual((lock_bytes(self.dir), db.read_bytes()), (lock0, file0))

    def test_nomic_to_qwen_saboteur_resident_weights_not_the_targets_refuse_and_nothing_switches(self):
        """The resident embedder's COMPUTED checksum is not the target lock's: no vector is
        written under the target, and the lock and rows stay where they were."""
        db = make_brain(self.dir, "nomic")
        lock0, meta0 = lock_bytes(self.dir), snapshot(db, "memory_embeddings")[2]

        def foreign_load(lock):
            import embedder_lock as el
            el.clear_loaded()
            el.register_loaded(model_id=lock["model_id"], revision=lock["revision"], weights_hash="f" * 64,
                               dim=int(lock["dim"]), vec_table=lock["vec_table"], tier=lock.get("tier"))
        self.ts._load = foreign_load
        with self.assertRaises(self.el.EmbedderLockError) as cm:
            self.ts.set_tier(db, "qwen")
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")
        self.assertEqual(snapshot(db, "memory_embeddings_v3")[:2], ([], []))
        self.assertEqual((lock_bytes(self.dir), snapshot(db, "memory_embeddings")[2]), (lock0, meta0))

    # --- qwen -> nomic ---------------------------------------------------------
    def test_qwen_to_nomic_control_re_embeds_everything_and_switches(self):
        self._move("qwen", "nomic")

    def test_qwen_to_nomic_saboteur_loader_cannot_verify_bytes_brain_as_found(self):
        """The REAL loader, against an empty and offline model cache: it refuses before
        anything is written -- no target table, no log row, lock and file unchanged."""
        self.ts._load = self._seams[0]
        db = make_brain(self.dir, "qwen")
        lock0, file0 = lock_bytes(self.dir), db.read_bytes()
        hf = self.dir / "empty_hf"
        keep = {k: os.environ.get(k) for k in ("HF_HOME", "HF_HUB_CACHE", "HF_HUB_OFFLINE")}
        os.environ.update({"HF_HOME": str(hf), "HF_HUB_CACHE": str(hf / "hub"), "HF_HUB_OFFLINE": "1"})
        try:
            env = self.ts.run(["--db", str(db), "set", "nomic"])
        finally:
            for k, v in keep.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.assertEqual(env["status"], "error", env)
        self.assertEqual(env["error"], "SNAPSHOT_ABSENT", env)
        self.assertIn("model_fetch.py", json.dumps(env))
        self.assertEqual((lock_bytes(self.dir), db.read_bytes()), (lock0, file0))

    # --- jina -> qwen (a lockless personal brain, D2) --------------------------
    def test_jina_to_qwen_control_lockless_source_gets_a_lock(self):
        self.assertIsNone(lock_bytes(self.dir))
        db, env = self._move("jina", "qwen")
        pre = json.loads((self.dir / "embedder.lock.preimage.json").read_text())
        self.assertIsNone(pre["previous_lock_raw"], "the pre-image records that there was NO lock")
        self.assertEqual(pre["previous_metadata"]["active_embedding_table"], "memory_embeddings_v2")

    def test_jina_to_qwen_saboteur_unresolved_or_stale_target_rows_are_never_reused(self):
        """A target row whose stamp is 'unresolved', or whose text changed after it was
        stamped, is re-embedded -- bytes nobody checked are not carried across."""
        import memory_write as mw
        db = make_brain(self.dir, "jina")
        table, dim, model, _cat = TABLES["qwen"]
        c = _connect(db)
        c.execute(f"CREATE VIRTUAL TABLE {table} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{dim}])")
        c.execute(f"INSERT INTO {table} VALUES (1, ?)", (b"\0" * 4 * dim,))
        c.execute("INSERT INTO embedding_provenance VALUES (1,?,?,?,?,?,?,?,?,?,?)",
                  (table, model, "r", "unresolved", dim, mw.content_hash("memory 1 about tides and lanterns"),
                   "2026-10-01T00:00:00+00:00", "x", "text", None))
        c.commit(); c.close()
        self.assertEqual(self.ts.plan(db, "qwen")["pending"], 7)
        env = self.ts.set_tier(db, "qwen")
        self.assertEqual((env["status"], env["receipt"]["embedded"]), ("ok", 7), env)
        self._assert_switched(db, "qwen", 7)
        # stale text: edit memory 3 after the switch -> it is work again, and only it
        c = _connect(db); c.execute("UPDATE memories SET content = 'edited' WHERE id = 3"); c.commit(); c.close()
        self.assertEqual(self.ts.plan(db, "qwen")["pending"], 1)
        env = self.ts.set_tier(db, "qwen")
        self.assertEqual((env["receipt"]["embedded"], env["receipt"]["already_on_target"]), (1, True))
        self._assert_switched(db, "qwen", 7)

    # --- interruption ----------------------------------------------------------
    def test_interrupted_control_rerun_resumes_and_reuses_finished_rows(self):
        db = make_brain(self.dir, "nomic")
        calls = {"n": 0}

        def dies_on_second_batch(texts, lock):
            calls["n"] += 1
            if calls["n"] == 2:
                raise KeyboardInterrupt("killed mid-build")
            return fake_encode(texts, lock)
        self.ts._encode = dies_on_second_batch
        with self.assertRaises(KeyboardInterrupt):
            self.ts.set_tier(db, "qwen", batch_size=3)
        self.ts._encode = fake_encode
        self.assertEqual(self.ts.plan(db, "qwen")["pending"], 4, "batch 1 (3 rows) committed, the rest is work")
        env = self.ts.set_tier(db, "qwen", batch_size=3)
        self.assertEqual((env["receipt"]["embedded"], env["receipt"]["reused"]), (4, 3), env)
        self._assert_switched(db, "qwen", 7)

    def test_interrupted_saboteur_old_tier_still_serves_and_lock_unmoved(self):
        db = make_brain(self.dir, "nomic")
        lock0 = lock_bytes(self.dir)
        meta0 = snapshot(db, "memory_embeddings")[2]

        def dies(texts, lock):
            raise KeyboardInterrupt("killed")
        self.ts._encode = dies
        with self.assertRaises(KeyboardInterrupt):
            self.ts.set_tier(db, "qwen")
        self.assertEqual(lock_bytes(self.dir), lock0, "the lock did not move")
        self.assertEqual(snapshot(db, "memory_embeddings")[2], meta0, "the metadata rows did not move")
        self.assertFalse((self.dir / "embedder.lock.preimage.json").exists())
        import embed_resolver
        c = _connect(db)
        self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings", 768), "the old tier serves")
        c.close()

    def test_interrupted_saboteur_sigkill_mid_build_leaves_lock_and_rows(self):
        """A real SIGKILL in a child process, after the first committed batch."""
        db = make_brain(self.dir, "qwen")
        lock0 = lock_bytes(self.dir)
        meta0 = snapshot(db, "memory_embeddings_v3")[2]
        child = (
            "import os, signal, sys\n"
            f"sys.path[:0] = [{str(ROOT / 'organs')!r}, {str(Path(__file__).parent)!r}]\n"
            "import tier_set, test_tier_set as T\n"
            "n = {'c': 0}\n"
            "def enc(texts, lock):\n"
            "    n['c'] += 1\n"
            "    if n['c'] == 2:\n"
            "        os.kill(os.getpid(), signal.SIGKILL)\n"
            "    return T.fake_encode(texts, lock)\n"
            "tier_set._load, tier_set._encode = T.fake_load, enc\n"
            f"tier_set.set_tier(tier_set.Path({str(db)!r}), 'nomic', batch_size=3)\n")
        p = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, timeout=120,
                           env={**os.environ, "CUDA_VISIBLE_DEVICES": ""})
        self.assertEqual(p.returncode, -signal.SIGKILL, p.stderr[-2000:])
        self.assertEqual(lock_bytes(self.dir), lock0)
        self.assertEqual(snapshot(db, "memory_embeddings_v3")[2], meta0)
        self.assertEqual(self.ts.plan(db, "nomic")["pending"], 4)
        env = self.ts.set_tier(db, "nomic", batch_size=3)
        self.assertEqual(env["status"], "ok", env)
        self._assert_switched(db, "nomic", 7)

    # --- a writer during the build ---------------------------------------------
    def test_concurrent_store_control_the_switch_covers_a_memory_stored_mid_build(self):
        db = make_brain(self.dir, "nomic")
        calls = {"n": 0}

        def store_during_build(texts, lock):
            calls["n"] += 1
            if calls["n"] == 1:
                c = _connect(db)
                c.execute("INSERT INTO memories (content) VALUES ('stored while the build ran')")
                c.commit(); c.close()
            return fake_encode(texts, lock)
        self.ts._encode = store_during_build
        env = self.ts.set_tier(db, "qwen", batch_size=10)
        self.assertEqual(env["status"], "ok", env)
        self._assert_switched(db, "qwen", 8)

    # --- rollback and cleanup ----------------------------------------------------
    def test_rollback_control_restores_the_old_lock_rows_and_table(self):
        db = make_brain(self.dir, "nomic")
        lock0 = lock_bytes(self.dir)
        before = snapshot(db, "memory_embeddings")
        self.assertEqual(self.ts.set_tier(db, "qwen")["status"], "ok")
        c = _connect(db)
        c.execute("INSERT INTO memories (content) VALUES ('written after the switch')")
        c.commit(); c.close()
        env = self.ts.rollback(db)
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(lock_bytes(self.dir), lock0, "the old lock, byte for byte")
        after = snapshot(db, "memory_embeddings")
        self.assertEqual(after, before, "old vectors, stamps and metadata rows as before the switch")
        self.assertEqual(env["receipt"]["memories_without_a_restored_vector"], 1)
        self.assertIn("tier set nomic", env["gap_fill"])

    def test_rollback_saboteur_second_rollback_and_lockless_source(self):
        db = make_brain(self.dir, "jina")
        self.assertEqual(self.ts.set_tier(db, "qwen")["status"], "ok")
        self.assertIsNotNone(lock_bytes(self.dir))
        env = self.ts.rollback(db)
        self.assertEqual(env["status"], "ok", env)
        self.assertIsNone(lock_bytes(self.dir), "a lockless brain is lockless again")
        import embed_resolver
        c = _connect(db)
        self.assertEqual(embed_resolver.resolve(c), ("memory_embeddings_v2", 1024))
        c.close()
        self.assertEqual(self.ts.run(["--db", str(db), "rollback"])["error"], "NOTHING_TO_ROLL_BACK")

    def test_cleanup_control_empties_the_old_table_with_a_receipt_and_retires_rollback(self):
        db = make_brain(self.dir, "jina")
        self.assertEqual(self.ts.set_tier(db, "qwen")["status"], "ok")
        env = self.ts.cleanup(db, "memory_embeddings_v2")
        self.assertEqual((env["receipt"]["vectors_removed"], env["receipt"]["stamps_removed"]), (7, 7), env)
        self.assertEqual(snapshot(db, "memory_embeddings_v2")[:2], ([], []))
        self._assert_switched(db, "qwen", 7)
        self.assertEqual(self.ts.run(["--db", str(db), "rollback"])["error"], "NOTHING_TO_ROLL_BACK")

    def test_cleanup_saboteur_active_or_unnamed_table_refuses_and_removes_nothing(self):
        db = make_brain(self.dir, "nomic")
        self.assertEqual(self.ts.set_tier(db, "qwen")["status"], "ok")
        file0 = db.read_bytes()
        for confirm in (None, "memory_embeddings_v3", "memories"):
            argv = ["--db", str(db), "cleanup"] + (["--confirm", confirm] if confirm else [])
            env = self.ts.run(argv)
            self.assertEqual(env["status"], "error", env)
            self.assertIn(env["error"], ("CLEANUP_UNCONFIRMED", "CLEANUP_REFUSED"))
        self.assertEqual(db.read_bytes(), file0)

    # --- crash between the lock rename and the commit -------------------------
    def test_flip_crash_control_resume_keeps_the_original_from_state_for_rollback(self):
        """The lock is renamed, then the process dies before COMMIT: lock=target, rows=old.
        The re-run finishes the switch WITHOUT overwriting the pre-image with the moved lock,
        so rollback still restores the ORIGINAL lock bytes."""
        db = make_brain(self.dir, "nomic")
        lock0, before = lock_bytes(self.dir), snapshot(db, "memory_embeddings")
        real_write = self.el.write_lock

        def write_then_die(*a, **k):
            real_write(*a, **k)
            raise KeyboardInterrupt("died after the rename, before COMMIT")
        self.el.write_lock = write_then_die
        try:
            with self.assertRaises(KeyboardInterrupt):
                self.ts.set_tier(db, "qwen")
        finally:
            self.el.write_lock = real_write
        self.assertEqual(self.el.resolve(db)["tier"], "gpu")
        self.assertEqual(dict(snapshot(db, "memory_embeddings")[2])["active_embedding_table"], "memory_embeddings")
        env = self.ts.set_tier(db, "qwen")
        self.assertEqual((env["status"], env["receipt"]["resumed_after_crash"]), ("ok", True), env)
        self._assert_switched(db, "qwen", 7)
        self.assertEqual(self.ts.rollback(db)["status"], "ok")
        self.assertEqual(lock_bytes(self.dir), lock0, "the ORIGINAL lock, not the half-moved one")
        self.assertEqual(snapshot(db, "memory_embeddings"), before)

    # --- same table, other weights ----------------------------------------------
    def test_same_table_saboteur_other_weights_refuse_and_control_same_weights_reconverge(self):
        db = make_brain(self.dir, "nomic")
        lp = self.dir / "embedder.lock.json"
        good = lp.read_text()
        lp.write_text(good.replace(self.el.catalogue_tiers()["cpu"]["weights_hash"], "a" * 64))
        file0 = db.read_bytes()
        env = self.ts.run(["--db", str(db), "set", "nomic"])
        self.assertEqual(env["error"], "SAME_TABLE_REVISION_CHANGE", env)
        self.assertEqual(db.read_bytes(), file0)
        lp.write_text(good)
        env = self.ts.set_tier(db, "nomic")
        self.assertEqual((env["status"], env["receipt"]["embedded"], env["receipt"]["already_on_target"]),
                         ("ok", 0, True), env)

    # --- an environment pin that disagrees ---------------------------------------
    def test_env_pin_saboteur_disagreeing_pin_refuses_and_control_agreeing_pin_proceeds(self):
        db = make_brain(self.dir, "jina")
        keep = os.environ.get("WIZARD_EMBED_MODEL")
        try:
            os.environ["WIZARD_EMBED_MODEL"] = "jina"
            file0 = db.read_bytes()
            env = self.ts.run(["--db", str(db), "set", "qwen"])
            self.assertEqual(env["error"], "LOCK_ENV_PINNED", env)
            self.assertEqual((db.read_bytes(), lock_bytes(self.dir)), (file0, None))
            os.environ["WIZARD_EMBED_MODEL"] = "qwen"
            self.assertEqual(self.ts.set_tier(db, "qwen")["status"], "ok")
        finally:
            os.environ.pop("WIZARD_EMBED_MODEL", None) if keep is None else os.environ.__setitem__(
                "WIZARD_EMBED_MODEL", keep)

    # --- one verb at a time --------------------------------------------------------
    @unittest.skipUnless(hasattr(os, "fork"), "advisory flock is POSIX")
    def test_mutex_saboteur_second_verb_refuses_busy_and_control_proceeds_after(self):
        import fcntl
        db = make_brain(self.dir, "nomic")
        with open(self.dir / ".tier_set.flock", "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            self.assertEqual(self.ts.run(["--db", str(db), "set", "qwen"])["error"], "TIER_BUSY")
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        self.assertEqual(self.ts.set_tier(db, "qwen")["status"], "ok")

    # --- a process still holding the OLD model after the switch ---------------------
    def test_resident_old_model_saboteur_write_refused_after_switch_control_target_lands(self):
        import memory_write as mw
        db = make_brain(self.dir, "nomic")
        self.assertEqual(self.ts.set_tier(db, "qwen")["status"], "ok")
        cpu = self.el.catalogue_tiers()["cpu"]
        self.el.clear_loaded()
        self.el.register_loaded(model_id=cpu["model_id"], revision=cpu["revision"], weights_hash=cpu["weights_hash"],
                                dim=768, vec_table="memory_embeddings", tier="cpu")
        c = _connect(db)
        mid = c.execute("INSERT INTO memories (content) VALUES ('late writer')").lastrowid
        with self.assertRaises(mw.EmbeddingUnavailable):
            mw.attach_embedding(c, mid, "late writer", encoder=lambda t: _vec("x", t, 1024), policy="require",
                                organ="test")
        self.assertEqual(c.execute("SELECT COUNT(*) FROM memory_embeddings_v3 WHERE memory_id=?", (mid,)
                                   ).fetchone()[0], 0)
        fake_load(self.el.resolve(db))
        res = mw.attach_embedding(c, mid, "late writer", encoder=lambda t: _vec("x", t, 1024), policy="require",
                                  organ="test")
        self.assertTrue(res["embedded"], res)
        c.close()

    # --- a writer faster than the switch ------------------------------------------
    def test_writer_storm_saboteur_refuses_and_nothing_switches_control_quiet_cap_zero(self):
        db = make_brain(self.dir, "nomic")
        lock0, meta0 = lock_bytes(self.dir), snapshot(db, "memory_embeddings")[2]
        keep = self.ts.FLIP_GAP_CAP
        self.ts.FLIP_GAP_CAP = 0

        def storm(texts, lock):
            c = _connect(db)
            c.execute("INSERT INTO memories (content) VALUES ('another')")
            c.commit(); c.close()
            return fake_encode(texts, lock)
        try:
            self.ts._encode = storm
            env = self.ts.run(["--db", str(db), "set", "qwen"])
            self.assertEqual(env["error"], "TIER_SET_WRITER_STORM", env)
            self.assertEqual((lock_bytes(self.dir), snapshot(db, "memory_embeddings")[2]), (lock0, meta0))
            self.ts._encode = fake_encode
            env = self.ts.set_tier(db, "qwen")
            self.assertEqual(env["status"], "ok", env)
        finally:
            self.ts.FLIP_GAP_CAP = keep

    # --- the old verbs name the route ------------------------------------------
    def test_swap_control_names_tier_set_as_built(self):
        import embedding_upgrade as eu
        with self.assertRaises(eu.VerbRefused) as cm:
            eu.cmd_swap(type("A", (), {"db_path": None})())
        self.assertEqual(cm.exception.detail["built"], True)
        self.assertIn("voidloom tier set", cm.exception.message)

    def test_init_saboteur_write_lock_still_refuses_to_re_point(self):
        make_brain(self.dir, "nomic")
        with self.assertRaises(self.el.EmbedderLockError) as cm:
            self.el.write_lock(self.dir, "gpu")
        self.assertEqual(cm.exception.code, "WEIGHTS_MISMATCH")
        self.assertIn("tier set", str(cm.exception))
        self.assertNotIn("not built", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
