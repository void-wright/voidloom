# -*- coding: utf-8 -*-
"""install.ps1 runs, whole, under Windows PowerShell through WSL interop, and leaves a brain that answers.

Every other test of this file extracts text or one function. That is how a fresh install that could not finish went
unseen: a `Copy-Item` that lays `organs\\` as an empty folder is invisible until the script runs. Here the script runs,
in a scratch folder under the Windows temp directory, with USERPROFILE redirected there. The one line that writes the
Windows user PATH is neutralised in this copy: a test never changes the account it runs under.

Skips where powershell.exe or a Windows Python is not reachable.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_PS = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
PATH_WRITE = re.compile(r'^(\s*)\[Environment\]::SetEnvironmentVariable\("Path".*$', re.M)


def _ps(code: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run([_PS, "-NoProfile", "-Command", code], capture_output=True, timeout=timeout, cwd="/",
                          text=True, encoding="utf-8", errors="replace")


@unittest.skipUnless(os.path.isfile(_PS), "no powershell.exe reachable (not a WSL host with interop)")
@unittest.skipUnless((ROOT / ".git").exists(), "not a checkout: the file list comes from git")
class TheScriptRuns(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        got = _ps("(Get-Command python -ErrorAction SilentlyContinue).Source; [IO.Path]::GetTempPath()")
        lines = [ln.strip() for ln in got.stdout.splitlines() if ln.strip()]
        if len(lines) < 2:
            raise unittest.SkipTest("no Windows Python on PATH")
        cls.win = lines[-1].rstrip("\\") + f"\\vl_ps1_{os.getpid()}"
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
        run = cls.root / "run.ps1"
        run.write_text(f"""$env:USERPROFILE='{cls.win}\\home'; $env:HOME='{cls.win}\\home'
$env:TEMP='{cls.win}'; $env:TMP='{cls.win}'; $env:HF_HOME='{cls.win}\\hf'
& '{cls.win}\\pkg\\install\\install.ps1' -Tier0Only -NoWireHooks -Approvals manual
"rc=" + $LASTEXITCODE
""", encoding="utf-8")
        (cls.root / "home").mkdir()
        p = subprocess.run([_PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", cls.win + "\\run.ps1"], cwd="/",
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=400)
        cls.out = p.stdout + p.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(getattr(cls, "root", "/nonexistent")), ignore_errors=True)

    def test_saboteur_the_fresh_install_finishes(self):
        self.assertIn("rc=0", self.out, self.out[-2000:])
        self.assertIn("Tier 0 VERIFIED", self.out, self.out[-2000:])
        self.assertNotIn("FATAL", self.out)

    def test_saboteur_every_folder_is_laid_with_its_files_in_it(self):
        brain = self.root / "home" / ".claude" / "memory"
        for rel in ("organs/claude_memory_lite.py", "organs/embedder_lock.py", "migrations/schema.sql",
                    "install/merge_config.py", "install/brain_is_new.py"):
            self.assertTrue((brain / rel).is_file(), rel)

    def test_saboteur_the_brain_holds_the_seed_and_the_merger_ran(self):
        brain = self.root / "home" / ".claude" / "memory" / "claude_memory.db"
        self.assertGreater(brain.stat().st_size, 0)
        self.assertNotIn("merge_config.py not present", self.out)

    def test_control_the_account_was_not_touched(self):
        self.assertIn("test copy: user PATH write skipped", (self.root / "pkg" / "install" / "install.ps1").read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
