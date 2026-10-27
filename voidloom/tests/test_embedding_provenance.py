# -*- coding: utf-8 -*-
"""Per-row vector provenance against memory_write.

THE CLAIM UNDER TEST is not "a stamp exists". It is that the stamp cannot lie in
the one way a stamp lies: by asserting a producer for a row nobody embedded. Two
shipped tiers are both 1024 floats wide, so a mixed population is shape-identical
to a clean one; provenance is the only thing that can tell them apart, and
provenance written by a DEFAULT tells you nothing because every row says the same
thing.

EVERY CHECK SHIPS AS A CONTROL PAIR (ruler M1-v1 §4d, COUNCIL_C0 ③):
    *_control_*   the correct state, and the check must read GREEN;
    *_saboteur_*  the defect planted, and the check must read RED.
A check whose control cannot pass is discarded, not tuned. The names carry the
pairing so a verbose run IS the ledger.

The checks, keyed to the round's P-list:
    C1  no DEFAULT on any stamp column                          (P1)
    C2  model_id / model_ref derived from the bijection + pin   (P7)
    C3  vector and stamp are one unit                           (P8)
    C4  invariant A: stamp without vector — THE SABOTEUR THAT MATTERS
    C5  invariant B: vector without stamp (the two-column path)
    C6  a stamp under a foreign model is not coverage
    C7  the reader REFUSES, typed, and never falls back         (P5)
    C8  coverage is an exact fraction, on the SUCCESS path      (P6)
    C9  an edited memory is visible by content hash             (A2)
    C10 the migration is idempotent and TRACKED                 (P3/P4)
    C11 no executescript on the provenance path                 (P2)
    C12 every engine vector-write site stamps

No embedding model is loaded. The encoder is a deterministic 1024-float stub: the
property under test is about the STAMP, and an absent model is a fact about the
host — NOT MEASURED is not MEASURED AND FINE.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "organs"))

MIGRATION = ROOT / "migrations" / "migration_033_embedding_provenance.sql"
DIM = 1024
TABLE = "memory_embeddings_v2"          # the jina tier: 1024 wide, pinned in code
TIER = "jina"


def _stub_encoder(text: str) -> bytes:
    """Deterministic, model-free, correct width. Content-derived so two texts differ."""
    h = hashlib.sha256(text.encode()).digest()
    floats = [((h[i % 32] / 255.0) - 0.5) for i in range(DIM)]
    return struct.pack(f"{DIM}f", *floats)


def _connect(path: Path, *, vec: bool = True) -> sqlite3.Connection:
    c = sqlite3.connect(str(path))
    if vec:
        import sqlite_vec
        c.enable_load_extension(True); sqlite_vec.load(c); c.enable_load_extension(False)
    return c


def _make_brain(path: Path, *, with_provenance: bool = True) -> sqlite3.Connection:
    """A minimal jina-tier brain. executescript here is fine: it runs OUTSIDE any
    savepoint and its commit is the point."""
    c = _connect(path)
    c.executescript(f"""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO metadata VALUES ('active_embedding_table', '{TABLE}');
        INSERT INTO metadata VALUES ('active_embedding_model', 'jinaai/jina-embeddings-v5-text-small');
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
                               is_archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
        CREATE VIRTUAL TABLE {TABLE} USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{DIM}]);
    """)
    if with_provenance:
        import memory_write as mw
        mw.ensure_provenance_table(c)
        c.commit()
    return c


def _add(conn: sqlite3.Connection, text: str) -> int:
    return conn.execute("INSERT INTO memories (content) VALUES (?)", (text,)).lastrowid


def _embed(conn: sqlite3.Connection, mid: int, text: str, organ: str = "test") -> dict:
    import memory_write as mw
    return mw.attach_embedding(conn, mid, text, encoder=_stub_encoder,
                               policy="require", organ=organ)


def _plant_default_style_stamp(conn: sqlite3.Connection, model_id: str) -> None:
    """What a DEFAULT does, spelled out as the backfill it amounts to: stamp EVERY
    memory with the current model, vector or no vector. Values are well-formed so
    the CHECK constraints do not pre-empt the invariant — the point is that a
    plausible-looking stamp is still a lie."""
    conn.execute(f"""
        INSERT OR REPLACE INTO embedding_provenance
            (memory_id, vec_table, model_id, model_ref, dim, content_hash, embedded_at, organ)
        SELECT id, '{TABLE}', ?, 'deadbeef', {DIM}, ?, '2026-01-01T00:00:00+00:00', 'mutant'
        FROM memories
    """, (model_id, hashlib.sha256(b"").hexdigest()))
    conn.commit()


def _no_default_in(ddl: str) -> bool:
    """The C1 checker, as a function so the saboteur can be fed to the SAME code."""
    code = "\n".join(l.split("--", 1)[0] for l in ddl.splitlines())
    return re.search(r"\bDEFAULT\b", code, re.I) is None


def _dflt_values(conn: sqlite3.Connection, table: str) -> dict:
    return {r[1]: r[4] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


class _BrainCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "brain.db"
        self.conn = _make_brain(self.db)
        import memory_write as mw
        import claude_memory as cm
        self.mw, self.cm = mw, cm

    def tearDown(self):
        self.conn.close(); self.tmp.cleanup()


# ---------------------------------------------------------------- C1: no DEFAULT
class C1_SchemaHasNoDefault(_BrainCase):

    def test_control_shipped_migration_has_no_default_on_any_column(self):
        ddl = MIGRATION.read_text(encoding="utf-8")
        self.assertTrue(_no_default_in(ddl), "the shipped DDL carries a DEFAULT")
        self.assertIn("CREATE TABLE IF NOT EXISTS embedding_provenance", ddl)
        # and on a BUILT brain, from sqlite's own catalogue: every dflt_value NULL
        dflt = _dflt_values(self.conn, "embedding_provenance")
        self.assertEqual(set(dflt), set(self.mw.PROVENANCE_COLUMNS))
        self.assertTrue(all(v is None for v in dflt.values()), dflt)

    def test_saboteur_a_default_on_a_stamp_column_is_caught_by_both_forms(self):
        ddl = MIGRATION.read_text(encoding="utf-8").replace(
            "model_id     TEXT    NOT NULL CHECK (model_id <> ''),",
            "model_id     TEXT    NOT NULL DEFAULT 'jinaai/jina-embeddings-v5-text-small',")
        self.assertNotEqual(ddl, MIGRATION.read_text(encoding="utf-8"), "saboteur did not plant")
        self.assertFalse(_no_default_in(ddl), "the text checker did not see the DEFAULT")
        sab = _connect(Path(self.tmp.name) / "sab.db")
        sab.executescript(ddl)
        dflt = _dflt_values(sab, "embedding_provenance")
        self.assertIsNotNone(dflt["model_id"], "PRAGMA table_info did not see the DEFAULT")
        sab.close()


# ------------------------------------------- C2: derived from the bijection + pin
class C2_StampIsDerivedFromTheBijection(_BrainCase):

    def test_control_stamp_carries_bijection_model_pinned_ref_dim_hash_organ(self):
        text = "the loom remembers what the wright forgot"
        mid = _add(self.conn, text)
        out = _embed(self.conn, mid, text, organ="test.c2")
        self.conn.commit()
        self.assertTrue(out["embedded"])
        row = self.conn.execute(
            "SELECT model_id, model_ref, dim, content_hash, embedded_at, organ "
            "FROM embedding_provenance WHERE memory_id=? AND vec_table=?", (mid, TABLE)).fetchone()
        self.assertIsNotNone(row, "no stamp row beside the vector")
        model_id, model_ref, dim, chash, at, organ = row
        self.assertEqual(model_id, self.cm.EMBED_TIERS[TIER][2])
        import embedding_upgrade
        self.assertEqual(model_ref, embedding_upgrade.JINA_REVISION,
                         "model_ref must be the code's own pin for this tier")
        self.assertEqual(out["provenance"]["ref_source"], "pinned")
        self.assertEqual(dim, DIM)
        self.assertEqual(chash, hashlib.sha256(text.encode()).hexdigest())
        self.assertRegex(at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
        self.assertEqual(organ, "test.c2")

    def test_saboteur_a_literal_model_id_would_not_follow_the_bijection(self):
        # Re-point the bijection's model NAME for this tier. Code that derives the
        # stamp from EMBED_TIERS follows; code that typed the name would not.
        real = self.cm.EMBED_TIERS[TIER]
        self.cm.EMBED_TIERS[TIER] = (real[0], real[1], "fictitious/model-for-c2")
        try:
            mid = _add(self.conn, "x"); _embed(self.conn, mid, "x"); self.conn.commit()
            got = self.conn.execute("SELECT model_id FROM embedding_provenance WHERE memory_id=?",
                                    (mid,)).fetchone()[0]
        finally:
            self.cm.EMBED_TIERS[TIER] = real
        self.assertEqual(got, "fictitious/model-for-c2",
                         "the stamp did not follow the bijection: model_id is a literal")
        self.assertNotEqual(got, real[2])

    def test_saboteur_a_table_the_bijection_does_not_name_is_refused(self):
        with self.assertRaises(self.mw.EmbeddingUnavailable):
            self.mw.model_identity("memory_embeddings_v9")


# ------------------------------------------------------- C3: one unit, atomic
class C3_VectorAndStampAreAtomic(_BrainCase):

    def _counts(self, mid):
        v = self.conn.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE memory_id=?", (mid,)).fetchone()[0]
        s = self.conn.execute("SELECT COUNT(*) FROM embedding_provenance WHERE memory_id=?", (mid,)).fetchone()[0]
        return v, s

    def test_control_one_write_yields_one_vector_and_one_stamp(self):
        mid = _add(self.conn, "x"); _embed(self.conn, mid, "x"); self.conn.commit()
        self.assertEqual(self._counts(mid), (1, 1))

    def test_saboteur_a_stamp_failure_after_the_vector_leaves_neither(self):
        mid = _add(self.conn, "x")
        real = self.mw._stamp_provenance
        self.mw._stamp_provenance = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("planted stamp failure"))
        try:
            with self.assertRaises(self.mw.EmbeddingUnavailable):
                _embed(self.conn, mid, "x")
        finally:
            self.mw._stamp_provenance = real
        self.conn.commit()
        self.assertEqual(self._counts(mid), (0, 0),
                         "a vector persisted without its stamp — the pair is not atomic")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM memory_tags WHERE memory_id=? AND tag='embedding-missing'",
            (mid,)).fetchone()[0], 0, "policy=require must raise, not tag")


# --------------------------- C4: invariant A — THE SABOTEUR THAT MATTERS
class C4_InvariantA_StampWithoutVector(_BrainCase):
    N, K = 12, 5

    def _populate(self):
        ids = [_add(self.conn, f"memory {i}") for i in range(self.N)]
        for mid in ids[:self.K]:
            _embed(self.conn, mid, f"memory {mid}")
        self.conn.commit()
        return ids

    def test_control_after_sanctioned_writes_the_invariant_is_clean(self):
        self._populate()
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual(cov.stamped_without_vector, 0)
        self.assertTrue(cov.clean)

    def test_saboteur_default_style_stamp_goes_red_on_EXACTLY_the_vectorless_rows(self):
        ids = self._populate()
        _plant_default_style_stamp(self.conn, self.cm.EMBED_TIERS[TIER][2])
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual(cov.stamped_without_vector, self.N - self.K,
                         f"red on {cov.stamped_without_vector} rows, expected exactly {self.N - self.K}")
        self.assertFalse(cov.clean)
        # the SET, not only the count: the red rows ARE the vectorless rows
        red = {r[0] for r in self.conn.execute(f"""
            SELECT p.memory_id FROM embedding_provenance p
            WHERE p.vec_table='{TABLE}'
              AND NOT EXISTS (SELECT 1 FROM {TABLE} v WHERE v.memory_id = p.memory_id)""")}
        self.assertEqual(red, set(ids[self.K:]))
        # and coverage did NOT become N/N — a DEFAULT cannot inflate it
        self.assertEqual(cov.coverage_of_active, Fraction(self.K, self.N))


# ------------------------------------------- C5: invariant B — the two-column path
class C5_InvariantB_VectorWithoutStamp(_BrainCase):

    def test_control_sanctioned_writes_leave_no_unstamped_vector(self):
        for t in ("a", "b", "c"):
            _embed(self.conn, _add(self.conn, t), t)
        self.conn.commit()
        self.assertEqual(self.mw.provenance_coverage(self.conn).vectors_without_stamp, 0)

    def test_saboteur_a_direct_insert_into_the_vector_table_goes_red(self):
        _embed(self.conn, _add(self.conn, "a"), "a")          # so the reader has a match
        mid = _add(self.conn, "legacy")
        self.conn.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)",
                          (mid, _stub_encoder("legacy")))     # the pre-M1-C2 write
        self.conn.commit()
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual(cov.vectors_without_stamp, 1)
        self.assertFalse(cov.clean)


# ------------------------------------------------- C6: a foreign stamp is not coverage
class C6_ForeignModelStampIsNotCoverage(_BrainCase):

    def test_control_no_foreign_stamps_after_sanctioned_writes(self):
        _embed(self.conn, _add(self.conn, "a"), "a"); self.conn.commit()
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual((cov.stamped_foreign, cov.covered), (0, 1))

    def test_saboteur_a_row_stamped_under_another_model_is_excluded_and_red(self):
        _embed(self.conn, _add(self.conn, "a"), "a")
        mid = _add(self.conn, "mixed")
        self.conn.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)",
                          (mid, _stub_encoder("mixed")))
        self.conn.execute("""INSERT INTO embedding_provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                          (mid, TABLE, "some/other-model", "cafe", DIM,
                           hashlib.sha256(b"mixed").hexdigest(), "2026-01-01T00:00:00+00:00", "t"))
        self.conn.commit()
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual(cov.stamped_foreign, 1)
        self.assertEqual(cov.covered, 1, "the foreign row must not count as covered")
        self.assertFalse(cov.clean)


# --------------------------------------------------- C7: typed refusal, no fallback
class C7_ReaderRefusesTyped(_BrainCase):

    def test_control_a_stamped_brain_returns_coverage_without_raising(self):
        _embed(self.conn, _add(self.conn, "a"), "a"); self.conn.commit()
        cov = self.mw.provenance_coverage(self.conn)
        self.assertIsInstance(cov, self.mw.ProvenanceCoverage)
        self.assertEqual((cov.vec_table, cov.model_id), (TABLE, self.cm.EMBED_TIERS[TIER][2]))

    def test_saboteur_A_vectors_stamped_under_another_model_only(self):
        mid = _add(self.conn, "a")
        self.conn.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)",
                          (mid, _stub_encoder("a")))
        self.conn.execute("INSERT INTO embedding_provenance VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (mid, TABLE, "some/other-model", "cafe", DIM,
                           hashlib.sha256(b"a").hexdigest(), "2026-01-01T00:00:00+00:00", "t"))
        self.conn.commit()
        with self.assertRaises(self.mw.ProvenanceUnmatched) as cm:
            self.mw.provenance_coverage(self.conn)
        cov = cm.exception.coverage                       # the refusal STILL reports coverage
        self.assertEqual((cov.covered, cov.vectors, cov.stamped_foreign), (0, 1, 1))
        self.assertIn("REFUSED", str(cm.exception))

    def test_saboteur_B_a_fictitious_tier_model_produces_a_refusal_not_a_result(self):
        _embed(self.conn, _add(self.conn, "a"), "a"); self.conn.commit()
        real = self.cm.EMBED_TIERS[TIER]
        self.cm.EMBED_TIERS[TIER] = (real[0], real[1], "fictitious/nobody-embedded-this")
        try:
            with self.assertRaises(self.mw.ProvenanceUnmatched) as cm:
                self.mw.provenance_coverage(self.conn)
        finally:
            self.cm.EMBED_TIERS[TIER] = real
        self.assertEqual(cm.exception.coverage.covered, 0)
        self.assertEqual(cm.exception.coverage.stamped_foreign, 1)

    def test_saboteur_C_an_unknown_table_in_metadata_is_refused_by_the_resolver(self):
        import embed_resolver
        self.conn.execute("UPDATE metadata SET value='memory_embeddings_v9' WHERE key='active_embedding_table'")
        self.conn.commit()
        with self.assertRaises(embed_resolver.EmbedTierUnresolved):
            self.mw.provenance_coverage(self.conn)

    def test_saboteur_D_unmeasurable_is_not_zero(self):
        # (i) the sidecar absent: UNREADABLE, never coverage 0/N
        bare = _make_brain(Path(self.tmp.name) / "bare.db", with_provenance=False)
        with self.assertRaises(self.mw.ProvenanceUnreadable):
            self.mw.provenance_coverage(bare)
        bare.close()
        # (ii) vec0 not loaded: 'no such module: vec0' reads like absence and is not
        _embed(self.conn, _add(self.conn, "a"), "a"); self.conn.commit(); self.conn.close()
        novec = _connect(self.db, vec=False)
        with self.assertRaises(self.mw.ProvenanceUnreadable) as cm:
            self.mw.provenance_coverage(novec)
        self.assertIn("vec0", str(cm.exception))
        novec.close()
        self.conn = _connect(self.db)          # so tearDown has something to close

    def test_there_is_no_fallback_flag_on_the_reader(self):
        import inspect
        sig = inspect.signature(self.mw.provenance_coverage)
        self.assertEqual(list(sig.parameters), ["conn"],
                         "the reader must not grow a flag that flips the refusal off")


# ------------------------------------------ C8: exact coverage on the SUCCESS path
class C8_CoverageIsExactOnEveryCall(_BrainCase):

    def test_control_half_populated_reports_exactly_one_half(self):
        ids = [_add(self.conn, f"m{i}") for i in range(10)]
        for mid in ids[:5]:
            _embed(self.conn, mid, f"m{mid}")
        self.conn.commit()
        cov = self.mw.provenance_coverage(self.conn)          # no exception: the success path
        self.assertEqual(cov.coverage_of_active, Fraction(1, 2))
        self.assertEqual(cov.as_dict()["coverage_of_active"], {"num": 5, "den": 10, "value": 0.5})
        self.assertEqual(cov.coverage_of_vectors, Fraction(1, 1))
        self.assertEqual(cov.denominator, "memories WHERE is_archived = 0")

    def test_saboteur_default_style_stamp_cannot_make_coverage_read_complete(self):
        ids = [_add(self.conn, f"m{i}") for i in range(10)]
        for mid in ids[:5]:
            _embed(self.conn, mid, f"m{mid}")
        self.conn.commit()
        _plant_default_style_stamp(self.conn, self.cm.EMBED_TIERS[TIER][2])
        naive = Fraction(self.conn.execute("SELECT COUNT(*) FROM embedding_provenance").fetchone()[0], 10)
        self.assertEqual(naive, Fraction(1, 1), "the naive stamps/active figure would read COMPLETE")
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual(cov.coverage_of_active, Fraction(1, 2), "coverage must count vectors, not stamps")
        self.assertFalse(cov.clean)

    def test_control_the_denominator_tracks_active_memories(self):
        ids = [_add(self.conn, f"m{i}") for i in range(4)]
        for mid in ids[:2]:
            _embed(self.conn, mid, f"m{mid}")
        self.conn.execute("UPDATE memories SET is_archived=1 WHERE id=?", (ids[3],))
        self.conn.commit()
        self.assertEqual(self.mw.provenance_coverage(self.conn).coverage_of_active, Fraction(2, 3))


# ------------------------------------------------------- C9: edits are visible
class C9_EditedMemoryIsVisibleByHash(_BrainCase):

    def test_control_unedited_rows_are_not_stale(self):
        _embed(self.conn, _add(self.conn, "original"), "original"); self.conn.commit()
        self.assertEqual(self.mw.provenance_coverage(self.conn).stale_content_hash, 0)

    def test_saboteur_an_edit_after_embedding_goes_red(self):
        mid = _add(self.conn, "original"); _embed(self.conn, mid, "original")
        self.conn.execute("UPDATE memories SET content='edited' WHERE id=?", (mid,)); self.conn.commit()
        self.assertEqual(self.mw.provenance_coverage(self.conn).stale_content_hash, 1)


# ------------------------------------------ C10: idempotent, and TRACKED
class C10_MigrationIsIdempotentAndTracked(unittest.TestCase):

    def _ls_files(self, rel: str) -> str:
        r = subprocess.run(["git", "ls-files", "--", rel], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            self.skipTest("git unavailable; tracking cannot be derived (UNKNOWN, not clean)")
        return r.stdout.strip()

    def test_control_the_migration_is_tracked_and_in_the_package_population(self):
        rel = MIGRATION.relative_to(ROOT).as_posix()
        self.assertEqual(self._ls_files(rel), rel,
                         f"{rel} is not tracked: the package is built from git ls-files, "
                         "so it would be absent from every install")

    def test_saboteur_an_untracked_path_is_seen_as_absent(self):
        self.assertEqual(self._ls_files("migrations/migration_999_never_added.sql"), "")

    def test_control_apply_twice_is_clean_and_the_table_is_in_the_reference_set(self):
        import schema_migrator as sm
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "b.db")
            r1 = sm.apply(db); r2 = sm.apply(db)
            for r in (r1, r2):
                bad = [f for f in r.get("files_with_errors", []) if "033" in str(f)]
                self.assertEqual(bad, [], r)
            c = sqlite3.connect(db)
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
            c.close()
            self.assertIn("embedding_provenance", names)
            self.assertIn("idx_embedding_provenance_model", names)
            self.assertIn("embedding_provenance", set(r2.get("reference", []) or names))

    def test_saboteur_a_non_idempotent_ddl_fails_on_the_second_pass(self):
        ddl = MIGRATION.read_text(encoding="utf-8").replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE")
        c = sqlite3.connect(":memory:")
        import memory_write as mw
        stmts = []
        buf = ""
        for line in ddl.splitlines(keepends=True):
            buf += line
            if sqlite3.complete_statement(buf):
                stmts.append(buf); buf = ""
        for s in stmts: c.execute(s)
        with self.assertRaises(sqlite3.OperationalError):
            for s in stmts: c.execute(s)


# ------------------------------------------------ C11: no executescript here
class C11_NoImplicitCommitOnTheProvenancePath(_BrainCase):

    def _run_inside_open_txn(self):
        mid = _add(self.conn, "in-flight")            # legacy mode: implicit BEGIN
        self.assertTrue(self.conn.in_transaction)
        try:
            _embed(self.conn, mid, "in-flight")
        except Exception:
            pass                                       # the saboteur raises; the control does not
        self.conn.rollback()                           # the CALLER decides. Nothing may have committed.
        m = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        v = self.conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
        return m, v

    def test_control_the_callers_rollback_removes_memory_vector_and_stamp(self):
        self.assertEqual(self._run_inside_open_txn(), (0, 0))

    def test_saboteur_executescript_commits_the_first_half_behind_the_callers_back(self):
        real = self.mw.ensure_provenance_table
        self.mw.ensure_provenance_table = lambda c: c.executescript(MIGRATION.read_text(encoding="utf-8"))
        try:
            m, v = self._run_inside_open_txn()
        finally:
            self.mw.ensure_provenance_table = real
        self.assertEqual((m, v), (1, 1),
                         "with executescript the memory and vector SURVIVE the caller's rollback: "
                         "the implicit COMMIT landed them without their stamp")

    def test_control_the_shipped_path_names_no_executescript(self):
        src = (ROOT / "organs" / "memory_write.py").read_text(encoding="utf-8")
        body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertNotRegex(body, r"^\s*[^#\n]*\.executescript\(", "memory_write calls executescript")


# ------------------------------------------ C12: every engine vector write stamps
class C12_EngineVectorWritesStamp(_BrainCase):
    VEC_INSERT = re.compile(r"INSERT INTO \{(?:_active_embed_table\(\)|table)\} \(memory_id, embedding\)")

    def _unstamped_sites(self, src: str):
        lines = src.splitlines()
        bad = []
        for i, l in enumerate(lines):
            if self.VEC_INSERT.search(l):
                window = "\n".join(lines[i:i + 5])
                if "_stamp_vector_provenance(" not in window:
                    bad.append(i + 1)
        return bad

    def test_control_each_engine_site_is_followed_by_the_stamp(self):
        src = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
        sites = [i + 1 for i, l in enumerate(src.splitlines()) if self.VEC_INSERT.search(l)]
        self.assertEqual(len(sites), 3, f"expected the three engine sites, found {sites}")
        self.assertEqual(self._unstamped_sites(src), [])

    def test_saboteur_a_site_with_the_stamp_removed_is_flagged(self):
        src = (ROOT / "organs" / "claude_memory.py").read_text(encoding="utf-8")
        sab = src.replace('_stamp_vector_provenance(conn, args.id, args.content, "claude_memory.update")', "pass")
        self.assertNotEqual(sab, src, "saboteur did not plant")
        self.assertEqual(len(self._unstamped_sites(sab)), 1)

    def test_control_stamp_after_vector_insert_stamps_the_callers_open_transaction(self):
        mid = _add(self.conn, "engine")
        self.conn.execute(f"INSERT INTO {TABLE} (memory_id, embedding) VALUES (?, ?)",
                          (mid, _stub_encoder("engine")))
        self.mw.stamp_after_vector_insert(self.conn, mid, "engine", organ="claude_memory.store")
        self.conn.commit()
        cov = self.mw.provenance_coverage(self.conn)
        self.assertEqual((cov.covered, cov.vectors_without_stamp), (1, 0))
        self.assertEqual(self.conn.execute("SELECT organ FROM embedding_provenance WHERE memory_id=?",
                                           (mid,)).fetchone()[0], "claude_memory.store")


if __name__ == "__main__":
    unittest.main(verbosity=2)
