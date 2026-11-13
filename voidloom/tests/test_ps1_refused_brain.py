# -*- coding: utf-8 -*-
"""install.ps1 over a brain `voidloom recall` refuses ends as install.sh ends over it: the failed check, what it means
and its fix are shown, nothing calls the brain usable or active, what install.sh withholds is withheld, and the run
exits 5. A brain recall answers still installs and exits 0.

The script runs under Windows PowerShell through WSL interop, -Tier0Only, in a scratch folder under the Windows temp
directory with USERPROFILE redirected there; the one line that writes the Windows user PATH is neutralised in this copy.
Each brain is laid before the run by the real schema and lock writer. Skips where powershell.exe or a Windows Python is
not reachable.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
PATH_WRITE = re.compile(r'^(\s*)\[Environment\]::SetEnvironmentVariable\("Path".*$', re.M)
sys.path.insert(0, str(ROOT / "organs"))
import embedder_lock as el  # noqa: E402
import schema_migrator  # noqa: E402


def _ps(code: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([_PS, "-NoProfile", "-Command", code], capture_output=True, timeout=timeout, cwd="/",
                          text=True, encoding="utf-8", errors="replace")


@unittest.skipUnless(os.path.isfile(_PS), "no powershell.exe reachable (not a WSL host with interop)")
@unittest.skipUnless((ROOT / ".git").exists(), "not a checkout: the file list comes from git")
class AnInstallOverARefusedBrain(unittest.TestCase):
    STATES = ("healthy", "lost_lock", "model_not_the_locks", "variable_names_another_model")

    @classmethod
    def setUpClass(cls):
        got = _ps("(Get-Command python -ErrorAction SilentlyContinue).Source; [IO.Path]::GetTempPath()")
        lines = [ln.strip() for ln in got.stdout.splitlines() if ln.strip()]
        if len(lines) < 2:
            raise unittest.SkipTest("no Windows Python on PATH")
        cls.win = lines[-1].rstrip("\\") + f"\\vl_ps1_refused_{os.getpid()}"
        cls.root = Path(subprocess.run(["wslpath", "-u", cls.win], capture_output=True, text=True, timeout=30).stdout.strip())
        pkg = cls.root / "pkg"
        pkg.mkdir(parents=True)
        files = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT), capture_output=True, text=True, timeout=60).stdout
        for rel in filter(None, files.split("\0")):
            if rel.startswith("voidloom/tests/"):
                continue
            dst = pkg / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(str(ROOT / rel), str(dst))
        script = pkg / "install" / "install.ps1"
        src = script.read_text(encoding="utf-8-sig")
        src, n = PATH_WRITE.subn(r'\1Say "  test copy: user PATH write skipped"', src)
        assert n == 1, n
        script.write_text("﻿" + src, encoding="utf-8")
        cls.out, cls.brains = {}, {}
        for state in cls.STATES:
            cls.out[state] = cls.install(state)

    @classmethod
    def install(cls, state):
        home = cls.root / state
        brain = home / "home" / ".claude" / "memory"
        brain.mkdir(parents=True)
        db = brain / "claude_memory.db"
        res = schema_migrator.apply(str(db), ROOT / "migrations")
        assert not res.get("files_with_errors"), res
        lock = el.write_lock(brain, "cpu")
        env = ""
        if state == "lost_lock":
            (brain / "embedder.lock.json").unlink()
        elif state == "model_not_the_locks":
            for pin in (lock, lock.get("code")):
                if pin:
                    snap = home / "hf" / "hub" / ("models--" + pin["model_id"].replace("/", "--")) / "snapshots" / pin["revision"]
                    for rel in pin["files"]:
                        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
                        (snap / rel).write_bytes(b"x")
        elif state == "variable_names_another_model":
            env = "$env:WIZARD_EMBED_MODEL='jina'\n"
        w = f"{cls.win}\\{state}"
        run = home / "run.ps1"
        run.write_text(f"""$env:USERPROFILE='{w}\\home'; $env:HOME='{w}\\home'
$env:TEMP='{w}'; $env:TMP='{w}'; $env:HF_HOME='{w}\\hf'; $env:HF_HUB_OFFLINE='1'
{env}& '{cls.win}\\pkg\\install\\install.ps1' -Tier0Only -NoWireHooks -Approvals manual
"rc=" + $LASTEXITCODE
""", encoding="utf-8")
        p = subprocess.run([_PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", w + "\\run.ps1"], cwd="/",
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=400)
        cls.brains[state] = brain
        # a warning wraps at the console width: joined, a line reads as the installer wrote it
        return re.sub(r"\n(?!WARNING:|\[install\]|rc=|\s*$)", "", (p.stdout + p.stderr).replace("\r", ""))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(getattr(cls, "root", "/nonexistent")), ignore_errors=True)

    def assert_refused(self, state, check, fix):
        out = self.out[state]
        self.assertIn("rc=5", out, out[-3000:])
        block = out.split("ACCEPTANCE GATE FAILED", 1)
        self.assertEqual(len(block), 2, out[-3000:])
        self.assertIn(check, block[1])
        self.assertIn("`voidloom recall` refuses this brain", block[1], "what the failed check means")
        self.assertIn("Fix: ", block[1])
        self.assertIn(fix, block[1])
        for claim in ("usable", "Tier 0 VERIFIED", "Tier 0 is ACTIVE", "ACTIVE - keyword-recall brain live",
                      "ACTIVE — keyword-recall brain live", "now has a memory"):
            self.assertNotIn(claim, out, "a refused brain is called working")
        self.assertIn("Persona Forge NOT laid", out)
        self.assertIn("was NOT recorded", out, "the approvals choice")
        self.assertFalse((self.brains[state] / "INSTALL_RECORD").exists(), "the record is written only by a passing run")

    def test_saboteur_a_lost_lock(self):
        self.assert_refused("lost_lock", "recall gate", "init")

    def test_saboteur_model_files_that_are_not_the_locks(self):
        self.assert_refused("model_not_the_locks", "recall model", "model_fetch.py")

    def test_saboteur_a_variable_naming_another_model(self):
        self.assert_refused("variable_names_another_model", "recall model", "Remove-Item Env:WIZARD_EMBED_MODEL")

    def test_control_a_brain_recall_answers(self):
        out = self.out["healthy"]
        self.assertIn("rc=0", out, out[-3000:])
        self.assertIn("Tier 0 VERIFIED", out)
        self.assertNotIn("ACCEPTANCE GATE FAILED", out)
        self.assertTrue((self.brains["healthy"] / "INSTALL_RECORD").is_file())


if __name__ == "__main__":
    unittest.main()
