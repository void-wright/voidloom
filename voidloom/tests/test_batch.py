#!/usr/bin/env python3
"""test_batch.py — GPU-free tests for the single-model-load batch verbs.

Run::

    python3 -m voidloom.tests.test_batch        # from the package parent dir

These tests mirror the existing voidloom suite's discipline:
  * the JSON-array input parser (inline / @file / - stdin) is pure-stdlib and
    needs no model;
  * per-item isolation is proven WITHOUT loading torch — a mix of valid +
    invalid items, asserting the batch never aborts and the envelope reports
    failed>=1 AND succeeded>=1 (using a temp DB copy; the heavy embeds are
    skipped unless the model is cached);
  * batch-store writes ONLY to a temp DB copy and never touches the live brain
    (md5 anchor), and only when the embedding model is cached.

The suite NEVER touches the live DB for writes and NEVER imports torch on the
input-parsing / category-validation paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_PKG_PARENT = str(Path(__file__).resolve().parents[2])
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from voidloom import _batch, _bootstrap, _facade, cli  # noqa: E402

# CRUCIBLE-3 D2-F6 / D8-F7: this was hardcoded to the author's own 263 MB brain, so
# the SUITE'S DENOMINATOR WAS A FUNCTION OF THE HOST -- four Crucible seats ran this
# suite and got four different numbers (81/95, 101, 87, 90), because the tests below
# skip when that exact path is absent. It now resolves through the product's OWN
# env-first order -- WIZARD_LIBRARY_DB > CLAUDE_MEMORY_DB > the relative-self default
# -- the same order the engine, the CLI and the installer use, so another host points
# it at their own brain and the collected count becomes explicable rather than
# mysterious. The skips that remain are HOST-BOUND BY NATURE and say so in their
# reason, which is the honest form: the suite states its own scope.
def _declared_device() -> str:
    """This machine's configured compute index, via the single reader.

    Falls back to "0" if the organ cannot be imported -- the package-safe answer,
    and the one a single-GPU host needs. Never raises: a test helper that cannot
    resolve a device must still be able to spawn a process.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "organs"))
        from voidloom_device import declared_device  # type: ignore
        return declared_device()
    except Exception:
        return "0"


def _live_db() -> str:
    try:
        from voidloom import _paths as _p
        return str(_p.resolve_db())
    except Exception:
        import os as _os
        from pathlib import Path as _P
        return (_os.environ.get("WIZARD_LIBRARY_DB")
                or _os.environ.get("CLAUDE_MEMORY_DB")
                or str(_P(__file__).resolve().parents[2] / "claude_memory.db"))


LIVE_DB = _live_db()

def _live_db_usable() -> bool:
    """Is the resolved brain actually USABLE by the tests that need one?

    `LIVE_DB_USABLE` was the old guard and it is the wrong predicate: these
    tests need a POPULATED brain, not a file. Pointing WIZARD_LIBRARY_DB at a blank
    or schema-only database satisfied "exists" and then failed inside the test,
    which is a false red -- the suite reporting a product defect where the only
    fact is that the fixture was empty. Content is the condition; content is what
    is checked.
    """
    try:
        import sqlite3 as _s
        from pathlib import Path as _P
        if not _P(LIVE_DB).exists():
            return False
        c = _s.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        try:
            return c.execute("SELECT COUNT(*) FROM memories").fetchone()[0] > 0
        finally:
            c.close()
    except Exception:
        return False


LIVE_DB_USABLE = _live_db_usable()



# ---------------------------------------------------------------------------
# C0 (cycle 1, 2026-09-15) — THE PURITY CLAIM IS ONLY DECIDABLE IN A SUBPROCESS
# ---------------------------------------------------------------------------
# `sys.modules` is PROCESS-GLOBAL.  Asserting `assertNotIn("torch", sys.modules)`
# after calling a verb in-process tests the whole pytest session, not the verb:
# once ANY sibling test loads a model, the assertion is meaningless.  The seven
# sites in this suite showed both halves of that failure at once — six were
# wrapped in `if not torch_pre:` and so PASSED VACUOUSLY (a pass over an empty
# set), and the seventh had no guard and HARD-FAILED, which is the "pre-existing
# flake" three Crucible seats independently bisected to collection order.
#
# A fresh interpreter is the only place the claim is decidable, so the claim is
# made there.  The in-process functional assertions are kept — they test what the
# verb RETURNS; this tests what importing it COSTS.
NET_DB = r"\\fileserver\share\claude_memory.db"   # the guard refuses this before any model loads
# The probe REPORTS every heavy module; the assertions below are scoped to the
# MODEL STACK, because that is what these tests actually claim ("before the
# embedding model loads", "stdlib purity").  `numpy` and `sqlite_vec` ride in on
# the voidloom_approvals -> decision_units/trust_tier import chain and are a
# SEPARATE open finding (Crucible D4/T-2026-0622-222, import-time cost) — carried
# in the ledger, not silently absorbed into this one and not hidden by it.
HEAVY = ("torch", "sentence_transformers", "transformers", "sqlite_vec", "numpy")
MODEL_STACK = ("torch", "sentence_transformers", "transformers")


def model_stack_loaded_by(argv, env_extra=None, timeout=180):
    """The decidable form of "this verb does not load the embedding model"."""
    return [m for m in heavy_modules_loaded_by(argv, env_extra, timeout)
            if m in MODEL_STACK]


def heavy_modules_loaded_by(argv, env_extra=None, timeout=180):
    """Run one CLI verb in a FRESH interpreter; return the heavy modules it left
    in sys.modules.  Returns a list so an assertion failure NAMES the offender."""
    import subprocess, json as _json
    code = (
        "import sys, json, io, contextlib\n"
        "from voidloom.cli import main\n"
        "argv = json.loads(sys.argv[1])\n"
        "with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):\n"
        "    try:\n"
        "        main(argv)\n"
        "    except SystemExit:\n"
        "        pass\n"
        "print('HEAVY' + json.dumps(sorted({m.split('.')[0] for m in sys.modules} & set("
        + repr(list(HEAVY)) + "))))\n"
    )
    # The device is DECLARED by this machine, never a literal. It used to read
    # "1" -- correct on the author's two-GPU box (index 0 drives the display) and
    # wrong everywhere else: on the ~99% of hosts with one GPU, index 1 does not
    # exist and the spawned process sees NO CUDA device at all. voidloom_device
    # reads the env, then the env file, then the config, then a package-safe
    # default, which is the same chain gpu_guard and the manifest use.
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
           "CUDA_VISIBLE_DEVICES": _declared_device()}
    env.update(env_extra or {})
    p = subprocess.run([sys.executable, "-c", code, _json.dumps(list(argv))],
                       capture_output=True, text=True, env=env, timeout=timeout)
    out = p.stdout
    if "HEAVY" not in out:
        raise AssertionError(f"purity probe did not report (rc={p.returncode}): "
                             f"{(p.stderr or out)[-400:]}")
    return _json.loads(out.rsplit("HEAVY", 1)[1].strip().splitlines()[0])

def _ns(**kw) -> argparse.Namespace:
    base = dict(db=None, json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _models_cached() -> bool:
    return _bootstrap._detect_models().get("present", False)


# ---------------------------------------------------------------------------
# 1. JSON-array input parser — inline / @file / - (stdin).
# ---------------------------------------------------------------------------


class TestLoadJsonArray(unittest.TestCase):
    def test_inline_array(self):
        v = cli._load_json_array('["a", "b", "c"]')
        self.assertEqual(v, ["a", "b", "c"])

    def test_file_array(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "items.json"
            p.write_text('[{"query": "x", "top_k": 2}]', encoding="utf-8")
            v = cli._load_json_array(f"@{p}")
            self.assertEqual(v, [{"query": "x", "top_k": 2}])

    def test_stdin_array(self):
        old = sys.stdin
        try:
            sys.stdin = __import__("io").StringIO('["from", "stdin"]')
            v = cli._load_json_array("-")
        finally:
            sys.stdin = old
        self.assertEqual(v, ["from", "stdin"])

    def test_missing_file_raises_valueerror(self):
        with self.assertRaises(ValueError):
            cli._load_json_array("@/no/such/file/here.json")

    def test_bad_json_raises_valueerror(self):
        with self.assertRaises(ValueError):
            cli._load_json_array("{not json")


# ---------------------------------------------------------------------------
# 2. recall-item coercion — string or {query, top_k}.
# ---------------------------------------------------------------------------


class TestRecallItemCoercion(unittest.TestCase):
    def test_string_uses_default_top_k(self):
        self.assertEqual(_batch._coerce_recall_item("hi", 5), ("hi", 5))

    def test_dict_top_k_override(self):
        self.assertEqual(
            _batch._coerce_recall_item({"query": "q", "top_k": 9}, 5), ("q", 9))

    def test_empty_string_rejected(self):
        with self.assertRaises(ValueError):
            _batch._coerce_recall_item("   ", 5)

    def test_dict_without_query_rejected(self):
        with self.assertRaises(ValueError):
            _batch._coerce_recall_item({"top_k": 3}, 5)

    def test_bad_top_k_rejected(self):
        with self.assertRaises(ValueError):
            _batch._coerce_recall_item({"query": "q", "top_k": "lots"}, 5)


# ---------------------------------------------------------------------------
# 3. store-item validation — content+category required, fields checked.
# ---------------------------------------------------------------------------


class TestStoreItemValidation(unittest.TestCase):
    def test_minimal_item_ok(self):
        kw = _batch._validate_store_item(
            {"content": "c", "category": "insight"})
        self.assertEqual(kw["content"], "c")
        self.assertEqual(kw["category"], "insight")
        self.assertEqual(kw["kind"], "episodic")
        self.assertFalse(kw["auto_edges"])

    def test_full_item_threads_l5_fields(self):
        kw = _batch._validate_store_item({
            "content": "c", "category": "insight", "tags": ["a", "b"],
            "importance": 0.8, "kind": "semantic", "project": "p",
            "context": "ctx", "summary": "s", "source_user": "test-operator",
            "auto_edges": True})
        self.assertEqual(kw["tags"], "a,b")
        self.assertEqual(kw["importance"], 0.8)
        self.assertEqual(kw["kind"], "semantic")
        self.assertEqual(kw["source_user"], "test-operator")
        self.assertTrue(kw["auto_edges"])

    def test_missing_required_rejected(self):
        with self.assertRaises(ValueError):
            _batch._validate_store_item({"content": "c"})
        with self.assertRaises(ValueError):
            _batch._validate_store_item({"category": "insight"})

    def test_importance_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            _batch._validate_store_item(
                {"content": "c", "category": "insight", "importance": 5.0})

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            _batch._validate_store_item(
                {"content": "c", "category": "insight", "bogus": 1})


# ---------------------------------------------------------------------------
# 4. envelope shape + gating (no torch needed).
# ---------------------------------------------------------------------------


class TestBatchEnvelopeAndGating(unittest.TestCase):
    def test_batch_recall_refuses_network_db(self):
        torch_pre = "torch" in sys.modules
        env = cli.verb_batch_recall(_ns(items='["x"]', top_k=3,
                                        db="//nas/share/m.db", json=True))
        self.assertEqual(env["status"], "refused")
        self.assertEqual(env["code"], "non_local_db")
        self.assertEqual(env["verb"], "batch-recall")
        self.assertEqual(
            model_stack_loaded_by(["batch-recall", "--items", '["x"]', "--json"],
                                    {"WIZARD_LIBRARY_DB": NET_DB}),
            [], "a refused batch-recall must not import torch")

    def test_batch_recall_bad_input_errors(self):
        env = cli.verb_batch_recall(_ns(items="{not json", top_k=3, json=True))
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["code"], "invalid_batch_input")

    def test_batch_recall_non_array_errors(self):
        env = cli.verb_batch_recall(_ns(items='{"query": "x"}', top_k=3,
                                        json=True))
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["code"], "invalid_batch_input")

    def test_batch_store_bad_input_errors(self):
        env = cli.verb_batch_store(_ns(items="nope", json=True))
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["code"], "invalid_batch_input")

    def test_summarize_counts(self):
        results = [{"status": "ok"}, {"status": "error"}, {"status": "ok"}]
        s = _batch.summarize(results)
        self.assertEqual(s, {"count": 3, "succeeded": 2, "failed": 1})


# ---------------------------------------------------------------------------
# 4b. H6 COLLAPSED-SHAPE contract — memories reachable at
#     .data.results[].results[] (no inner `result.` wrapper). Proven GPU-FREE by
#     monkeypatching the engine-touching `recall_batch` with a synthetic
#     collapsed record list, so we exercise the CLI's pass-through WITHOUT loading
#     a model or opening the DB. This is the regression devreview #107 flagged:
#     the hook's DEFAULT verb shipped its real output shape untested.
# ---------------------------------------------------------------------------


class TestBatchRecallCollapsedShape(unittest.TestCase):
    def _fake_collapsed(self, queries, *, top_k=5, db_path=None):
        # Mimic the H6 collapsed contract that _batch.recall_batch now produces:
        # each record is {index, query, top_k, status:"ok", results:[<memory>...]}
        # — the inner `result.` engine-dict wrapper is GONE.
        out = []
        for i, q in enumerate(queries):
            out.append({
                "index": i, "query": q, "top_k": top_k, "status": "ok",
                "results": [
                    {"id": 100 + i, "content": f"hit for {q}",
                     "category": "insight", "final_score": 0.9 - 0.01 * i},
                ],
            })
        return out

    def test_memories_reachable_one_level_deep(self):
        # Patch recall_batch so NO model loads; assert the CLI folds the collapsed
        # list straight into data["results"] and the memories sit at
        # .data.results[].results[] (one level shallower than the old
        # .data.results[].result.results[]).
        import voidloom._batch as _b
        orig = _b.recall_batch
        torch_pre = "torch" in sys.modules
        try:
            _b.recall_batch = self._fake_collapsed
            env = cli.verb_batch_recall(_ns(
                items='["alpha query", "beta query"]', top_k=2,
                db=str(Path(LIVE_DB)) if LIVE_DB_USABLE else None,
                json=True))
        finally:
            _b.recall_batch = orig
        # A patched, model-free batch must still produce the ok envelope.
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["code"], "batch_recall_ok")
        data = env["data"]
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["succeeded"], 2)
        # THE CONTRACT: per-query record carries `results` (memory list) DIRECTLY;
        # there is NO inner `result` wrapper key.
        rec0 = data["results"][0]
        self.assertEqual(rec0["query"], "alpha query")
        self.assertIn("results", rec0,
                      "collapsed shape exposes memories at .results[] directly")
        self.assertNotIn("result", rec0,
                         "the legacy inner `result.` wrapper must be gone (H6)")
        # The memory itself lives at .data.results[].results[].
        mem = data["results"][0]["results"][0]
        self.assertEqual(mem["id"], 100)
        self.assertEqual(mem["content"], "hit for alpha query")
        # No model loaded on this path.
        # This path is driven by an in-test stub, so a subprocess cannot reach
        # it and `sys.modules` cannot decide it once a sibling has loaded a
        # model.  C0's rule: never pass vacuously — say so and skip.
        if torch_pre:
            self.skipTest("torch already resident from a sibling test; this "
                          "in-process purity claim is UNDECIDABLE here (C0)")
        self.assertNotIn("torch", sys.modules,
                         "the collapsed-shape pass-through must stay torch-free")

    def test_json_envelope_passes_collapsed_shape_through_verbatim(self):
        # The --json envelope must serialize the collapsed shape unchanged — a
        # consumer doing `jq '.data.results[].results[]'` gets the memories.
        import json as _json
        import voidloom._batch as _b
        orig = _b.recall_batch
        try:
            _b.recall_batch = self._fake_collapsed
            env = cli.verb_batch_recall(_ns(
                items='["only query"]', top_k=1,
                db=str(Path(LIVE_DB)) if LIVE_DB_USABLE else None,
                json=True))
        finally:
            _b.recall_batch = orig
        blob = _json.dumps(env)            # must round-trip cleanly
        reparsed = _json.loads(blob)
        mems = [m for rec in reparsed["data"]["results"]
                for m in rec["results"]]   # .data.results[].results[]
        self.assertEqual(len(mems), 1)
        self.assertEqual(mems[0]["id"], 100)


# ---------------------------------------------------------------------------
# 5. per-item isolation + single-model-load against a TEMP DB copy.
# ---------------------------------------------------------------------------


@unittest.skipUnless(LIVE_DB_USABLE, "host-bound: the resolved brain is absent or empty; set WIZARD_LIBRARY_DB to a populated brain to run")
class TestBatchAgainstTempDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # [107-L4]: R10 asserts "no bare tempfile call in the shipped set", scans
        # one of four test files, and missed this one. Bare mkdtemp lands on
        # $TMPDIR -- tmpfs here -- and several assertions in this tree read
        # st_dev and inode reuse, which differ there. ext4 under $HOME, named.
        _boxes = Path(__file__).resolve().parent.parent.parent / ".pytest_boxes"
        _boxes.mkdir(exist_ok=True)
        cls._tmp = tempfile.mkdtemp(prefix="vl_batch_test_", dir=str(_boxes))
        cls.tmp_db = os.path.join(cls._tmp, "vl_batch_test.db")
        shutil.copy2(LIVE_DB, cls.tmp_db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_store_batch_invalid_category_isolated_without_torch(self):
        # An invalid-category item must error in place WITHOUT loading torch,
        # because the category check runs before the engine call.
        torch_pre = "torch" in sys.modules
        results = _batch.store_batch(
            [{"content": "x", "category": "not_a_category"}],
            db_path=self.tmp_db,
            valid_categories=_facade.engine_categories())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "error")
        self.assertEqual(
            model_stack_loaded_by(["batch-store", "--items",
                                     '[{"content":"x","category":"NOT_A_CATEGORY"}]',
                                     "--json"]),
            [], "invalid-category item must short-circuit pre-torch")

    @unittest.skipUnless(_models_cached(),
                         "embedding model not cached — skipping heavy batch")
    def test_store_batch_per_item_isolation_and_single_load(self):
        """D8-F8: the untouched-brain guard is over a file THIS TEST OWNS."""
        from voidloom.tests._owned_brain import md5, owned_default_brain
        _owned_cm = owned_default_brain(self.tmp_db, self._tmp)
        owned = _owned_cm.__enter__()
        self.addCleanup(lambda: _owned_cm.__exit__(None, None, None))
        live_before = md5(owned)
        items = [
            {"content": "Voidloom batch test A — safe to delete.",
             "category": "insight", "tags": "voidloom-batch-test"},
            {"content": "x", "category": "not_a_category"},  # bad — isolated
            {"content": "Voidloom batch test B — safe to delete.",
             "category": "insight", "summary": "batch B",
             "kind": "semantic"},
        ]
        env = cli.verb_batch_store(_ns(items=json.dumps(items),
                                       db=self.tmp_db, json=True))
        # C5: the test's SUBJECT -- per-item isolation -- is unchanged and still
        # asserted below: one invalid item does not abort the batch, and the two
        # good items land. What changed is the VERDICT beside that count. This
        # asserted `ok`, which is how a batch that lost an item came to exit 0;
        # a caller writing `batch-store … && echo done` printed done. A batch
        # with failures is now `incomplete` (rc 4), and the all-good case is
        # still `ok` (asserted separately, so a fix that failed every batch
        # could not pass by satisfying this one).
        self.assertEqual(env["status"], "incomplete", env)
        self.assertEqual(env["code"], "batch_store_incomplete")
        data = env["data"]
        self.assertEqual(data["count"], 3)
        self.assertGreaterEqual(data["succeeded"], 2)
        self.assertGreaterEqual(data["failed"], 1)
        # The two good items landed in the --db target; the DEFAULT brain untouched.
        self.assertEqual(live_before, md5(owned),
                         "batch-store must write the --db target, never the DEFAULT brain")
        ok_ids = [r["memory_id"] for r in data["results"]
                  if r["status"] == "ok"]
        self.assertTrue(all(isinstance(i, int) for i in ok_ids))

    @unittest.skipUnless(_models_cached(),
                         "embedding model not cached — skipping heavy batch")
    def test_recall_batch_envelope_against_temp_db(self):
        env = cli.verb_batch_recall(_ns(
            items=json.dumps(["memory brain architecture",
                              {"query": "voidloom toolkit", "top_k": 2}]),
            top_k=3, db=self.tmp_db, json=True))
        self.assertIn(env["status"], ("ok", "error"))
        if env["status"] == "ok":
            self.assertEqual(env["code"], "batch_recall_ok")
            data = env["data"]
            self.assertEqual(data["count"], 2)
            self.assertIn("results", data)
            # H6 collapsed-shape contract walked at DEPTH against a real engine:
            # each per-query record exposes its memory list DIRECTLY at
            # `.results[]` (no inner `result.` wrapper). This is the level the
            # hook/user actually consume — the pre-fix double nesting
            # (.data.results[].result.results[]) is gone.
            for rec in data["results"]:
                self.assertIn("query", rec)
                self.assertNotIn("result", rec,
                                 "the legacy inner `result.` wrapper must be "
                                 "absent (H6 collapsed shape)")
                if rec.get("status") == "ok":
                    self.assertIn("results", rec,
                                  "memories must be reachable at "
                                  ".data.results[].results[]")
                    self.assertIsInstance(rec["results"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
