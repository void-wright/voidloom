#!/usr/bin/env python3
"""test_voidloom.py — GPU-free test suite for the voidloom CLI v0.1.

Run::

    python3 -m voidloom.tests.test_voidloom        # from the package parent dir
    # or
    PYTHONPATH=<pkg-parent> python3 <this file>

Design of the suite (all GPU-free):
  * bootstrap returns a tier WITHOUT importing torch (asserted by sys.modules).
  * the local-FS guard accepts a local path + refuses a fake network/UNC path.
  * agent-context emits valid JSON containing ALL v0.1 verbs.
  * recall/context facades return well-formed envelopes against a TEMP COPY of
    the live DB (never the live DB).
  * store validates the category enum (a torch-free refusal) AND — only when the
    embedding model is actually cached — writes to the temp DB copy.
  * approvals ref-regex accepts a valid ref, refuses a malformed one, and
    refuses an enact under --json / non-TTY.

The suite NEVER touches the live DB for writes (it copies it to /tmp first) and
NEVER imports torch on the bootstrap/guard/agent-context/regex paths.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# Make the package importable when run as a bare script.

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

def fixture_root() -> str:
    """Every filesystem fixture in this file lives on a filesystem that RECYCLES
    INODE NUMBERS (R10, iteration 2 / A4): tempfile's default is tmpfs on this
    host, where a freed inode number is never handed back, so the delete-and-
    recreate shape that defeated the identity gate on ext4 cannot occur there.
    $VOIDLOOM_SELFCHECK_FIXTURE_DIR when set (the harness points it into its
    scratch box), else ~/.cache/voidloom_tests under $HOME.  The class is closed
    by the harness's AST scan, which now includes this file."""
    root = (os.environ.get("VOIDLOOM_SELFCHECK_FIXTURE_DIR")
            or os.path.join(os.path.expanduser("~"), ".cache", "voidloom_tests"))
    os.makedirs(root, exist_ok=True)
    return root


_PKG_PARENT = str(Path(__file__).resolve().parents[2])
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from voidloom import _agent_context, _bootstrap, _facade, _guard, cli  # noqa: E402

# CRUCIBLE-3 D2-F6 / D8-F7: this was hardcoded to the author's own 263 MB brain, so
# the SUITE'S DENOMINATOR WAS A FUNCTION OF THE HOST -- four Crucible seats ran this
# suite and got four different numbers (81/95, 101, 87, 90), because the tests below
# skip when that exact path is absent. It now resolves through the product's OWN
# env-first order -- WIZARD_LIBRARY_DB > CLAUDE_MEMORY_DB > the relative-self default
# -- the same order the engine, the CLI and the installer use, so another host points
# it at their own brain and the collected count becomes explicable rather than
# mysterious. The skips that remain are HOST-BOUND BY NATURE and say so in their
# reason, which is the honest form: the suite states its own scope.
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


def _ns(**kw) -> argparse.Namespace:
    """Build a namespace with sensible defaults for the CLI verbs."""
    base = dict(db=None, json=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _models_cached() -> bool:
    """True if at least one embedding model is in the HF cache (so a real
    store/recall would not need a multi-GB download)."""
    return _bootstrap._detect_models().get("present", False)


# ---------------------------------------------------------------------------
# 1. bootstrap — returns a tier, no torch import.
# ---------------------------------------------------------------------------


class TestBootstrap(unittest.TestCase):
    def test_bootstrap_returns_tier_without_torch(self):
        # Snapshot whether torch was already loaded (shouldn't be, but be exact).
        torch_pre = "torch" in sys.modules
        st_pre = "sentence_transformers" in sys.modules

        env = cli.verb_bootstrap(_ns(json=True))

        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["code"], "bootstrap_ok")
        report = env["data"]
        self.assertIn(report["tier"], ("gpu", "cpu"))
        self.assertIn("os", report)
        self.assertIn("gpu", report)
        self.assertIn("brain", report)
        # THE CARDINAL ASSERTION, decided where it is decidable (C0): the
        # in-process form passed vacuously behind `if not torch_pre` the moment
        # any sibling test loaded a model.
        self.assertEqual(model_stack_loaded_by(["bootstrap", "--json"]), [],
                         "bootstrap must import no heavy module — stdlib purity")

    def test_bootstrap_wsl2_and_gpu_shape(self):
        report = _bootstrap.bootstrap()
        self.assertIsInstance(report["os"]["is_wsl2"], bool)
        self.assertIsInstance(report["gpu"]["available"], bool)
        self.assertIsInstance(report["gpu"]["gpus"], list)
        # Whichever tier, notes must be a non-empty list of strings.
        self.assertTrue(report["notes"])
        self.assertTrue(all(isinstance(n, str) for n in report["notes"]))

    def test_bootstrap_never_calls_lspci(self):
        # Behavioural white-box: lspci may be *named* in the docstring/comments
        # (we document that we avoid it), but it must NEVER appear in executable
        # code. We strip comments + string/docstring literals via the tokenizer
        # and assert no live NAME/identifier or command token is 'lspci'.
        import io as _io
        import tokenize
        src = Path(_bootstrap.__file__).read_text(encoding="utf-8")
        offending = []
        for tok in tokenize.generate_tokens(_io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue  # comments + string literals (incl. docstrings) are prose
            if "lspci" in tok.string:
                offending.append(tok.string)
        self.assertEqual(offending, [],
                         "bootstrap must never invoke lspci in code "
                         "(it fails in WSL2); found live tokens: "
                         f"{offending}")
        # And, concretely: the only external command bootstrap shells out to is
        # nvidia-smi (resolved via shutil.which), never lspci.
        self.assertIn("nvidia-smi", src)


# ---------------------------------------------------------------------------
# 2. local-FS guard — accept local, refuse network/UNC/cloud.
# ---------------------------------------------------------------------------


class TestGuard(unittest.TestCase):
    def test_local_path_accepted(self):
        v = _guard.check(LIVE_DB)
        self.assertTrue(v.ok, f"local DB should pass the guard: {v.message}")
        self.assertEqual(v.code, "local_ok")

    def test_unc_path_refused(self):
        v = _guard.check(r"\\fileserver\share\claude_memory.db")
        self.assertFalse(v.ok)
        self.assertEqual(v.code, "non_local_db")

    def test_double_slash_authority_refused(self):
        v = _guard.check("//nas-host/brains/claude_memory.db")
        self.assertFalse(v.ok)
        self.assertEqual(v.code, "non_local_db")

    def test_cloud_sync_path_refused(self):
        for p in ("/home/someone/Dropbox/brain/claude_memory.db",
                  "/home/someone/OneDrive/claude_memory.db",
                  "/Users/someone/Library/Mobile Documents/com~apple~CloudDocs/m.db"):
            v = _guard.check(p)
            self.assertFalse(v.ok, f"cloud path should refuse: {p}")
            self.assertEqual(v.code, "non_local_db")

    def test_require_local_returns_none_for_local(self):
        self.assertIsNone(_guard.require_local(LIVE_DB))

    def test_require_local_returns_refusal_for_network(self):
        ref = _guard.require_local("//nas/share/m.db")
        self.assertIsNotNone(ref)
        self.assertEqual(ref["code"], "non_local_db")

    def test_require_write_readonly_degrade(self):
        # Create a real file, strip its write bit, and assert writes degrade.
        with tempfile.TemporaryDirectory(dir=fixture_root()) as td:
            ro = Path(td) / "ro.db"
            ro.write_bytes(b"")
            os.chmod(ro, 0o444)
            try:
                ref = _guard.require_write(str(ro))
                # On most systems os.access reports not-writable -> readonly_db.
                if ref is not None:
                    self.assertEqual(ref["code"], "readonly_db")
            finally:
                os.chmod(ro, 0o644)  # let TemporaryDirectory clean up

    def test_verb_recall_refuses_network_db(self):
        # The recall verb must refuse a network DB BEFORE any heavy import.
        torch_pre = "torch" in sys.modules
        env = cli.verb_recall(_ns(query="anything", top_k=3,
                                  db="//nas/share/m.db", json=True))
        self.assertEqual(env["status"], "refused")
        self.assertEqual(env["code"], "non_local_db")
        self.assertEqual(
            model_stack_loaded_by(["recall", "--query", "x", "--json"],
                                    {"WIZARD_LIBRARY_DB": NET_DB}),
            [], "a refused recall must not have imported torch")


# ---------------------------------------------------------------------------
# 3. agent-context — valid JSON with ALL verbs.
# ---------------------------------------------------------------------------


class TestAgentContext(unittest.TestCase):
    EXPECTED_VERBS = {
        "bootstrap", "agent-context", "capabilities", "doctor",
        "recall", "store", "context", "batch-recall", "batch-store",
        "approvals",
        # 2026-09-14, the installable CLI (ACE cli_install, 9.53 accept-with-
        # recorded-defects, landed). Admitted DELIBERATELY: this set is a gate,
        # and a verb that appears in _agent_context without appearing here is
        # a verb nobody consciously admitted.
        "hooks", "instructions", "preamble", "canary",
        # 2026-09-27, model-choice Round 1 (M9): the overnight thinker's record and switch. Admitted deliberately.
        "thinker",
        # 2026-10-15, handoff round 1 (D1, lanes L4 + L2): `voidloom preflight` proves a machine before a package is
        # cut for it. Admitted deliberately.
        "preflight",
        # 2026-09-28, the dream fix (Tranche A, A5/A7): the owner's dream verbs (backfill / confirm / show /
        # watch). Admitted deliberately.
        "dream",
        # 2026-10-12, the Persona Forge: status / forge / declare / decline. Admitted deliberately.
        "persona",
    }

    def test_manifest_valid_json_all_verbs(self):
        env = cli.verb_agent_context(_ns(json=True))
        self.assertEqual(env["status"], "ok")
        # Round-trips through JSON cleanly.
        blob = json.dumps(env, ensure_ascii=False)
        reparsed = json.loads(blob)
        verbs = {v["name"] for v in reparsed["data"]["verbs"]}
        self.assertEqual(verbs, self.EXPECTED_VERBS,
                         "agent-context must describe exactly the v0.1 verbs")

    def test_every_verb_has_purpose_args_when(self):
        m = _agent_context.manifest()
        for v in m["verbs"]:
            self.assertTrue(v.get("purpose"), f"{v['name']} missing purpose")
            self.assertIsInstance(v.get("args"), dict, f"{v['name']} args")
            self.assertTrue(v.get("when"), f"{v['name']} missing when")
            # `when` trigger must be terse (<=10 words).
            self.assertLessEqual(len(v["when"].split()), 10,
                                 f"{v['name']} 'when' should be <=10 words")

    def test_manifest_matches_dispatchable_verbs(self):
        # The manifest verbs must equal the argparse subcommands (no drift).
        parser = cli.build_parser()
        sub_actions = [a for a in parser._actions
                       if isinstance(a, argparse._SubParsersAction)]
        self.assertEqual(len(sub_actions), 1)
        dispatchable = set(sub_actions[0].choices.keys())
        manifest_verbs = set(_agent_context.verb_names())
        self.assertEqual(manifest_verbs, dispatchable,
                         "manifest verbs must match dispatchable subcommands")

    def test_manifest_render_text_nonempty(self):
        txt = _agent_context.render_text(_agent_context.manifest())
        self.assertIn("voidloom", txt)
        for verb in self.EXPECTED_VERBS:
            self.assertIn(verb, txt)


# ---------------------------------------------------------------------------
# 4. recall / context facades — envelopes against a TEMP DB copy.
# ---------------------------------------------------------------------------


@unittest.skipUnless(LIVE_DB_USABLE, "host-bound: the resolved brain is absent or empty; set WIZARD_LIBRARY_DB to a populated brain to run")
class TestFacadeReads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="vl_cli_test_", dir=fixture_root())
        cls.tmp_db = os.path.join(cls._tmp, "vl_cli_test.db")
        shutil.copy2(LIVE_DB, cls.tmp_db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_context_envelope_against_temp_db(self):
        # context is torch-free (pure SQL) — always runs.
        env = cli.verb_context(_ns(project="engineering-workstation",
                                   limit=5, db=self.tmp_db, json=True))
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["code"], "context_ok")
        data = env["data"]
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["action"], "context")
        self.assertIn("results", data)
        self.assertLessEqual(len(data["results"]), 5)

    def test_context_writes_only_to_temp_copy(self):
        """context bumps access_count; prove it lands ONLY in the --db target.

        D8-F8: this hashed the operator's real brain, which the product's own
        crons and any concurrent session write to -- so a red said "a cron
        fired", not "the verb misbehaved". The guarded file is now one this test
        owns and nothing else can reach, AND it is the brain the resolver would
        pick by default, so an ignored `--db` would move the hash.
        """
        from voidloom.tests._owned_brain import md5, owned_default_brain
        with owned_default_brain(self.tmp_db, self._tmp) as owned:
            before = md5(owned)
            cli.verb_context(_ns(project=None, limit=3, db=self.tmp_db, json=True))
            self.assertEqual(before, md5(owned),
                             "a context call against an explicit --db must not "
                             "touch the DEFAULT brain")

    @unittest.skipUnless(_models_cached(),
                         "embedding model not cached — skipping heavy recall")
    def test_recall_envelope_against_temp_db(self):
        env = cli.verb_recall(_ns(query="memory brain architecture",
                                  top_k=3, db=self.tmp_db, json=True))
        # recall may be ok, a clean engine_error if a vec extension is absent, or
        # a refusal — three states, because the product has three. D8-F5: a
        # two-state vocabulary here is the same defect as in the approvals
        # envelope test above, one verb along.
        self.assertIn(env["status"], ("ok", "error", "refused"))
        self.assertIn("code", env)
        if env["status"] == "ok":
            self.assertEqual(env["code"], "recall_ok")
            self.assertEqual(env["data"]["status"], "ok")


# ---------------------------------------------------------------------------
# 5. store — category enum validation + (conditional) temp-DB write.
# ---------------------------------------------------------------------------


class TestStoreValidation(unittest.TestCase):
    def test_invalid_category_refused_without_torch(self):
        torch_pre = "torch" in sys.modules
        env = cli.verb_store(_ns(content="hello", category="not_a_category",
                                 tags="", importance=0.5, json=True))
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["code"], "invalid_category")
        self.assertIn("valid", env["data"]["detail"])
        # A bad category must be rejected BEFORE the embedding model loads.
        self.assertEqual(
            model_stack_loaded_by(["store", "--content", "x",
                                     "--category", "NOT_A_CATEGORY", "--json"]),
            [], "invalid category must short-circuit before torch")

    def test_valid_category_list_matches_engine(self):
        cats = _facade.engine_categories()
        self.assertIn("technical_decision", cats)
        self.assertIn("insight", cats)
        self.assertEqual(len(cats), 12)

    def test_importance_out_of_range_refused(self):
        env = cli.verb_store(_ns(content="x", category="insight",
                                 tags="", importance=5.0, json=True))
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["code"], "invalid_importance")


@unittest.skipUnless(LIVE_DB_USABLE, "host-bound: the resolved brain is absent or empty; set WIZARD_LIBRARY_DB to a populated brain to run")
class TestStoreWrite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="vl_cli_store_", dir=fixture_root())
        cls.tmp_db = os.path.join(cls._tmp, "vl_cli_test.db")
        shutil.copy2(LIVE_DB, cls.tmp_db)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @unittest.skipUnless(_models_cached(),
                         "embedding model not cached — skipping heavy store")
    def test_store_writes_to_temp_db(self):
        """D8-F8: guarded over a brain THIS TEST OWNS, not the operator's live one."""
        from voidloom.tests._owned_brain import md5, owned_default_brain
        _owned_cm = owned_default_brain(self.tmp_db, self._tmp)
        owned = _owned_cm.__enter__()
        self.addCleanup(lambda: _owned_cm.__exit__(None, None, None))
        live_before = md5(owned)

        env = cli.verb_store(_ns(
            content="Voidloom D3 CLI test write — safe to delete.",
            category="insight", tags="voidloom-d3-test,ace",
            importance=0.4, db=self.tmp_db, json=True))

        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["code"], "store_ok")
        self.assertEqual(env["data"]["status"], "ok")
        self.assertIsInstance(env["data"]["memory_id"], int)

        # The write landed in the --db target, NOT the resolved default.
        self.assertEqual(live_before, md5(owned),
                         "store must write the --db target, never the DEFAULT brain")

        # And the row is actually in the temp copy.
        import sqlite3
        conn = sqlite3.connect(self.tmp_db)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE id=?",
                (env["data"]["memory_id"],)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 1)

    @unittest.skipUnless(_models_cached(),
                         "embedding model not cached — skipping heavy store")
    def test_store_importance_echo_matches_persisted(self):
        # H4 regression (devreview #107 missing-test #5): the store envelope's
        # `data.importance` must report the value PERSISTED to memories.importance
        # — i.e. the surprise-adjusted (input + beta*novelty) value the engine
        # actually wrote — NOT the bare input. Pre-fix, the envelope echoed the
        # INPUT importance while the row carried a transformed value, a silent
        # data-integrity drift. We read the row back and assert echo == DB value.
        env = cli.verb_store(_ns(
            content="Voidloom importance-fidelity test — safe to delete.",
            category="insight", tags="voidloom-importance-test",
            importance=0.7, db=self.tmp_db, json=True))
        self.assertEqual(env["status"], "ok", env)
        self.assertEqual(env["code"], "store_ok")
        echoed = env["data"].get("importance")
        self.assertIsInstance(echoed, (int, float),
                              "store envelope must carry a numeric importance")

        import sqlite3
        conn = sqlite3.connect(self.tmp_db)
        try:
            stored = conn.execute(
                "SELECT importance FROM memories WHERE id=?",
                (env["data"]["memory_id"],)).fetchone()[0]
        finally:
            conn.close()

        # The CONTRACT: the envelope reflects the PERSISTED value, so the echo
        # equals the round-tripped DB field (within float tolerance). This holds
        # whether or not the surprise feed nudged it above the 0.7 input.
        self.assertAlmostEqual(
            float(echoed), float(stored), places=4,
            msg=f"store echo {echoed} != persisted {stored} "
                f"(H4: envelope must report the stored importance)")
        # And the persisted value must be a sane probability — never below the
        # input (the surprise feed only adds) and never above 1.0.
        self.assertGreaterEqual(float(stored), 0.7 - 1e-6)
        self.assertLessEqual(float(stored), 1.0 + 1e-6)


# ---------------------------------------------------------------------------
# 6. approvals — ref-regex + confirm-gate refusals.
# ---------------------------------------------------------------------------


class TestApprovalsRefRegex(unittest.TestCase):
    def test_valid_self_eval_ref_accepted(self):
        self.assertIsNone(cli._validate_approval_ref("self-eval:7"))

    def test_valid_belief_ref_accepted(self):
        # Belief batch ids are slugs with timestamps (colons allowed in raw).
        self.assertIsNone(
            cli._validate_approval_ref("belief:belief-sweep-2026-06-10T18:04:00"))

    def test_malformed_ref_refused(self):
        for bad in ("garbage", "self-eval:", "belief:", "drop table",
                    "self-eval:7; rm -rf", "unknown:7", "self-eval:7 8",
                    "self-eval:../../etc"):
            ref = cli._validate_approval_ref(bad)
            self.assertIsNotNone(ref, f"{bad!r} should be refused")
            self.assertEqual(ref["status"], "refused")
            self.assertEqual(ref["code"], "invalid_approval_ref")

    def test_confirm_gate_refuses_under_json(self):
        gate = cli._confirm_gate("approve", "self-eval:7", as_json=True)
        self.assertIsNotNone(gate)
        self.assertEqual(gate["status"], "refused")
        self.assertEqual(gate["code"], "confirm_required_no_tty")

    def test_confirm_gate_refuses_non_tty(self):
        # In the test runner, stdin is not a TTY -> the gate must refuse.
        gate = cli._confirm_gate("reject", "belief:b1", as_json=False)
        self.assertIsNotNone(gate)
        self.assertEqual(gate["code"], "confirm_required_no_tty")

    def test_verb_approvals_enact_refused_under_json(self):
        # An --approve under --json must be refused at the confirm gate, AFTER
        # passing the ref-regex (valid ref) — never enacting.
        if not LIVE_DB_USABLE:
            self.skipTest("host-bound: the resolved brain is absent or empty; set WIZARD_LIBRARY_DB to a populated brain to run")
        env = cli.verb_approvals(_ns(approve="self-eval:999999",
                                     title=["nonexistent"], ids=None,
                                     show=None, reject=None, reason=None,
                                     list=False, db=None, json=True))
        self.assertEqual(env["status"], "refused")
        self.assertEqual(env["code"], "confirm_required_no_tty")

    def test_verb_approvals_malformed_ref_refused_before_backend(self):
        env = cli.verb_approvals(_ns(approve="DROP TABLE memories",
                                     title=None, ids=None, show=None,
                                     reject=None, reason=None, list=False,
                                     db=None, json=True))
        self.assertEqual(env["status"], "refused")
        self.assertEqual(env["code"], "invalid_approval_ref")

    @unittest.skipUnless(LIVE_DB_USABLE, "host-bound: the resolved brain is absent or empty; set WIZARD_LIBRARY_DB to a populated brain to run")
    def test_verb_approvals_list_envelope(self):
        env = cli.verb_approvals(_ns(approve=None, title=None, ids=None,
                                     show=None, reject=None, reason=None,
                                     list=True, db=None, json=True))
        # D8-F5: this read ("ok", "error") and the product returns **refused**
        # on a brain without `governance_proposals`, carrying the message the
        # crucible's own index called exemplary: "its pending count is UNKNOWN,
        # not zero, and any 0 published for it is a placeholder." The suite
        # encoded a two-state vocabulary and called the product's three-valued
        # honesty a failure -- so the refusal path, the best thing in this
        # codebase, was the thing that turned the suite red for a NEW USER on
        # first contact, while passing on the author's host where the table
        # happens to exist. An assertion written over a state space the product
        # outgrew.
        #
        # `refused` is not merely tolerated below: it is CHECKED, because a
        # refusal that does not say what it could not observe is the defect this
        # product refuses to commit.
        self.assertIn(env["status"], ("ok", "error", "refused"))
        if env["status"] == "refused":
            self.assertIn("code", env)
            _blob = json.dumps(env)
            self.assertTrue(
                any(w in _blob.lower() for w in ("unknown", "not opened", "absent")),
                "a refusal must name what it could NOT observe, not just decline: " + _blob[:400])
        if env["status"] == "ok":
            self.assertEqual(env["code"], "approvals_list")
            # H2-RED FIX: the unified approval surface renamed the queue key
            # `items` -> `records` (and added `breakdown`/`ping`). The assertion
            # below tracks the LANDED contract (cli.py:887-892 emits
            # {unified_count, breakdown, ping, records}). Asserting `items` here
            # shipped the canonical suite RED — proof the suite was not a ship
            # gate. We assert the CURRENT shape AND that the retired key is gone,
            # so a silent re-rename can never re-pass on the stale name.
            self.assertIn("records", env["data"])
            self.assertNotIn("items", env["data"],
                             "approvals list must use 'records', not the "
                             "retired 'items' key")
            self.assertIn("unified_count", env["data"])
            self.assertIn("breakdown", env["data"])


# ---------------------------------------------------------------------------
# 7. envelope contract + dispatch end-to-end.
# ---------------------------------------------------------------------------


class TestEnvelopeAndDispatch(unittest.TestCase):
    def test_envelope_status_enum_enforced(self):
        with self.assertRaises(AssertionError):
            cli._envelope("bogus", "x", {})

    def test_exit_codes(self):
        self.assertEqual(cli._exit_code_for(cli.ok("c", {})), 0)
        self.assertEqual(cli._exit_code_for(cli.error("c", "m")), 1)
        self.assertEqual(cli._exit_code_for(cli.refused("c", "m")), 3)

    def test_dispatch_agent_context_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.dispatch(["agent-context", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["verb"], "agent-context")

    def test_dispatch_bootstrap_human(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.dispatch(["bootstrap"])
        self.assertEqual(code, 0)
        self.assertIn("voidloom bootstrap", buf.getvalue())

    def test_dispatch_no_command_prints_help(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.dispatch([])
        self.assertEqual(code, 1)

    def test_dispatch_recall_network_db_refused_exit3(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.dispatch(["--db", "//nas/share/m.db",
                                 "recall", "-q", "x", "--json"])
        self.assertEqual(code, 3)  # refused -> exit 3
        out = json.loads(buf.getvalue())
        self.assertEqual(out["status"], "refused")
        self.assertEqual(out["code"], "non_local_db")

    def test_doctor_is_fail_graceful(self):
        env = cli.verb_doctor(_ns(json=True))
        self.assertEqual(env["status"], "ok")
        # C5: `healthy` was split into brain_healthy (the machine is sound)
        # and promise_healthy (the promise is kept). There is deliberately no
        # single flag meaning "the database opened".
        self.assertIn("brain_healthy", env["data"])
        self.assertIn("promise_healthy", env["data"])
        self.assertIn("local_fs_guard", env["data"]["checks"])

    def test_doctor_honors_env_db_non_default_basename(self):
        # M1 regression (devreview #107 missing-test #4): `doctor` must resolve
        # the DB via the SAME env-first resolver (`_paths.resolve_db`) the rest of
        # the CLI uses, honoring CLAUDE_MEMORY_DB even when the basename is NOT the
        # default `claude_memory.db`. The pre-fix bug re-appended a hardcoded
        # basename in detect_brain, so doctor reported db_present:False + the WRONG
        # path on a correctly-configured env-overridden brain (while `context`
        # honored it). GPU-free: doctor never loads a model.
        if not LIVE_DB_USABLE:
            self.skipTest("no live DB")
        td = tempfile.mkdtemp(prefix="vl_doctor_env_", dir=fixture_root())
        alt = os.path.join(td, "mybrain.db")  # deliberately NON-default basename
        old = os.environ.get("CLAUDE_MEMORY_DB")
        old_wl = os.environ.get("WIZARD_LIBRARY_DB")
        try:
            shutil.copy2(LIVE_DB, alt)
            # WIZARD_LIBRARY_DB outranks CLAUDE_MEMORY_DB in the resolver order;
            # clear it so this test exercises the CLAUDE_MEMORY_DB leg specifically.
            os.environ.pop("WIZARD_LIBRARY_DB", None)
            os.environ["CLAUDE_MEMORY_DB"] = alt
            env = cli.verb_doctor(_ns(json=True))  # NO --db; env must win.
            self.assertEqual(env["status"], "ok")
            checks = env["data"]["checks"]
            self.assertTrue(
                checks["db_present"],
                "doctor must resolve CLAUDE_MEMORY_DB, not reappend "
                "claude_memory.db (db_present should be True)")
            self.assertEqual(
                checks["db_path"], str(Path(alt).resolve()),
                "doctor must report the env-resolved path, not a hardcoded "
                "claude_memory.db basename")
            # A model must NOT have loaded for a path-only health check.
            # Decided in a fresh interpreter: see heavy_modules_loaded_by().
            self.assertEqual(
                model_stack_loaded_by(["doctor", "--json"],
                                        {"CLAUDE_MEMORY_DB": alt}),
                [], "doctor must stay torch-free")
        finally:
            os.environ.pop("CLAUDE_MEMORY_DB", None)
            if old is not None:
                os.environ["CLAUDE_MEMORY_DB"] = old
            if old_wl is not None:
                os.environ["WIZARD_LIBRARY_DB"] = old_wl
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
