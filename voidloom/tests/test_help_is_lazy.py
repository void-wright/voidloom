"""`voidloom --help` imports neither the thinker nor numpy.

THE CLAIM UNDER TEST
  Printing the verb list must not pay for the brain. `voidloom/cli.py` used to bind
  the approval-ref pattern at import time, from the module that owns the approvals
  vocabulary; that import pulls in the approvals backend, the overnight thinker and
  numpy, so `--help` imported all three. The pattern is now derived on first use.

  control   `--help` in a fresh interpreter: rc 0, usage printed, and none of
            numpy / thinker / voidloom_approvals in sys.modules.
  saboteur  validating an approval ref in a fresh interpreter DOES import the
            owner, and a malformed ref is still refused: the vocabulary still comes
            from its owner, it is only fetched later -- and the probe can see an
            import when one happens.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEAVY = ("numpy", "thinker", "voidloom_approvals")

_PROBE = r"""
import io, json, runpy, sys, contextlib
mode = sys.argv[1]
out = io.StringIO()
rc = 0
with contextlib.redirect_stdout(out):
    if mode == "help":
        sys.argv = ["voidloom", "--help"]
        try:
            runpy.run_module("voidloom", run_name="__main__")
        except SystemExit as e:
            rc = e.code or 0
    else:
        import voidloom.cli as cli
        good = cli._validate_approval_ref("self-eval:1")
        bad = cli._validate_approval_ref("not a ref")
        print(json.dumps({"good": good, "bad_code": (bad or {}).get("code")}))
sys.stderr.write(json.dumps({"rc": rc, "loaded": sorted(m for m in %r if m in sys.modules),
                             "stdout": out.getvalue()[:4000]}) + "\n")
""" % (HEAVY,)


def _probe(mode: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    p = subprocess.run([sys.executable, "-c", _PROBE, mode], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=120)
    lines = [l for l in p.stderr.splitlines() if l.startswith("{")]
    if p.returncode != 0 or not lines:
        raise AssertionError(f"probe rc {p.returncode}: {p.stderr[-2000:]}")
    return json.loads(lines[-1])


class HelpIsLazy(unittest.TestCase):
    def test_control_help_imports_no_thinker_and_no_numpy(self):
        r = _probe("help")
        self.assertEqual(r["rc"], 0, r)
        self.assertIn("usage", r["stdout"].lower())
        self.assertEqual(r["loaded"], [], r["loaded"])

    def test_saboteur_validating_a_ref_still_asks_the_vocabulary_owner(self):
        r = _probe("validate")
        self.assertIn("voidloom_approvals", r["loaded"])
        verdict = json.loads(r["stdout"].strip().splitlines()[-1])
        self.assertIsNone(verdict["good"])
        self.assertEqual(verdict["bad_code"], "invalid_approval_ref")


if __name__ == "__main__":
    unittest.main()
