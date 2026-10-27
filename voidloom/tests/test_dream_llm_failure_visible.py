"""R8 (reference-host round): a failed LLM call in the dream is VISIBLE, and COUNTED.

Measured on the reference host, 2026-09-23 22:00: the nightly dream logged
`claude -p exited 1:` with an EMPTY message seven times (and once on 09-24) while the
run reported phases_error 0. Two defects, both repaired here:
  * the warning printed `result.stderr[:200]` and `claude -p` had said nothing on
    stderr -- it now carries stderr when stderr says something, else stdout's TAIL
    (least_privilege.failed_run_detail), else says plainly that both were empty;
  * a phase whose LLM calls failed ran on its heuristic path (a pair left
    undreamed, a cluster left unsummarised) and read as a clean success -- the
    sequencer now counts it: per phase `llm_fallbacks`, per run `phases_fallback`
    and `llm_calls_failed`, and the phase's dream_phase_log.detail says so.
The dream's second mouth, `dream_pipeline._claude_p` (RAPTOR / playbooks), returned
None on a non-zero exit WITHOUT ANY LOG LINE; it is visible and counted too.

Executed with a FAKE `claude` first on PATH that exits 1 writing ONLY to stdout -- the
shape the reference host's blank warnings imply -- inside a scratch HOME (the legacy token
sidecar lives there) and a scratch brain whose THINKER_RECORD names that fake, recorded
through `thinker set claude --probe-env direct` while the fake still answered. Nothing here
reaches a network or a real CLI.
Model-choice Round 1: the organs think through organs/thinker.py, which renders a failed CLI
with the shared detail (least_privilege.failed_run_detail) inside a TYPED failure; the class
binder is now "every think() call site catches ThinkError and reports it".
At c6d7af2: dream_on_pair has no `on_failure`, the warning's detail is empty, the
summary has no phases_fallback, and the synthesis mouth logs nothing -- red throughout.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGANS = ROOT / "organs"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
SAID_ON_STDOUT = "Claude usage limit reached for this window (fake, test-only)"

DRIVER = textwrap.dedent(r'''
    import json, logging, sys
    sys.path.insert(0, sys.argv[1])
    records = []
    class _Grab(logging.Handler):
        def emit(self, rec):
            records.append(rec.levelname + " " + rec.getMessage())
    logging.getLogger().addHandler(_Grab()); logging.getLogger().setLevel(logging.DEBUG)
    mode = sys.argv[2]
    out = {}
    if mode == "pair":
        import memory_dreaming as md
        failures = []
        a = {"id": 1, "content": "alpha memory about sleep", "summary": "alpha", "category": "insight",
             "tags": "", "created_at": "2026-09-01 00:00:00", "source_project": "p1", "kind": "episodic"}
        b = {"id": 2, "content": "beta memory about dreams", "summary": "beta", "category": "insight",
             "tags": "", "created_at": "2026-09-02 00:00:00", "source_project": "p2", "kind": "episodic"}
        out["ret"] = md.dream_on_pair(a, b, "divergent", timeout_seconds=30, on_failure=failures.append)
        out["failures"] = failures
    elif mode == "cycle":
        import dream_pipeline as dp
        def phase_two_llm_calls(conn, cfg, batch_id):
            n = sum(1 for _ in range(2) if dp._claude_p("summarise these", cfg) is None)
            return {"status": "ok", "summarized": 2 - n}
        dp.PHASES[:] = [dp.Phase("synthetic_llm_phase", phase_two_llm_calls, nocturnal=True)]
        res = dp.run_dream_cycle(sys.argv[3], config={"lock_file": sys.argv[4]})
        out["summary"] = {k: res.get(k) for k in ("status", "phases_ok", "phases_error", "phases_fallback",
                                                  "llm_calls_failed")}
        out["phase"] = [{k: v for k, v in p.items() if k != "result"} for p in res.get("phases", [])]
    out["log"] = records
    print(json.dumps(out))
''')


class TestDreamLlmFailureIsVisible(unittest.TestCase):
    def setUp(self):
        for need in ("memory_dreaming.py", "dream_pipeline.py", "least_privilege.py", "schema_migrator.py"):
            if not (ORGANS / need).is_file():
                raise unittest.SkipTest("tree lacks organs/%s" % need)
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="dreamllm_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.home = self.tmp / "home"
        (self.home / ".voidloom").mkdir(parents=True)
        (self.home / ".voidloom" / "claude-oauth-token").write_text("not-a-real-token\n", encoding="utf-8")
        fakebin = self.tmp / "fakebin"
        sys.path.insert(0, str(ROOT / "voidloom" / "tests"))
        try:
            import _thinker_fakes as F
        finally:
            sys.path.pop(0)
        fake = F.make_fake(fakebin, "claude")                     # answers the set-time probe ...
        (self.tmp / "driver.py").write_text(DRIVER, encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("WIZARD_LIBRARY_DB", "WIZARD_LIBRARY_HOME", "VOIDLOOM_ENV_DIR", "PYTHONPATH",
                                 "CLAUDE_CODE_OAUTH_TOKEN", "TMPDIR")}
        self.env.update({"HOME": str(self.home), "PATH": str(fakebin) + ":/usr/local/bin:/usr/bin:/bin",
                         "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_LIBRARY_DB": str(self.tmp / "claude_memory.db")})
        rec = subprocess.run([sys.executable, "-B", str(ORGANS / "thinker.py"), "set", "claude", "--probe-env",
                              "direct", "--answered-by", "test"], capture_output=True, text=True, env=self.env,
                             timeout=120)
        self.assertEqual(rec.returncode, 0, (rec.stdout[-800:], rec.stderr[-800:]))
        F.set_behaviour(fake, mode="fail", said=SAID_ON_STDOUT)   # ... then fails, saying why ONLY on stdout

    def _drive(self, *args):
        p = subprocess.run([sys.executable, "-B", str(self.tmp / "driver.py"), str(ORGANS)] + list(args),
                           capture_output=True, text=True, env=self.env, timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout[-800:], p.stderr[-1500:]))
        return json.loads(p.stdout.strip().splitlines()[-1])

    def test_the_detail_is_stderr_else_stdout_tail_else_said_empty(self):
        sys.path.insert(0, str(ORGANS))
        try:
            import importlib
            lp = importlib.import_module("least_privilege")
            importlib.reload(lp)
        finally:
            sys.path.remove(str(ORGANS))
        from types import SimpleNamespace as R
        self.assertIn("boom", lp.failed_run_detail(R(stderr="boom\n", stdout="ignored")))
        d = lp.failed_run_detail(R(stderr="", stdout="line one\n" + SAID_ON_STDOUT + "\n"))
        self.assertTrue(d.startswith("stdout"), d)
        self.assertIn(SAID_ON_STDOUT, d)
        self.assertEqual(lp.failed_run_detail(R(stderr="", stdout="")), "no output on stderr or stdout")
        tail = lp.failed_run_detail(R(stderr="", stdout="x" * 2000 + "THE END"), limit=50)
        body = tail.split(": ", 1)[1]
        self.assertTrue(body.endswith("THE END") and body.startswith("...") and len(body) <= 53, tail)

    def test_the_dream_warning_carries_what_claude_said_and_the_failure_is_reported(self):
        out = self._drive("pair")
        self.assertIsNone(out["ret"])
        self.assertEqual(len(out["failures"]), 1, out)
        self.assertIn(SAID_ON_STDOUT, out["failures"][0])
        warns = [l for l in out["log"] if l.startswith("WARNING") and "exited 1" in l]
        self.assertEqual(len(warns), 1, out["log"])
        self.assertIn(SAID_ON_STDOUT, warns[0], "the warning is blank after the colon again: %r" % warns[0])

    def test_the_run_summary_counts_the_phase_that_fell_back(self):
        sys.path.insert(0, str(ROOT / "voidloom" / "tests"))
        try:
            from _owned_brain import packaged_brain
        finally:
            sys.path.pop(0)
        db = packaged_brain(self.tmp / "claude_memory.db")
        out = self._drive("cycle", str(db), str(self.tmp / "dream.lock"))
        s = out["summary"]
        # ROUND 1 (lane L5): this fake's failure is a QUOTA message, and an unavailable thinker makes the night
        # STARVED (organs/memory_dreaming.py EXIT_STARVED): the run says so instead of "ok". The R8 property this
        # test binds -- the failed calls are COUNTED and SAID, never silent -- is unchanged below.
        self.assertEqual(s["status"], "starved", out)
        self.assertEqual(s["phases_error"], 0, "a fallback is not an error: %r" % s)
        self.assertEqual(s["phases_fallback"], 1, "the phase that fell back was not counted: %r" % s)
        self.assertEqual(s["llm_calls_failed"], 2, s)
        ph = out["phase"][0]
        self.assertEqual(ph.get("llm_fallbacks"), 2, ph)
        self.assertTrue(any(SAID_ON_STDOUT in r for r in ph.get("llm_fallback_reasons", [])), ph)
        warns = [l for l in out["log"] if l.startswith("WARNING") and "synthesis" in l and SAID_ON_STDOUT in l]
        # The first call is asked and fails loudly; the second is NOT asked once the thinker is known unavailable
        # tonight (round 1) -- and is still counted and said, in llm_fallback_reasons ("... not asked").
        self.assertEqual(len(warns), 1, "the synthesis mouth failed silently: %r" % out["log"])
        self.assertTrue(any("not asked" in r for r in ph.get("llm_fallback_reasons", [])), ph)
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            detail = c.execute("SELECT detail FROM dream_phase_log WHERE phase_name='synthetic_llm_phase'").fetchone()[0]
        finally:
            c.close()
        self.assertIn("fell back", detail or "", "dream_phase_log does not say the phase fell back: %r" % detail)


class TestEveryThinkCallSiteReportsItsFailure(unittest.TestCase):
    """The class, not the instance: every organ call of thinker.think() sits in a try whose handler catches the
    typed ThinkError and REPORTS it (a logger.warning, or a returned message carrying str(e)). An organ that
    swallows a failure silently is the reference host's blank warning waiting to recur. The old stderr-slice check
    stays: no organ renders a subprocess result's stderr slice for a message any more."""

    def test_every_call_site_catches_and_reports(self):
        import ast
        sites, missing = [], []
        for f in sorted(ORGANS.glob("*.py")):
            if f.name in ("thinker.py", "least_privilege.py"):
                continue
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            parents = {}
            for node in ast.walk(tree):
                for ch in ast.iter_child_nodes(node):
                    parents[ch] = node
            for n in ast.walk(tree):
                if not (isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "think"):
                    continue
                sites.append("%s:%d" % (f.name, n.lineno))
                cur, ok = n, False
                while cur in parents and not ok:
                    cur = parents[cur]
                    if isinstance(cur, ast.Try):
                        for h in cur.handlers:
                            names = ast.dump(h.type) if h.type is not None else ""
                            if "ThinkError" not in names:
                                continue
                            body = ast.dump(ast.Module(body=h.body, type_ignores=[]))
                            if "'warning'" in body or ("Name(id='e'" in body and "str" in body):
                                ok = True
                if not ok:
                    missing.append("%s:%d calls think() without catching ThinkError and reporting it"
                                   % (f.name, n.lineno))
            if not any(x.startswith(f.name + ":") for x in sites):
                continue                                  # the stderr rule binds the organs that think
            for n in ast.walk(tree):
                if not (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)):
                    continue
                v = n.value
                if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) and v.func.attr == "strip":
                    v = v.func.value
                if (isinstance(v, ast.Attribute) and v.attr == "stderr" and isinstance(v.value, ast.Name)
                        and v.value.id in ("result", "res", "proc")):
                    missing.append("%s:%d slices %s.stderr for a message" % (f.name, n.lineno, v.value.id))
        self.assertGreaterEqual(len(sites), 8, "the think() census found too few call sites: %s" % sites)
        self.assertEqual(missing, [], "think() failures not reported: %s" % missing)


class TestTokenDoctorSaysWhatClaudeSaid(unittest.TestCase):
    """Iteration 2, item 14 -- the R8 class outside the dream. tools/token_doctor.py --ping printed
    "INVALID -- claude -p did not authenticate ... Likely expired/revoked" on ANY non-zero exit and
    never what claude said, so a usage limit read as a revoked token. With a FAKE `claude` first on
    PATH that exits 1 saying why only on stdout: a failure that names no auth problem is FAILED, an
    auth failure is INVALID, and both lines carry what claude said. No network, no real CLI."""

    def setUp(self):
        self.tool = ROOT / "tools" / "token_doctor.py"
        if not self.tool.is_file():
            raise unittest.SkipTest("tree lacks tools/token_doctor.py")
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="tokdoc_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        (self.tmp / "home").mkdir()
        self.fakebin = self.tmp / "fakebin"
        self.fakebin.mkdir()

    def _ping(self, said):
        (self.fakebin / "claude").write_text("#!/bin/sh\n# test double: fails, and says why ONLY on stdout\n"
                                             "echo '%s'\nexit 1\n" % said, encoding="utf-8")
        os.chmod(str(self.fakebin / "claude"), 0o755)
        # NO PYTHONPATH: the tool is run BY PATH, as docs/ONBOARDING_GUIDE.md documents it (iteration 3, item 8:
        # with PYTHONPATH=organs/ this test hid that the documented invocation died at `import voidloom_env`)
        env = {"PATH": str(self.fakebin) + ":/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp / "home"),
               "PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_CODE_OAUTH_TOKEN": "not-a-real-token-test-only"}
        p = subprocess.run([sys.executable, "-B", str(self.tool), "--ping"], capture_output=True, text=True,
                           env=env, timeout=120)
        self.assertEqual(p.returncode, 4, (p.stdout[-1200:], p.stderr[-800:]))
        return p.stdout

    def test_a_failure_that_names_no_auth_problem_is_failed_and_says_what_claude_said(self):
        out = self._ping(SAID_ON_STDOUT)
        self.assertIn("FAILED -- claude -p exited 1: ", out, out[-1200:])
        self.assertIn(SAID_ON_STDOUT, out, "the verdict line must carry what claude said: %r" % out[-1200:])
        self.assertNotIn("INVALID", out, "a usage limit is not a revoked token: %r" % out[-1200:])

    def test_an_auth_failure_is_invalid_and_says_what_claude_said(self):
        said = "Invalid API key (fake, test-only)"
        out = self._ping(said)
        self.assertIn("INVALID -- claude -p exited 1: ", out, out[-1200:])
        self.assertIn(said, out)


if __name__ == "__main__":
    unittest.main()
