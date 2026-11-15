"""An encoder that cannot load is said at the top of every surface, typed, and a locked brain stores nothing without it.

THE CLAIM UNDER TEST
  * the one loader refuses EMBEDDER_UNAVAILABLE, typed, when the interpreter cannot import what runs the verified bytes
    (torch, sentence-transformers); a load that fails for any other reason the loader did not type reaches the engine's
    callers as the same code, never as an untyped error;
  * the engine's own recall and anticipate keep `status: ok` with `degraded: true` and a degraded code at the top, and
    answer by keyword; the engine's own store refuses, typed, and writes nothing;
  * on a brain whose install lock declares a tier, `voidloom store` and `batch-store` refuse with that code and write
    nothing, whichever stage the failure was born in; a brain with no lock stores keyword-only and says so at the top;
    a brain installed keyword-only keeps storing with a plain ok;
  * `voidloom doctor` reads the brain unhealthy with the reason named when its runtime is gone or it cannot be written,
    and `status` and `inspect` carry a fault they found at the top of the envelope, never only inside it; a brain
    installed keyword-only reads healthy and plain ok on all three.

The model's bytes are not on this disk in a test, so the loader's byte check is answered here (it is exercised by its
own tests); the import the loader makes after it is blocked for real.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
for p in (str(ROOT / "organs"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from voidloom.tests.test_doctor_brain_verdict import NOMIC, NOMIC_DIM, _Brain, el, mw  # noqa: E402
from voidloom import _facade, cli  # noqa: E402

import claude_memory  # noqa: E402
import embedding_upgrade as eu  # noqa: E402

_REAL_FIND_SPEC = __import__("importlib.util").util.find_spec


@contextlib.contextmanager
def runtime_gone(*names):
    """A process in which `names` cannot be imported and find_spec says so (what a provisioned environment that lost a
    package looks like to every check that asks without importing), with the byte check answered, which these fixtures
    cannot satisfy."""
    names = names or ("sentence_transformers",)
    blocked = {n: None for n in names}

    def find_spec(name, *a, **k):
        if name.split(".")[0] in names:
            return None
        return _REAL_FIND_SPEC(name, *a, **k)
    snap = Path(os.environ.get("TMPDIR", "/tmp"))
    with mock.patch.dict(sys.modules, blocked), \
            mock.patch("importlib.util.find_spec", side_effect=find_spec), \
            mock.patch.object(el, "verify_snapshot_against_lock", return_value=(snap, "e" * 64)), \
            mock.patch.dict(eu._model_cache, {}, clear=True), \
            mock.patch.dict(claude_memory._model_cache, {}, clear=True):
        yield


@contextlib.contextmanager
def engine_on(db):
    held = claude_memory.DB_PATH
    env = {k: os.environ.pop(k, None) for k in ("WIZARD_LIBRARY_DB", "CLAUDE_MEMORY_DB", "WIZARD_EMBED_MODEL")}
    claude_memory.DB_PATH = Path(db)
    claude_memory._active_model_cache.clear()
    try:
        yield
    finally:
        claude_memory.DB_PATH = held
        claude_memory._active_model_cache.clear()
        for k, v in env.items():
            if v is not None:
                os.environ[k] = v


def organ(db, *argv):
    """The engine's own door, `claude_memory.py <verb>`, in this process: (exit code, the one JSON line)."""
    out = io.StringIO()
    code = 0
    with engine_on(db), mock.patch.object(sys, "argv", ["claude_memory.py", *argv]), contextlib.redirect_stdout(out):
        try:
            claude_memory.main()
        except SystemExit as e:
            code = e.code or 0
    text = out.getvalue().strip()
    return code, json.loads(text[text.rindex("\n{") + 1:] if "\n{" in text else text)


class _Locked(_Brain):
    """A locked cpu brain holding two stamped memories, as `init` and two stores leave it."""

    def setUp(self):
        super().setUp()
        _facade._TIER0_ONLY_CACHE = None
        self.memory("tides and lanterns over the harbour", whash=self.h)
        self.memory("harbour bells at dusk", whash=self.h)
        self.conn.close()
        self.conn = self.connect()

    def tearDown(self):
        _facade._TIER0_ONLY_CACHE = None
        super().tearDown()

    def count(self):
        c = sqlite3.connect(str(self.db))
        try:
            return c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            c.close()

    def voidloom(self, verb, **kw):
        ns = argparse.Namespace(db=str(self.db), json=True, **kw)
        with engine_on(self.db):
            return getattr(cli, verb)(ns)


class TheLoaderTypesItsOwnFailure(_Locked):
    def test_saboteur_a_runtime_the_interpreter_cannot_import_is_refused_typed_by_the_loader(self):
        for names in (("sentence_transformers",), ("torch",)):
            with self.subTest(gone=names), runtime_gone(*names):
                with self.assertRaises(el.EmbedderLockError) as cm:
                    eu.get_locked_embedder(dict(self.lock))
                self.assertEqual(cm.exception.code, "EMBEDDER_UNAVAILABLE")
                self.assertIn(names[0], str(cm.exception))

    def test_saboteur_an_untyped_load_failure_reaches_the_engine_typed(self):
        with engine_on(self.db), mock.patch.object(eu, "get_locked_embedder", side_effect=OSError("disk said no")), \
                mock.patch.dict(claude_memory._model_cache, {}, clear=True):
            with self.assertRaises(el.EmbedderLockError) as cm:
                claude_memory._load_model()
        self.assertEqual(cm.exception.code, "EMBEDDER_UNAVAILABLE")
        self.assertIn("disk said no", str(cm.exception))

    def test_saboteur_the_refusal_is_one_of_the_lock_modules_named_refusals(self):
        self.assertIn("EMBEDDER_UNAVAILABLE", el.REFUSALS)


class TheEnginesOwnDoor(_Locked):
    def test_saboteur_recall_degrades_at_the_top_and_answers_by_keyword(self):
        with runtime_gone():
            rc, env = organ(self.db, "recall", "--query", "harbour bells", "--top_k", "3", "--no-rerank")
        self.assertEqual(rc, 0, env)
        self.assertEqual((env.get("status"), env.get("degraded"), env.get("code")), ("ok", True, "recall_degraded"), env)
        self.assertIn("EMBEDDER_UNAVAILABLE", json.dumps(env))

    def test_saboteur_anticipate_degrades_at_the_top(self):
        with runtime_gone():
            rc, env = organ(self.db, "anticipate", "--top-k", "5")
        self.assertEqual(rc, 0, env)
        self.assertEqual((env.get("status"), env.get("degraded"), env.get("code")),
                         ("ok", True, "anticipate_degraded"), env)
        self.assertEqual(env.get("vector_arm"), "EMBEDDER_UNAVAILABLE", env)

    def test_saboteur_anticipate_degrades_when_the_bytes_are_absent_too(self):
        absent = el.EmbedderLockError("SNAPSHOT_ABSENT", "planted: the pin is not on this disk")
        with mock.patch.object(claude_memory, "_encode", side_effect=absent):
            rc, env = organ(self.db, "anticipate", "--top-k", "5")
        self.assertEqual((rc, env.get("status"), env.get("code")), (0, "ok", "anticipate_degraded"), env)

    def test_saboteur_store_refuses_typed_and_writes_nothing(self):
        before = self.count()
        with runtime_gone():
            rc, env = organ(self.db, "store", "--content", "a note the encoder never saw", "--category", "insight")
        self.assertEqual((rc, env.get("status"), env.get("error")), (1, "error", "EMBEDDER_UNAVAILABLE"), env)
        self.assertEqual(self.count(), before)

    def test_control_an_integrity_refusal_is_never_turned_into_a_keyword_answer(self):
        wrong = el.EmbedderLockError("WEIGHTS_MISMATCH", "planted: the bytes disagree with the lock")
        with mock.patch.object(claude_memory, "_encode", side_effect=wrong):
            rc, env = organ(self.db, "recall", "--query", "harbour bells", "--top_k", "3", "--no-rerank")
        self.assertEqual((rc, env.get("error")), (1, "WEIGHTS_MISMATCH"), env)


class AStoreOnALockedBrain(_Locked):
    def test_saboteur_store_refuses_typed_and_writes_nothing(self):
        before = self.count()
        for names in (("sentence_transformers",), ("torch",)):
            with self.subTest(gone=names), runtime_gone(*names):
                env = self.voidloom("verb_store", content="a note on lanterns", category="insight", importance=0.5,
                                    tags="", answers=None)
            self.assertEqual((env["status"], env["code"]), ("error", "EMBEDDER_UNAVAILABLE"), env)
            self.assertEqual(self.count(), before, "a vectorless memory was written on a locked brain")

    def test_saboteur_a_failure_born_before_the_engine_reaches_the_facade_typed_too(self):
        """The same refusal whichever arm the failure arrives on: a printed load failure, or an ImportError raised
        outside the loader."""
        before = self.count()
        printed = json.dumps({"status": "error", "message": "Failed to load embedding model: No module named 'torch'"})

        def prints_and_exits(_ns):
            print(printed)
            raise SystemExit(1)

        def imports_and_fails(_ns):
            raise ModuleNotFoundError("No module named 'sentence_transformers'", name="sentence_transformers")

        for name, fn in (("printed", prints_and_exits), ("raised", imports_and_fails)):
            with self.subTest(arm=name), mock.patch.object(claude_memory, "cmd_store", fn), \
                    mock.patch.object(_facade, "_tier1_unavailable", return_value=True):
                env = self.voidloom("verb_store", content="a note on lanterns", category="insight", importance=0.5,
                                    tags="", answers=None)
            self.assertEqual((env["status"], env["code"]), ("error", "EMBEDDER_UNAVAILABLE"), env)
            self.assertEqual(self.count(), before)

    def test_saboteur_batch_store_refuses_every_item_and_writes_nothing(self):
        before = self.count()
        items = json.dumps([{"content": "a batch note on lanterns", "category": "insight"}])
        with runtime_gone():
            env = self.voidloom("verb_batch_store", items=items)
        self.assertEqual((env["status"], env["code"]), ("incomplete", "batch_store_incomplete"), env)
        self.assertEqual(env["data"]["failed"], 1, env)
        self.assertIn("EMBEDDER_UNAVAILABLE", json.dumps(env["data"]["results"]))
        self.assertEqual(self.count(), before)

    def test_saboteur_a_lost_lock_still_holds_the_brain_to_its_promise(self):
        (self.d / el.LOCK_NAME).unlink()
        self.assertTrue(_facade._brain_is_locked(str(self.db)))

    def test_control_a_brain_that_never_had_a_lock_made_no_promise(self):
        (self.d / el.LOCK_NAME).unlink()
        (self.d / el.LOCK_HISTORY_NAME).unlink()
        self.conn.execute("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
        self.conn.commit()
        self.assertFalse(_facade._brain_is_locked(str(self.db)))


class AStoreOnABrainWithNoLock(_Locked):
    """A brain from before the lock, holding vectors: its store is keyword-only when the encoder is gone, and says so."""

    def setUp(self):
        super().setUp()
        (self.d / el.LOCK_NAME).unlink()
        (self.d / el.LOCK_HISTORY_NAME).unlink()
        self.conn.execute("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
        self.declare(NOMIC, NOMIC_DIM)
        self.conn.commit()

    def test_saboteur_store_writes_by_keyword_and_says_so_at_the_top(self):
        before = self.count()
        with runtime_gone():
            env = self.voidloom("verb_store", content="a lockless note on lanterns", category="insight",
                                importance=0.5, tags="", answers=None)
        self.assertEqual((env["status"], env.get("degraded"), env["code"]), ("ok", True, "store_degraded"), env)
        self.assertEqual(self.count(), before + 1)

    def test_saboteur_batch_store_says_so_at_the_top(self):
        items = json.dumps([{"content": "a lockless batch note", "category": "insight"}])
        with runtime_gone():
            env = self.voidloom("verb_batch_store", items=items)
        self.assertEqual((env["status"], env.get("degraded"), env["code"]), ("ok", True, "batch_store_degraded"), env)


class _KeywordOnly(_Locked):
    """A brain installed keyword-only (Tier 0): no lock, no record of one, no vector table, `tier0_only` declared."""

    def setUp(self):
        super().setUp()
        (self.d / el.LOCK_NAME).unlink()
        (self.d / el.LOCK_HISTORY_NAME).unlink()
        self.conn.execute("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
        self.conn.execute(f"DELETE FROM {mw.PROVENANCE_TABLE}")
        self.conn.execute(f"DROP TABLE IF EXISTS {NOMIC}")
        self.conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('tier0_only', '1')")
        self.conn.commit()
        self._t0 = os.environ.pop("VOIDLOOM_TIER0_ONLY", None)
        os.environ["VOIDLOOM_TIER0_ONLY"] = "1"
        _facade._TIER0_ONLY_CACHE = None

    def tearDown(self):
        os.environ.pop("VOIDLOOM_TIER0_ONLY", None)
        if self._t0 is not None:
            os.environ["VOIDLOOM_TIER0_ONLY"] = self._t0
        super().tearDown()


class AKeywordOnlyBrainStaysSound(_KeywordOnly):
    def test_control_store_keeps_storing_with_a_plain_ok(self):
        before = self.count()
        with runtime_gone("torch", "sentence_transformers"):
            env = self.voidloom("verb_store", content="a keyword-only note", category="insight", importance=0.5,
                                tags="", answers=None)
        self.assertEqual((env["status"], env["code"], env.get("degraded")), ("ok", "store_ok", None), env)
        self.assertEqual(self.count(), before + 1)

    def test_control_doctor_reads_healthy_without_the_runtime(self):
        with runtime_gone("torch", "sentence_transformers"):
            d = self.doctor(models_on_disk=False)
        self.assertIs(d["brain_healthy"], True, d["reason"])

    def test_control_status_and_inspect_read_plain_ok(self):
        with runtime_gone("torch", "sentence_transformers"):
            st = self.voidloom("verb_status")
            ins = self.voidloom("verb_inspect", id=1)
        self.assertEqual((st["code"], st.get("degraded")), ("status_ok", None), st)
        self.assertEqual(st["data"]["faults"], [], st)
        self.assertEqual((ins["code"], ins.get("degraded")), ("inspect_ok", None), ins)


class DoctorAndTheStatusPages(_Locked):
    def test_saboteur_doctor_names_a_runtime_that_is_gone(self):
        for names in (("sentence_transformers",), ("torch",)):
            with self.subTest(gone=names), runtime_gone(*names):
                d = self.doctor()
            self.assertIs(d["brain_healthy"], False)
            self.assertTrue(str(d["reason"]).startswith("EMBEDDER_UNAVAILABLE"), d["reason"])
            self.assertIn(names[0], d["reason"])

    def test_control_doctor_reads_healthy_with_the_runtime(self):
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])

    def test_saboteur_doctor_never_reads_healthy_beside_a_brain_it_cannot_write(self):
        os.chmod(self.db, 0o444)
        try:
            d = self.doctor()
        finally:
            os.chmod(self.db, 0o644)
        self.assertIs(d["checks"]["local_fs_guard"]["writable"], False)
        self.assertIs(d["brain_healthy"], False)
        self.assertTrue(str(d["reason"]).startswith("DB_READ_ONLY"), d["reason"])

    def test_saboteur_status_carries_a_runtime_fault_at_the_top(self):
        with runtime_gone(), mock.patch.object(cli, "_absent_model", return_value=None):
            env = self.voidloom("verb_status")
        self.assertEqual((env["status"], env.get("degraded"), env["code"]), ("ok", True, "status_degraded"), env)
        self.assertTrue(any(f.startswith("EMBEDDER_UNAVAILABLE") for f in env["data"]["faults"]), env["data"]["faults"])

    def test_saboteur_status_carries_a_read_only_brain_at_the_top(self):
        os.chmod(self.db, 0o444)
        try:
            with mock.patch.object(cli, "_absent_model", return_value=None):
                env = self.voidloom("verb_status")
        finally:
            os.chmod(self.db, 0o644)
        self.assertEqual((env.get("degraded"), env["code"]), (True, "status_degraded"), env)
        self.assertTrue(any(f.startswith("DB_READ_ONLY") for f in env["data"]["faults"]), env["data"]["faults"])

    def test_saboteur_status_carries_absent_model_files_at_the_top(self):
        empty = self.d / "empty_cache"
        empty.mkdir()
        with mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(empty), "HF_HOME": str(empty)}):
            env = self.voidloom("verb_status")      # an empty cache: the lock's snapshot is absent for real
        self.assertTrue(any(f.startswith("SNAPSHOT_ABSENT") for f in env["data"]["faults"]), env["data"]["faults"])
        self.assertEqual((env.get("degraded"), env["code"]), (True, "status_degraded"), env)

    def test_control_status_on_a_sound_brain_is_plain_ok(self):
        with mock.patch.object(cli, "_absent_model", return_value=None):
            env = self.voidloom("verb_status")
        self.assertEqual((env["code"], env.get("degraded"), env["data"]["faults"]), ("status_ok", None, []), env)

    def test_saboteur_a_lost_lock_is_at_the_top_of_status_and_inspect(self):
        (self.d / el.LOCK_NAME).unlink()
        with mock.patch.object(cli, "_absent_model", return_value=None):
            st = self.voidloom("verb_status")
        ins = self.voidloom("verb_inspect", id=1)
        self.assertEqual((st.get("degraded"), st["code"]), (True, "status_degraded"), st)
        self.assertEqual((st["data"]["lock"]["present"], st["data"]["lock"]["was_locked"]), (False, True), st["data"])
        self.assertEqual((ins.get("degraded"), ins["code"]), (True, "inspect_degraded"), ins)
        self.assertEqual(ins["data"]["index_coverage"]["error"], "LOCK_MISSING")

    def test_saboteur_a_dropped_vector_table_is_at_the_top_of_status_and_inspect(self):
        self.conn.execute(f"DROP TABLE {NOMIC}")
        self.conn.commit()
        with mock.patch.object(cli, "_absent_model", return_value=None):
            st = self.voidloom("verb_status")
        ins = self.voidloom("verb_inspect", id=1)
        self.assertEqual((st.get("degraded"), st["code"]), (True, "status_degraded"), st)
        self.assertEqual((ins.get("degraded"), ins["code"]), (True, "inspect_degraded"), ins)

    def test_control_inspect_on_a_sound_brain_is_plain_ok(self):
        ins = self.voidloom("verb_inspect", id=1)
        self.assertEqual((ins["code"], ins.get("degraded")), ("inspect_ok", None), ins)


if __name__ == "__main__":
    unittest.main()
