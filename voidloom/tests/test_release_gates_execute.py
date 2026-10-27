# -*- coding: utf-8 -*-
"""The release gates EXECUTE. Not that they are clean — that they run at all.

CRUCIBLE-3 D1-F1 found both shipped release gates "wired to nothing — and both
RED against the tree they guard". Between them they had two references outside
their own files: one manifest row listing them as files, and one comment.

The cycle-2 measurement added the quieter half of the cause: they could not
FINISH. Both walk os.walk(followlinks=True) from the repo root and covered
16,378 files / 3.5 GB of archives/ and ACE_Workspaces/ — rc 124 at 900 SECONDS,
both gates. A gate nobody can run is a gate nobody wires.

So this binds the property that was actually broken, and nothing more:
  * the release check RUNS, to completion, in reasonable time;
  * every gate it drives produces a VERDICT, not a timeout and not a crash;
  * no gate reports a VACUOUS population — a gate that scanned nothing has not
    passed, which is the same defect this cycle removed from the acceptance
    suite (0/0 CHECKS HELD, rc 0).

It deliberately does NOT assert the gates are CLEAN. They are red today, with
real findings, over a tree still under repair; asserting green here would create
exactly one incentive — to make the test pass by adjusting the GATE rather than
the PRODUCT. The reds are reported by tools/release_check.py, and --strict flips
it to blocking when the remaining reds are the ones we chose to keep.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "tools" / "release_check.py"


class TestReleaseGatesExecute(unittest.TestCase):

    def setUp(self):
        if not CHECK.exists():
            self.skipTest(f"release_check.py absent at {CHECK}")

    def _run(self):
        p = subprocess.run([sys.executable, str(CHECK), "--json"],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        self.assertEqual(p.returncode, 0,
                         f"release_check must report, never block: {p.stderr[:400]}")
        return json.loads(p.stdout[p.stdout.index("{"):])

    def test_every_gate_produces_a_verdict(self):
        d = self._run()
        self.assertTrue(d["results"], "no gates ran at all")
        for r in d["results"]:
            self.assertIn("verdict", r, f"{r.get('gate')} produced no verdict")
            self.assertNotEqual(
                r["verdict"], "UNRUNNABLE",
                f"{r['gate']} did not execute: "
                f"timed_out={r.get('timed_out')} err={r.get('envelope_error')}")

    def test_no_gate_scanned_an_empty_population(self):
        # A gate that scanned nothing has not passed. This is the 0/0-CHECKS-HELD
        # defect in gate form, and it is the reason the population comes from the
        # laying mechanism rather than from `git ls-files`.
        for r in self._run()["results"]:
            self.assertNotEqual(r["verdict"], "VACUOUS",
                                f"{r['gate']} scanned an empty population")
            if r.get("population") is not None:
                self.assertGreater(r["population"], 0, f"{r['gate']} population is 0")

    def test_the_gates_are_actually_reached(self):
        # The UNBOUND class itself: a rule with no executor. If this file is the
        # only thing that names release_check.py, that is still one caller more
        # than the gates had — and the name is asserted so a rename cannot quietly
        # orphan them again.
        gates = {r["gate"] for r in self._run()["results"]}
        for expected in ("gpu_pin", "pii", "package_manifest"):
            self.assertIn(expected, gates, f"{expected} is no longer on the release path")


if __name__ == "__main__":
    unittest.main()
