#!/usr/bin/env python3
"""test_cross_tier.py — GPU-free regression suite for the cross-tier recall path.

These are the missing-regression tests devreview ``T-2026-0622-107`` flagged: the
cross-tier recall path (``cross_tier_bridges._query_single_tier``) had ZERO test
coverage, so the keystone 768-vs-1024 dim-mismatch fired on EVERY recall while the
green suites never saw it. Each test here asserts the FIXED contract (C1 + the G1
resolver fix) and is constructed to be **non-vacuous**: run against the un-fixed
(anchor) source it FAILS, run against the landed staged fix it PASSES.

Everything here is GPU-free / model-free:
  * ``cross_tier_bridges`` is pure-stdlib at import time (``sqlite_vec`` is imported
    lazily INSIDE functions, never at module load) — so importing it loads no torch.
  * the resolver tests build a SYNTHETIC in-memory sqlite DB (a ``metadata`` row +
    a vec0-shaped CREATE string) — no real brain, no embedding model, no network.
  * the grep-guards read the LANDED module source via ``ctb.__file__`` — pure text.

The module lives in organs/ (``…/.claude/memory/organs/cross_tier_bridges.py``),
imported as a TOP-LEVEL module (not ``voidloom.cross_tier_bridges``). We add the
brain dir to ``sys.path`` so the suite can import it whether run from the package
parent or the brain dir itself.
"""
from __future__ import annotations

import importlib
import os
import re
import sqlite3
import sys
import unittest
from pathlib import Path

# ``cross_tier_bridges`` is a sibling top-level module of the brain (it sits next
# to ``claude_memory.py`` at the memory-dir root), NOT inside the ``voidloom``
# package. Make the brain dir importable, in PRIORITY order: the env-resolved
# brain dir (so a sandbox/staging copy is preferred) wins over the canonical
# install path. We insert at sys.path[0] in REVERSE priority so the FIRST
# candidate ends up FRONTMOST (sys.path.insert(0, ...) pushes earlier inserts
# back). This is what lets the suite verify a staged copy via CLAUDE_MEMORY_DB
# before it lands at the canonical path.
_CANDIDATE_DIRS = []
_env_db = os.environ.get("WIZARD_LIBRARY_DB") or os.environ.get("CLAUDE_MEMORY_DB")
if _env_db:
    _CANDIDATE_DIRS.append(str(Path(_env_db).resolve().parent))
# D8-F7: this was the author's absolute brain path, so on any other host the
# candidate list ended in a directory that does not exist and the module went
# unfound -- silently, as a skip. The last resort is now the package's own
# location, which is correct EVERYWHERE by construction: these tests live inside
# the tree whose organs they are looking for.
_CANDIDATE_DIRS.append(str(Path(__file__).resolve().parents[2] / "organs"))
_CANDIDATE_DIRS.append(str(Path(__file__).resolve().parents[2]))

# Insert in REVERSE so _CANDIDATE_DIRS[0] (highest priority) lands at index 0.
for _d in reversed(_CANDIDATE_DIRS):
    if _d and Path(_d, "cross_tier_bridges.py").exists():
        # Move-to-front: drop any existing occurrence, then prepend.
        if _d in sys.path:
            sys.path.remove(_d)
        sys.path.insert(0, _d)

try:
    import cross_tier_bridges as ctb  # type: ignore  # noqa: E402
    _IMPORT_ERR = None
except Exception as e:  # pragma: no cover - environment-dependent
    ctb = None  # type: ignore
    _IMPORT_ERR = e


# D2-F4: the old skip reason read "cross_tier_bridges not importable: <err>",
# which on a clean install looks like a DEFECT. It is a DECISION: commit 6a152a6
# ("Cross-brain bridges become provisional: out of the package, armable at home")
# removed the module from the package and .gitignore:115 keeps it out. So these
# ten tests are expected to skip on every install that is not the author's, and
# the reason has to say which of the two it is -- a skip whose message reads like
# a failure is the mirror image of a skip that reads like a pass.
_CTB_SKIP = ("cross_tier_bridges is DELIBERATELY not packaged (6a152a6, "
             ".gitignore:115) — these tests exercise it only where it is armed "
             f"at home. Import result: {_IMPORT_ERR}")


@unittest.skipUnless(ctb is not None, _CTB_SKIP)
class TestCrossTierEmbedTableResolver(unittest.TestCase):
    """The recall path must resolve the ACTIVE embedding table via ``_embed_table``
    — never the hardcoded legacy nomic-768 ``memory_embeddings``."""

    def test_embed_table_reads_active_metadata(self):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE metadata (key TEXT, value TEXT)")
            conn.execute(
                "INSERT INTO metadata (key, value) "
                "VALUES ('active_embedding_table', 'memory_embeddings_v2')")
            conn.commit()
            self.assertEqual(ctb._embed_table(conn), "memory_embeddings_v2")
        finally:
            conn.close()

    def test_embed_table_falls_back_to_v2_not_legacy(self):
        # No metadata row -> the resolver must fall back to the jina-1024 table,
        # NOT the nomic-768 'memory_embeddings'. (A fallback to the legacy table
        # is exactly the dim-mismatch source.)
        conn = sqlite3.connect(":memory:")
        try:
            self.assertEqual(ctb._embed_table(conn), "memory_embeddings_v2")
            self.assertNotEqual(ctb._embed_table(conn), "memory_embeddings")
        finally:
            conn.close()

    def test_vec_table_dim_parses_declared_dimension(self):
        # _vec_table_dim parses float[N] from the CREATE text — a 1024-dim active
        # table and a 768-dim legacy table must read back distinct dims, which is
        # what the recall preflight uses to skip a mixed-era tier.
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE memory_embeddings_v2 "
                "(memory_id INTEGER, embedding float[1024])")
            conn.execute(
                "CREATE TABLE memory_embeddings "
                "(memory_id INTEGER, embedding float[768])")
            self.assertEqual(
                ctb._vec_table_dim(conn, "memory_embeddings_v2"), 1024)
            self.assertEqual(
                ctb._vec_table_dim(conn, "memory_embeddings"), 768)
        finally:
            conn.close()


@unittest.skipUnless(ctb is not None, _CTB_SKIP)
class TestQuerySingleTierIsResolverDriven(unittest.TestCase):
    """Grep/AST guards that make the keystone regression impossible to reintroduce.

    These read the LANDED source of the recall path (``_query_single_tier``) and
    assert it is resolver-driven. Against the pre-fix source (which hardcodes
    ``FROM memory_embeddings me``) these FAIL — proving they are non-vacuous."""

    def _query_src(self) -> str:
        import inspect
        return inspect.getsource(ctb._query_single_tier)

    def test_query_single_tier_calls_embed_table(self):
        src = self._query_src()
        self.assertIn(
            "_embed_table(", src,
            "the cross-tier recall path must resolve the active table via "
            "_embed_table(); a hardcoded table name is the 768/1024 dim bug")

    def test_query_single_tier_has_no_frozen_legacy_table(self):
        src = self._query_src()
        self.assertNotIn(
            "FROM memory_embeddings me", src,
            "recall path hardcodes the nomic-768 'memory_embeddings' table; it "
            "must MATCH against the resolver-driven active table instead")

    def test_query_single_tier_matches_against_resolved_table(self):
        # The MATCH must target the resolved table variable, not a frozen name.
        src = self._query_src()
        self.assertIn(
            "FROM {emb_tbl} me", src,
            "the vec MATCH must be issued against the resolved {emb_tbl}, "
            "mirroring discover_bridges")

    def test_query_single_tier_preflights_dim_compatibility(self):
        # A mixed-era pairing (remote dim != query dim) must DEGRADE silently
        # (skip the tier) rather than raise into the generic handler and spam a
        # WARNING on every recall. Assert the dim preflight + the dim resolver are
        # present in the recall path.
        src = self._query_src()
        self.assertIn("_vec_table_dim(", src,
                      "recall path must preflight remote/query dim compatibility")
        self.assertIn("query_dim", src)


@unittest.skipUnless(ctb is not None, _CTB_SKIP)
class TestNoPersonalPathLiteralShips(unittest.TestCase):
    """C1 grep-guard: no personal EDDIE path literal ships in the cross-tier source. A reconstructed
    ``/mnt/c/Users/{win_user}`` f-string is fine (it is derived from WIN_USER/USERNAME at runtime); the BANNED form is
    ANY frozen ``/mnt/c/Users/<name>`` literal. (2026-10-14: the guard used to name the one banned user folder, which
    shipped that name in the test itself; it now bans the whole class and names nobody.)"""

    def test_no_personal_eddie_path_literal_in_source(self):
        src = Path(ctb.__file__).read_text(encoding="utf-8")
        frozen = re.findall(r"/mnt/c/Users/(?!\{)[^/\s\"']+", src)
        self.assertEqual(frozen, [],
            "cross_tier_bridges must not ship a frozen /mnt/c/Users/<name> literal "
            "-- reconstruct it from WIN_USER/USERNAME instead")

    def test_default_tier_paths_carry_no_personal_literal(self):
        # The default tier-path table may name a Windows user folder only as the one derived at runtime.
        joined = "".join(str(v) for v in ctb._DEFAULT_TIER_PATHS.values())
        named = set(re.findall(r"/mnt/c/Users/([^/{]+)", joined))
        derived = {v for v in (os.environ.get("WIN_USER"), os.environ.get("USERNAME")) if v}
        self.assertLessEqual(named, derived, "_DEFAULT_TIER_PATHS must carry no frozen username")

    def test_env_key_uses_settable_underscores(self):
        # The derived env override key for the eddie-windows tier must be
        # shell-settable: MEMORY_TIER_EDDIE_WINDOWS_PATH (underscores), NOT the
        # un-settable hyphenated MEMORY_TIER_EDDIE-WINDOWS_PATH. We exercise the
        # resolver by setting the underscore key and asserting it wins.
        key = "MEMORY_TIER_EDDIE_WINDOWS_PATH"
        saved = os.environ.get(key)
        try:
            os.environ[key] = "/tmp/voidloom_twin_override_xtest.db"
            resolved = ctb._get_tier_db_path("eddie-windows")
            self.assertEqual(
                resolved, "/tmp/voidloom_twin_override_xtest.db",
                "the eddie-windows tier path must be overridable via the "
                "underscore env key MEMORY_TIER_EDDIE_WINDOWS_PATH")
        finally:
            if saved is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
