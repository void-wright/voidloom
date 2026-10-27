# -*- coding: utf-8 -*-
"""THE ENV-FILE WRITER PASS'S CARRIED RESIDUE, closed in the model-choice round (Round 1, Wave B; ROUND1_INHERITED).

  R-3 (EW5-03)  a quoted value that spans lines and closes LATER was told "keep the value and quote it" -- a value that
                holds a line break has no one-line spelling, so the words looped. Now: end it on its first line.
                (Class-level proof: the reflector's own fuzz + replay, seeds 1231/9876, variants A/B -- LOOP 1 each on
                0b061d7, 0 on these bytes: iterations/scratch_records/r1_waveB/fuzz_r3/.)
  EW5-04        an end marker swallowed by a value INSIDE the old block: the refusal names the line.
  EW5-05        `KEY=v # note\\` + a next line: systemd joins the next line into KEY, a shell reads it on its own --
                refused in the ambient shape (it was ACCEPTED with a warning), the line named.
  R-4 (EW5-08)  cron/platform-detection.sh rendered Environment=CUDA_VISIBLE_DEVICES=0 into units by default: now the
                owner's declaration (wizard-config.json gpu_device_index) or nothing.
  R-7 (EW4-06)  no `eval` runs a HOME-derived path: install.sh's pip line (an array) and install_council.sh (argv, no
                eval) -- under a CLAUDE_HOME holding a ' the council installer printed "installed" for copies that
                failed (a false success); now it installs, or says FAILED and exits non-zero.
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
BEGIN, END = "# >>> wizard-library managed >>>", "# <<< wizard-library managed <<<"


def _env_file_module():
    spec = importlib.util.spec_from_file_location("env_file_residue", str(ROOT / "install" / "env_file.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Tmp(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="residue_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))

    def _check(self, text, shape="ambient"):
        m = _env_file_module()
        p = self.tmp / "envf"
        p.write_text(text, encoding="utf-8")
        return [(l, msg) for l, msg in m.check(m.read_file(str(p), BEGIN, END), str(p), shape, str(ROOT / "organs"))]


class TestTheRefusalWordsConverge(_Tmp):

    def test_r3_a_value_holding_a_line_break_is_ended_not_requoted(self):
        m = _env_file_module()
        text = "OWNER_H='a\nb'\nWIZARD_CUDA_DEVICE=1\n"
        res = m.parse(text.encode(), BEGIN, END)
        outside = [u for u in res["units"] if not u.marker and not u.inside]
        info = m._line_info(outside, None, res["outside"])
        words = m._remedy(("shell", 1), "WIZARD_CUDA_DEVICE", info)
        self.assertNotIn("keep the value and quote it", words, "the looping advice: a value with a line break has no "
                                                              "one-line spelling")
        self.assertIn("The line to change is line 1: the value of OWNER_H that starts there holds a line break (its quote "
                      "opens there and closes only on line 2)", words)
        self.assertIn("end it on line 1 (close its quote there)", words)
        after = "OWNER_H='a'\nb'\nWIZARD_CUDA_DEVICE=1\n"                 # the words, followed literally
        res2 = m.parse(after.encode(), BEGIN, END)
        info2 = m._line_info([u for u in res2["units"] if not u.marker and not u.inside],
                             res2["eof_open_line"] if res2["eof_open"] else None, res2["outside"])
        self.assertNotEqual(m._remedy(("shell", 1), "WIZARD_CUDA_DEVICE", info2), words, "the words repeat: a LOOP")

    def test_ew5_04_an_end_marker_swallowed_inside_the_old_block_names_the_line(self):
        rows = self._check(BEGIN + "\nPATH=/usr/bin\nWIZARD_CUDA_DEVICE=1\\\n" + END + "\nWIZARD_CUDA_DEVICE=1\n")
        refusals = [msg for l, msg in rows if l == "REFUSE"]
        self.assertTrue(refusals, rows)
        self.assertIn("the managed block's end marker is swallowed: the value that starts on line 3, INSIDE the managed "
                      "block, runs over it", refusals[0])
        self.assertIn("The line to change is line 3", refusals[0])
        self.assertNotIn("Leave exactly one pair", refusals[0])

    def test_ew5_05_an_inline_comment_ending_in_a_backslash_is_refused_naming_the_line(self):
        rows = self._check("OWNER_P=1 # c\\\nWIZARD_CUDA_DEVICE=1\n" + BEGIN + "\nPATH=/usr/bin\n" + END + "\n")
        refusals = [msg for l, msg in rows if l == "REFUSE"]
        self.assertTrue(refusals, "accepted: systemd swallows WIZARD_CUDA_DEVICE into OWNER_P, a shell reads it: %r" % rows)
        self.assertIn("an inline '#' comment ending in a backslash: systemd joins lines 1-2", refusals[0])
        self.assertIn("The line to change is line 1", refusals[0])


class TestPlatformDetectionRendersTheOwnersPinOrNothing(_Tmp):

    def _generate(self, config=None, env_file=None):
        home = self.tmp / ("home%d" % len(list(self.tmp.iterdir())))
        (home / ".voidloom").mkdir(parents=True)
        (home / "tmp").mkdir()
        if config is not None:
            (home / ".voidloom" / "wizard-config.json").write_text(config, encoding="utf-8")
        if env_file is not None:
            (home / ".voidloom" / "wizard-library-env").write_text(env_file, encoding="utf-8")
        env = {"HOME": str(home), "PATH": "/usr/local/bin:/usr/bin:/bin", "TMPDIR": str(home / "tmp"),
               "XDG_CONFIG_HOME": str(home / ".config"), "PYTHONDONTWRITEBYTECODE": "1"}
        p = subprocess.run(["bash", str(ROOT / "cron" / "platform-detection.sh"), "generate", "--dry-run"],
                           capture_output=True, text=True, env=env, timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        return p.stdout + p.stderr

    def test_no_declaration_renders_no_pin_and_a_declaration_renders_it(self):
        out = self._generate(env_file="WIZARD_CUDA_DEVICE=1\n")
        self.assertNotIn("CUDA_VISIBLE_DEVICES", out, "a unit pin nobody declared (it outranks the env file's card)")
        out = self._generate(config='{"gpu_device_index": 1}')
        self.assertIn("Environment=CUDA_VISIBLE_DEVICES=1", out)
        self.assertNotIn("CUDA_VISIBLE_DEVICES=0", out)
        out = self._generate(config='{"gpu_device_index": 0}')
        self.assertIn("Environment=CUDA_VISIBLE_DEVICES=0", out, "a DECLARED card 0 is the owner's word, honoured")


class TestNoEvalRunsAHomeDerivedPath(_Tmp):

    def test_no_eval_in_the_installers(self):
        for rel in ("install.sh", "install/install_council.sh"):
            code = [l for l in (ROOT / rel).read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#")]
            bad = [l.strip() for l in code if re.search(r"(^|[;&|(\s])eval\s", l)]
            self.assertEqual(bad, [], "%s runs eval: %r" % (rel, bad))

    def test_the_council_installs_under_a_quoted_home_or_says_it_did_not(self):
        pkg = self.tmp / "pkg"
        (pkg / "install").mkdir(parents=True)
        shutil.copytree(str(ROOT / "persona"), str(pkg / "persona"))
        (pkg / "commands").mkdir()
        shutil.copy2(str(ROOT / "persona" / "commands" / "council.md"), str(pkg / "commands" / "council.md"))
        shutil.copy2(str(ROOT / "install" / "install_council.sh"), str(pkg / "install" / "install_council.sh"))
        ch = self.tmp / "o'brien" / ".claude"
        ch.mkdir(parents=True)
        env = {"HOME": str(self.tmp / "o'brien"), "PATH": "/usr/local/bin:/usr/bin:/bin", "WIZARD_CLAUDE_HOME": str(ch)}
        p = subprocess.run(["bash", str(pkg / "install" / "install_council.sh")], capture_output=True, text=True, env=env,
                           timeout=120)
        said = p.stdout.count("installed (")
        on_disk = len(list((ch / "skills").glob("council-*"))) + len(list((ch / "commands").glob("council.md")))
        self.assertEqual((p.returncode, said, on_disk), (0, 6, 6), "claimed %d, on disk %d: %s" % (said, on_disk, p.stdout[-800:]))
        # a destination that cannot be written: FAILED, named, rc != 0 -- never "installed"
        shutil.rmtree(str(ch / "skills"))
        shutil.rmtree(str(ch / "commands"))
        (ch / "skills").write_text("not a directory\n", encoding="utf-8")
        p = subprocess.run(["bash", str(pkg / "install" / "install_council.sh")], capture_output=True, text=True, env=env,
                           timeout=120)
        self.assertNotEqual(p.returncode, 0, p.stdout[-800:])
        self.assertIn("FAILED", p.stdout)
        self.assertNotIn("Inner Council installed.", p.stdout)


if __name__ == "__main__":
    unittest.main()
