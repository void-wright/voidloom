# -*- coding: utf-8 -*-
"""install.ps1 stops on a typed `init` refusal and a failed seed scrub, and prints the seed self-test's real number.

EXECUTED under Windows PowerShell through WSL interop: each function is lifted from install.ps1 byte for byte and run
with a .cmd stub standing in for the interpreter, so the exit codes and the stderr folding are PowerShell's own.
Skips where powershell.exe is not reachable; the static half runs everywhere.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PS1 = ROOT / "install" / "install.ps1"
_PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


def _src():
    return PS1.read_text(encoding="utf-8-sig")


def _fn(name):
    s = _src()
    i = s.index("function %s {" % name)
    return s[i:s.index("\n}\n", i) + 3]


class TheCallSites(unittest.TestCase):
    """Every `init` and every seed load goes through the function that keeps its words."""

    def test_no_init_is_sent_to_null(self):
        code = [l for l in _src().splitlines() if not l.lstrip().startswith("#")]
        self.assertEqual([l for l in code if "claude_memory.py" in l and " init " in l + " " and "*> $null" in l], [])
        self.assertEqual(sum("Invoke-BrainInit" in l for l in code), 3)      # the definition and two callers

    def test_no_seed_load_is_discarded(self):
        self.assertNotIn('"load-seed", "--file", $Seed) | Out-Null', _src())

    def test_the_self_test_runs_where_the_seed_embeds(self):
        s = _src()
        i = s.index('Warn "embedding the seed pack did not complete')
        self.assertIn("Invoke-SeedSelfTest", s[i:i + 260])


@unittest.skipUnless(os.path.isfile(_PS), "no powershell.exe reachable (not a WSL host with interop)")
class UnderPowerShell(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = Path(self.td.name)
        (self.tmp / "organs").mkdir()
        (self.tmp / "seed").mkdir()
        (self.tmp / "organs" / "seed_loader.py").write_text("", encoding="utf-8")
        (self.tmp / "seed" / "seed_doctrine_queries.json").write_text("{}", encoding="utf-8")

    @staticmethod
    def _unc(p):
        return subprocess.run(["wslpath", "-w", str(p)], capture_output=True, text=True, timeout=30).stdout.strip()

    def _stub(self, lines, rc, to_stderr=False):
        body = "".join("echo %s%s\r\n" % (l.replace("%", "%%"), " 1>&2" if to_stderr else "") for l in lines)
        f = self.tmp / "py.cmd"
        f.write_bytes(("@echo off\r\n" + body + "exit /b %d\r\n" % rc).encode("ascii"))
        return f

    def _ps(self, stub, call):
        script = "\n".join([
            "$ErrorActionPreference = 'Continue'",
            'function Say ($m) { Write-Host "[install] $m" }',
            'function Warn ($m) { Write-Host "WARN: $m" }',
            'function Die ($m) { Write-Host "FATAL: $m"; exit 1 }',
            _fn("Invoke-PyOut"), _fn("Invoke-BrainInit"), _fn("Invoke-SeedLoad"), _fn("Invoke-SeedSelfTest"), _fn("Invoke-SkillColumn"),
            "$BrainDir = '%s'" % self._unc(self.tmp), "$VenvPy = '%s'" % self._unc(stub), "$PySys = $VenvPy",
            "$Lite = 'lite.py'; $Seed = 'pack.json'; $DbPath = 'b.db'",
            call, "Write-Host 'REACHED_THE_END'",
        ])
        f = self.tmp / "t.ps1"
        f.write_bytes(script.replace("\n", "\r\n").encode("utf-8-sig"))
        return subprocess.run([_PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", self._unc(f)],
                              capture_output=True, text=True, timeout=120)

    def test_the_whole_file_parses(self):
        cmd = ("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$t,[ref]$e);"
               "$e.Count" % self._unc(PS1))
        p = subprocess.run([_PS, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.stdout.strip(), "0", p.stdout + p.stderr)

    def test_a_typed_refusal_on_stderr_stops_and_is_shown(self):
        p = self._ps(self._stub(["noise", "WEIGHTS_MISMATCH: the lock names another tier"], 1, to_stderr=True),
                     "$r = Invoke-BrainInit; Write-Host \"rc=$r\"")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("init refused, typed", p.stdout)
        self.assertIn("WEIGHTS_MISMATCH", p.stdout)
        self.assertNotIn("REACHED_THE_END", p.stdout)

    def test_an_untyped_failure_returns_with_the_last_line(self):
        p = self._ps(self._stub(["no sqlite_vec here"], 1), "$r = Invoke-BrainInit; Write-Host \"rc=$r cause=$($script:InitCause)\"")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("rc=1 cause=no sqlite_vec here", p.stdout)

    def test_control_a_clean_init_returns_zero_and_nothing_else(self):
        p = self._ps(self._stub(["all fine"], 0), "$r = Invoke-BrainInit; Write-Host \"rc=[$r]\"")
        self.assertIn("rc=[0]", p.stdout, p.stdout + p.stderr)

    def test_a_failed_scrub_stops(self):
        p = self._ps(self._stub(['{"error":"SEED_DENYLIST_HIT"}'], 4), "Invoke-SeedLoad")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("denylist scrub", p.stdout)
        self.assertNotIn("REACHED_THE_END", p.stdout)

    def test_any_other_seed_failure_is_a_note(self):
        p = self._ps(self._stub(["nope"], 2), "Invoke-SeedLoad")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("just emptier", p.stdout)
        self.assertIn("REACHED_THE_END", p.stdout)

    def test_a_column_that_cannot_be_added_warns_and_goes_on(self):
        p = self._ps(self._stub(["nope"], 1), "Invoke-SkillColumn")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("WARN: could not add the skill-graduation column (rc 1)", p.stdout)
        self.assertIn("REACHED_THE_END", p.stdout)

    def test_control_a_column_added_says_nothing(self):
        p = self._ps(self._stub(["{}"], 0), "Invoke-SkillColumn")
        self.assertNotIn("WARN", p.stdout, p.stdout + p.stderr)

    def test_the_self_test_prints_the_engines_number(self):
        line = json.dumps({"line": "seed doctrine self-test: 24 of 25: PASS"}).replace('"', '"')
        p = self._ps(self._stub([line], 0), "Invoke-SeedSelfTest; Write-Host \"offline=[$env:HF_HUB_OFFLINE]\"")
        self.assertIn("24 of 25: PASS", p.stdout, p.stdout + p.stderr)
        self.assertIn("offline=[]", p.stdout, "the offline flag outlived the self-test")

    def test_below_the_bar_warns_and_goes_on(self):
        p = self._ps(self._stub([json.dumps({"line": "seed doctrine self-test: 20 of 25: RED"})], 5), "Invoke-SeedSelfTest")
        self.assertIn("WARN: seed doctrine self-test: 20 of 25: RED", p.stdout, p.stdout + p.stderr)
        self.assertIn("REACHED_THE_END", p.stdout)

    def test_a_self_test_that_could_not_run_is_not_a_pass(self):
        p = self._ps(self._stub(["Traceback"], 1), "Invoke-SeedSelfTest")
        self.assertIn("did not run (rc 1)", p.stdout, p.stdout + p.stderr)
        self.assertNotIn("PASS", p.stdout)


if __name__ == "__main__":
    unittest.main()
