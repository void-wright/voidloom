# -*- coding: utf-8 -*-
"""THE INSTALLER'S QUESTIONS (model-choice unit, Round 1, Wave B) -- every one asked through THE ASK HELPER, every answer
compared as FIXED TEXT, nothing ever chosen for the owner.

  R-2   THE ASK HELPER compares an answer as fixed text, never as a grep/regex/glob pattern: '.', '-e0', '.*', '[1]' and
        '0*' are asked again (on 0b061d7 the GPU question took them as matches, and '0*' switched the monitor card's
        typed confirmation off -- EW5-02). Its contract comment says so.
  R-1   the ambient upgrade never records a card its own P4 will refuse: such a card is never offered behind a
        confirmation, and the answers are HELD until the proofs pass -- a refused upgrade leaves the env file (and so
        the units) exactly as they were (EW5-01).
  R-5   the interactive GPU question states the AI-agent rule (EW5-09); a refusal without a terminal names the card an
        earlier installer's managed block pins (EW5-12).
  M10   the a-f question: "Should your brain think while you sleep, and with which model?" -- six options with the
        facts FOUND beside each, nothing pre-selected, a blank or pattern-shaped answer asked again, a follow-up for f,
        `--thinker` for every NAME (and the letters, d and f with their one-release note), no terminal + no answer + timers armed or about to be -> rc 2 with nothing
        touched, a recorded answer re-used (never re-asked), a Claude-era upgrade ASKS (past usage is evidence), the
        record written only after the proof, and the gate in install/install_crons.sh too.
  M11   README.md and docs/ONBOARDING_GUIDE.md put the question to an installing agent WORD FOR WORD, with the rule.

Every run is a scratch HOME (F4), a FAKE nvidia-smi where a GPU question arises, a FAKE model CLI and a recording
systemd-run shim where a proof runs -- no real card, no credential, no network, no user manager.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voidloom.tests.test_tier1_ambient import (_AmbientHome, _drive_tty, _fake_nvidia_smi, _tree, BEGIN, END, SH, ROOT,
                                               Q_MONITOR, Q_WORK)

CATALOG = ROOT / "organs" / "thinkers.json"
Q_THINKER = r"Your answer \(a, b, c, d, e or f\):"
PATTERNS = (".", "-e0", ".*", "[1]", "0*")
# a scripted terminal waits this long for a question that never comes (a RED tree asks something else, or nothing)
TTY_TIMEOUT = int(os.environ.get("VOIDLOOM_TTY_TIMEOUT") or 600)


def _question():
    return json.loads(CATALOG.read_text(encoding="utf-8"))["question"]


SHIM = r'''#!%(py)s
# a RECORDING stand-in for `systemd-run --user`: it logs the call, then runs the command the way the user manager would
# -- the env file's assignments as systemd reads them (install/env_file.py, bound to libsystemd), the -p Environment=
# and --setenv values, the WorkingDirectory -- and nothing of the caller's environment
import os, subprocess, sys
sys.path.insert(0, %(install)r)
import env_file
args = sys.argv[1:]
open(%(log)r, "a").write("systemd-run " + " ".join(args) + "\n")
env, cwd, i = {}, "/", 0
while i < len(args):
    a = args[i]
    if a in ("--user", "--wait", "--pipe", "--collect", "--quiet"):
        i += 1; continue
    if a == "--unit":
        i += 2; continue
    if a == "-p":
        k, _, v = args[i + 1].partition("=")
        if k == "EnvironmentFile":
            f = v.lstrip("-")
            if os.path.exists(f):
                env.update({x: y[0] for x, y in env_file.read_file(f)["effective"].items()})
        elif k == "WorkingDirectory":
            cwd = v
        elif k == "Environment":
            x, _, y = v.partition("="); env[x] = y
        i += 2; continue
    if a.startswith("--setenv="):
        x, _, y = a[len("--setenv="):].partition("="); env[x] = y
        i += 1; continue
    break
env.setdefault("PATH", "/usr/bin:/bin")
sys.exit(subprocess.run(args[i:], env=env, cwd=cwd, stdin=subprocess.DEVNULL).returncode)
'''


class _Home(_AmbientHome):
    def setUp(self):
        super().setUp()
        if not sys.platform.startswith("linux"):
            self.skipTest("the scripted terminal is a Linux pty")
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.mlog = self.tmp / "manager_calls.log"
        (self.bin / "systemd-run").write_text(SHIM % {"py": sys.executable, "install": str(ROOT / "install"),
                                                     "log": str(self.mlog)}, encoding="utf-8")
        os.chmod(str(self.bin / "systemd-run"), 0o755)
        nogpu = self.tmp / "no_gpu"
        nogpu.mkdir()
        (nogpu / "nvidia-smi").write_text("#!/bin/sh\necho 'NVIDIA-SMI has failed (test double)' >&2\nexit 9\n")
        os.chmod(str(nogpu / "nvidia-smi"), 0o755)
        self.path = "%s:%s:/usr/local/bin:/usr/bin:/bin" % (self.bin, nogpu)

    def _fake(self, name="claude", **behaviour):
        sys.path.insert(0, str(ROOT / "voidloom" / "tests"))
        try:
            import _thinker_fakes as F
        finally:
            sys.path.pop(0)
        return F.make_fake(self.bin, name, log=str(self.tmp / "fake.log"), **behaviour)

    def _cred(self, backend="claude"):
        th = self.home / ".voidloom" / "thinker"
        th.mkdir(parents=True, exist_ok=True)
        os.chmod(str(th), 0o700)
        name = {"claude": "CLAUDE_CODE_OAUTH_TOKEN", "kimi": "KIMI_MODEL_API_KEY"}[backend]
        (th / ("%s.env" % backend)).write_text("%s=fake-credential-0123456789abcdef\n" % name)
        os.chmod(str(th / ("%s.env" % backend)), 0o600)

    def _run(self, *args, **extra):
        env = self._env(**dict({"PATH": self.path}, **extra))
        return subprocess.run(["bash", str(SH)] + list(args), capture_output=True, text=True, env=env, timeout=900,
                              cwd=str(self.tmp))

    def _tty(self, answers, *args, **extra):
        env = self._env(**dict({"PATH": self.path}, **extra))
        return _drive_tty(["bash", str(SH)] + list(args), env, answers, str(self.tmp), timeout=TTY_TIMEOUT)

    def _record(self):
        p = self.brain / "THINKER_RECORD"
        if not p.exists():
            return None
        return dict(l.split("=", 1) for l in p.read_text(encoding="utf-8").splitlines() if "=" in l and not l.startswith("#"))


# ================================================================================================ R-2 (EW5-02)
class TestTheAskHelperComparesFixedText(_Home):

    def test_the_contract_comment_says_fixed_text_and_no_check_builds_a_pattern(self):
        src = SH.read_text(encoding="utf-8")
        helper = src[src.index("# THE ASK HELPER"):src.index("(end of THE ASK HELPER)")]
        self.assertIn("EVERY CHECK COMPARES THE ANSWER AS FIXED TEXT", helper)
        for p in ("'.'", "'-e0'", "'.*'", "'[1]'", "'0*'"):
            self.assertIn(p, helper, "the contract comment must name %s" % p)
        # the class: no grep in install.sh matches an ANSWER variable as a pattern (-x without -F)
        bad = [l.strip() for l in src.splitlines()
               if re.search(r'grep\s+-q?x\b(?![^|]*F)[^|]*"\$', l) and not l.lstrip().startswith("#")]
        self.assertEqual(bad, [], "a grep matches an answer as a PATTERN: %r" % bad)

    def test_pattern_shaped_gpu_answers_are_asked_again_and_never_skip_the_confirmation(self):
        _fake_nvidia_smi(self.tmp / "fake2", {"0": "Disabled", "1": "Disabled"})
        env = self._env(PATH=str(self.tmp / "fake2") + ":/usr/local/bin:/usr/bin:/bin", WIZARD_EMBED_MODEL="nomic")
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        answers = [(Q_MONITOR, "."), (Q_MONITOR, "-e0"), (Q_MONITOR, "0*"), (Q_MONITOR, "0"),
                   (Q_WORK, ".*"), (Q_WORK, "[1]"), (Q_WORK, "0"), (r"type 0 again", "0*"), (Q_WORK, "1")]
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--env-only"], env, answers, str(self.tmp), timeout=TTY_TIMEOUT)
        self.assertEqual((rc, left), (0, []), (tty[-2000:], err[-1200:]))
        for p in (".", "-e0", "0*"):
            self.assertIn("'%s' is not one of the cards listed above (0, 1), nor 'none'." % p, tty)
        for p in (".*", "[1]"):
            self.assertIn("'%s' is not one of the cards listed above (0, 1)." % p, tty)
        self.assertIn("Careful: card 0 is the card you said drives your monitor(s)", tty, "the confirmation was skipped")
        self.assertIn("Not confirmed -- choose again.", tty, "'0*' confirmed card 0")
        self.assertIn("If you are an AI agent running this installer for someone: stop, put these two questions to "
                      "them, and pass their answers as --display-gpu/--gpu -- never answer them yourself.", tty)   # EW5-09
        t = self.env_file.read_text(encoding="utf-8")
        self.assertIn("\nWIZARD_DISPLAY_INDEX=0\n", t)
        self.assertIn("\nWIZARD_CUDA_DEVICE=1\n", t)


# ================================================================================================ R-5 (EW5-12), EW5-06
class TestTheRefusalNamesTheOldPin(_Home):

    def test_an_upgrade_without_a_terminal_names_the_card_the_old_block_pins(self):
        _fake_nvidia_smi(self.tmp / "fake2", {"0": "Disabled", "1": "Disabled"})
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text(BEGIN + "\nPATH=/usr/bin:/bin\nCUDA_VISIBLE_DEVICES='1'\n" + END + "\n", encoding="utf-8")
        before = self.env_file.read_bytes()
        p = subprocess.run(["bash", str(SH), "--env-only"], capture_output=True, text=True, timeout=300, cwd=str(self.tmp),
                           env=self._env(PATH=str(self.tmp / "fake2") + ":/usr/local/bin:/usr/bin:/bin",
                                         WIZARD_EMBED_MODEL="nomic"))
        self.assertEqual(p.returncode, 2, p.stderr[-1200:])
        self.assertIn("currently pins card 1 (CUDA_VISIBLE_DEVICES='1', line 3) -- an earlier installer's choice, not a "
                      "declaration", p.stderr)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_a_hand_display_index_that_names_no_card_answers_nothing(self):
        _fake_nvidia_smi(self.tmp / "fake2", {"0": "Disabled", "1": "Disabled"})
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("WIZARD_DISPLAY_INDEX=7\n", encoding="utf-8")
        env = self._env(PATH=str(self.tmp / "fake2") + ":/usr/local/bin:/usr/bin:/bin", WIZARD_EMBED_MODEL="nomic")
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--env-only"], env,
                                             [(Q_MONITOR, "0"), (Q_WORK, "0"), (r"type 0 again", "0")], str(self.tmp),
                                             timeout=TTY_TIMEOUT)
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1200:]))
        self.assertIn("declares WIZARD_DISPLAY_INDEX=7 (line 1), which names no card nvidia-smi lists (0, 1)", err)
        self.assertIn("Careful: card 0 is the card you said drives your monitor(s)", tty)


    def test_a_declared_card_nvidia_smi_does_not_list_is_named(self):
        _fake_nvidia_smi(self.tmp / "fake2", {"0": "Disabled", "1": "Disabled"})
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("WIZARD_CUDA_DEVICE=5\n", encoding="utf-8")
        p = subprocess.run(["bash", str(SH), "--env-only"], capture_output=True, text=True, timeout=300, cwd=str(self.tmp),
                           env=self._env(PATH=str(self.tmp / "fake2") + ":/usr/local/bin:/usr/bin:/bin",
                                         WIZARD_EMBED_MODEL="nomic"))
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("card 5 (declared by WIZARD_CUDA_DEVICE", p.stderr)
        self.assertIn("is not one nvidia-smi lists (0 1)", p.stderr)
        self.assertIn("WIZARD_CUDA_DEVICE=5", self.env_file.read_text(), "the owner's line is his; never rewritten")


# ================================================================================================ R-1 (EW5-01)
class TestTheAmbientUpgradeNeverRecordsARefusedCard(_Home):

    def _two_asleep(self):
        _fake_nvidia_smi(self.tmp / "fake2", {"0": "Disabled", "1": "Disabled"})
        return str(self.tmp / "fake2") + ":" + self.path

    def test_a_display_card_is_not_offered_behind_a_confirmation(self):
        self._brain()
        rc, tty, out, err, left = self._tty([(Q_MONITOR, "0"), (Q_WORK, "0"), (Q_WORK, "1")],
                                            "--tier1-ambient", sys.executable, "--dry-run", "--thinker", "off",
                                            PATH=self._two_asleep())
        self.assertEqual(left, [], (tty[-1500:], err[-1200:]))
        self.assertIn("Card 0 cannot be chosen in this upgrade: this upgrade (--tier1-ambient) PROVES that the brain's "
                      "jobs never share a card with a display (its check P4), and card 0 is the card you said drives your "
                      "monitor(s). Choose another card.", tty)
        self.assertNotIn("type 0 again", tty)

    def test_the_flag_path_refuses_it_and_touches_nothing(self):
        self._brain()
        before = _tree(self.home)
        p = self._run("--tier1-ambient", sys.executable, "--gpu", "0", "--display-gpu", "0", PATH=self._two_asleep())
        self.assertEqual(p.returncode, 2, p.stderr[-1500:])
        self.assertIn("--gpu 0: this upgrade (--tier1-ambient) PROVES that the brain's jobs never share a card with a "
                      "display (its check P4)", p.stderr)
        self.assertEqual(_tree(self.home), before)

    def test_a_refused_upgrade_leaves_the_env_file_exactly_as_it_was(self):
        self._brain()
        venv = self.tmp / "no_torch_venv"
        mk = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], capture_output=True, text=True)
        if mk.returncode != 0:
            self.skipTest("cannot build a bare venv on this host")
        cand = str(venv / "bin" / "python3")
        if subprocess.run([cand, "-c", "import torch"], capture_output=True).returncode == 0:
            self.skipTest("the bare venv imports torch")
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text(BEGIN + "\nPATH=/usr/bin:/bin\n" + END + "\nOWNER_LINE=kept\n", encoding="utf-8")
        before = self.env_file.read_bytes()
        rc, tty, out, err, left = self._tty([(Q_MONITOR, "0"), (Q_WORK, "1")], "--tier1-ambient", cand,
                                            "--thinker", "off", PATH=self._two_asleep())
        self.assertEqual(left, [], (tty[-1500:], err[-1500:]))
        self.assertNotIn(rc, (0, 2), (out[-1500:], err[-1500:]))
        self.assertIn("P1 FAILED", out + err)
        self.assertIn("were HELD for the proofs and never recorded", err)
        self.assertEqual(self.env_file.read_bytes(), before, "a refused upgrade recorded the GPU answers")
        self.assertFalse((self.brain / "organs").exists(), "files were laid after a failed proof")


# ================================================================================================ M10 -- the a-e question
class TestTheOvernightThinkerQuestion(_Home):

    def test_the_question_its_facts_no_preselection_reasks_and_the_d_follow_up(self):
        q = _question()
        answers = [(Q_THINKER, "")] + [(Q_THINKER, x) for x in PATTERNS] + [
            (Q_THINKER, "f"), (r"which model\?.*:", ""), (r"which model\?.*:", "qwen 2"),
            (r"which model\?.*:", "qwen2.5:0.5b"), (r"Answer 1, 2 or 3:", "0*"), (r"Answer 1, 2 or 3:", "1"),
            (r"address .*:", "localhost:11434"), (r"address .*:", "http://127.0.0.1:11434")]
        rc, tty, out, err, left = self._tty(answers, "--dry-run", "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-2500:], err[-1200:]))
        self.assertIn(q["ask"], tty)
        for letter, words, _a in q["options"]:
            self.assertIn("%s) %s" % (letter, words), tty)
        self.assertEqual(tty.count("       found: "), 6, "the facts found are shown beside EACH letter")
        self.assertIn(q["chat_note"], tty)
        self.assertIn(q["agent_rule"], tty)
        self.assertNotRegex(tty, r"(?i)default|\[[a-f]\]|\(recommended\)", "an answer was pre-selected")
        self.assertIn("An answer is needed here (nothing is chosen for you).", tty)
        for p in PATTERNS:
            self.assertIn("'%s' is not one of the letters a, b, c, d, e or f." % p, tty)
        self.assertIn("A model name has no spaces in it", tty)
        self.assertIn("'0*' is not one of 1, 2 or 3.", tty)
        self.assertIn("An address starts with http:// or https://", tty)
        self.assertIn("Your answer: f) Other (which model?) -- qwen2.5:0.5b.", tty)
        self.assertIn("DRY: would show docs/OVERNIGHT_THINKER_SETUP.md Part D", out)
        self.assertFalse((self.home / ".claude").exists(), "a dry run wrote")

    def test_every_name_and_letter_as_a_flag_and_a_wrong_flag_touches_nothing(self):
        local = ["--thinker-model", "tiny", "--thinker-endpoint", "http://127.0.0.1:9", "--thinker-api", "ollama"]
        for flag, extra, want in (("claude", [], "a) Claude"), ("codex", [], "b) Codex"), ("kimi", [], "c) Kimi"),
                                  ("local", local, "f) Other (which model?) (tiny)"), ("off", [], "e) Not now"),
                                  ("a", [], "a) Claude"), ("e", [], "e) Not now"),
                                  ("f", local, "f) Other (which model?) (tiny)")):
            p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--with-crons", "--thinker", flag,
                          *extra)
            self.assertEqual(p.returncode, 0, (flag, p.stdout[-800:], p.stderr[-1200:]))
            self.assertIn("Thinker:      %s" % want, p.stdout, flag)
        before = _tree(self.home)
        for bad in (["--thinker", "g"], ["--thinker", "other"], ["--thinker", "."], ["--thinker", "0*"],
                    ["--thinker", "local"], ["--thinker", "f"],
                    # an OLD scripted d) Other fails CLOSED: d is Gemini now, and Gemini takes no endpoint
                    ["--thinker", "d", "--thinker-model", "tiny", "--thinker-endpoint", "http://x"],
                    ["--thinker", "claude", "--thinker-command", "x {prompt}"], ["--thinker-model", "tiny"]):
            p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", *bad)
            self.assertEqual(p.returncode, 2, (bad, p.stdout[-500:], p.stderr[-800:]))
            self.assertIn("Nothing was touched.", p.stderr, bad)
        self.assertEqual(_tree(self.home), before)

    def test_no_terminal_no_answer_and_timers_about_to_be_armed_is_refused_touching_nothing(self):
        q = _question()
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("OWNER=1\n", encoding="utf-8")
        before = _tree(self.home)
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--with-crons")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("this run arms timers that think (--with-crons: dreaming whisper-digest self-eval)", p.stderr)
        self.assertIn(q["ask"], p.stderr)
        for letter, words, _a in q["options"]:
            self.assertIn("%s) %s" % (letter, words), p.stderr)
        self.assertIn("Pass the answer as --thinker claude|codex|kimi|gemini|off|local.", p.stderr)
        self.assertIn("it must never choose the answer itself", p.stderr)
        self.assertIn("Nothing was touched.", p.stderr)
        self.assertEqual(_tree(self.home), before, "a refused run changed the home (brain and units)")

    def test_timers_already_armed_refuse_too_and_none_armed_is_a_note(self):
        units = self.home / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "wizard-library-dreaming.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
        before = _tree(self.home)
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual(p.returncode, 2, p.stderr[-1200:])
        self.assertIn("this host's timers already think (dreaming", p.stderr)
        self.assertEqual(_tree(self.home), before)
        (units / "wizard-library-dreaming.timer").unlink()
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual(p.returncode, 0, p.stderr[-1200:])
        self.assertIn("not asked (no terminal) and no timer that thinks is armed", p.stdout)

    def test_e_is_recorded_after_the_run_and_a_recorded_answer_is_not_asked_again(self):
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "off")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        rec = self._record()
        self.assertEqual((rec["backend"], rec["answered_by"], rec["source"]), ("off", "owner-flag", "installer"), rec)
        ir = (self.brain / "INSTALL_RECORD").read_text(encoding="utf-8")
        self.assertIn("thinker_backend=off", ir)
        self.assertIn("Overnight thinker: off (the owner answered e) not now", p.stdout)
        sha = (self.brain / "THINKER_RECORD").read_bytes()
        rc, tty, out, err, left = self._tty([], "--tier0-only", "--force-cpu")     # an upgrade AT A TERMINAL
        self.assertEqual(rc, 0, (tty[-1200:], err[-1200:]))
        self.assertNotIn("Your answer (a, b, c, d, e or f)", tty, "a recorded answer was asked again")
        self.assertIn("is re-used, never re-asked: e) Not now", out)
        self.assertEqual((self.brain / "THINKER_RECORD").read_bytes(), sha, "e) kept as recorded")

    def test_a_claude_answer_is_recorded_only_after_its_proof_in_the_timers_environment(self):
        self._fake("claude", mode="fail", said="401 unauthorized (fake)")
        self._cred("claude")
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "claude")
        self.assertEqual(p.returncode, 1, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("was NOT proven", p.stderr)
        self.assertIsNone(self._record(), "a record was written without a passing proof")
        self.assertFalse((self.brain / "organs").exists(), "files were laid after a failed proof")
        self.assertFalse(self.brain.exists(), "the brain directory made for the proof was left behind")
        self.assertIn("systemd-run --user --wait --pipe --collect", self.mlog.read_text(), "not proven through the manager")
        self._fake("claude")                                            # the same backend, now answering
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "claude")
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        rec = self._record()
        self.assertEqual((rec["backend"], rec["probe_nonce_ok"], rec["probe_tool_block"], rec["source"]),
                         ("claude", "true", "held", "installer"), rec)
        self.assertTrue(rec["probe_env"].startswith("systemd-run --user (EnvironmentFile=-"), rec["probe_env"])
        order = p.stdout.index("Proven and recorded"), p.stdout.index("STEP A")
        self.assertLess(order[0], order[1], "the thinker must be proven BEFORE Step A lays anything")
        n = len(self.mlog.read_text().splitlines())
        rc, tty, out, err, left = self._tty([], "--tier0-only", "--force-cpu")     # re-run: re-used and re-proven
        self.assertEqual(rc, 0, (out[-1200:], err[-1200:]))
        self.assertNotIn("Your answer (a, b, c, d, e or f)", tty)
        self.assertEqual(len(self.mlog.read_text().splitlines()), n + 1, "the recorded answer was not proven again")

    def test_an_answer_at_the_terminal_shows_its_part_waits_for_the_step_then_proves(self):
        """a) at the terminal: the guide's Part C is printed word for word FROM the package's docs, the installer waits
        for the one step (Enter), then `thinker set` proves it in the timers' environment and only then records it."""
        self._fake("claude")
        self._cred("claude")                      # the owner's one step, already done (the paste-twice login is Wave A's)
        guide = (ROOT / "docs" / "OVERNIGHT_THINKER_SETUP.md").read_text(encoding="utf-8")
        part = guide[guide.index("## 6. Part C: Claude (option a)"):]
        part = part[:part.index("\n## ", 5)]
        rc, tty, out, err, left = self._tty([(Q_THINKER, "a"), (r"Press Enter when ready:", "")],
                                            "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        for line in [l for l in part.splitlines() if l.strip()][:6]:
            self.assertIn(line, tty, "the Part is not shown word for word")
        self.assertLess(tty.index("## 6. Part C"), tty.index("Press Enter when ready:"))
        rec = self._record()
        self.assertEqual((rec["backend"], rec["answered_by"], rec["source"]), ("claude", "owner", "installer"), rec)
        self.assertLess(out.index("Proven and recorded"), out.index("STEP A"))

    def test_a_claude_era_upgrade_asks_and_past_usage_is_evidence_not_an_answer(self):
        p = self._run("--init-blank", "--tier0-only", "--force-cpu")          # an install with no thinker answer
        self.assertEqual(p.returncode, 0, p.stderr[-1200:])
        with open(str(self.env_file), "a", encoding="utf-8") as fh:
            fh.write("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-fake-for-the-canary\n")
        units = self.home / ".config" / "systemd" / "user"
        units.mkdir(parents=True, exist_ok=True)
        (units / "wizard-library-dreaming.timer").write_text("[Timer]\nOnCalendar=daily\n", encoding="utf-8")
        rc, tty, out, err, left = self._tty([(Q_THINKER, "e")], "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn(_question()["ask"], tty, "the Claude-era upgrade did not ASK")
        self.assertRegex(tty, r"carries CLAUDE_CODE_OAUTH_TOKEN on line \d+ \(outside the managed block\): your timers "
                              r"could reach Claude with it -- evidence, not an answer")
        self.assertNotIn("sk-ant-oat01", tty + out + err, "a credential value was printed")
        self.assertEqual(self._record()["backend"], "off")
        self.assertIn("note: your Claude token (CLAUDE_CODE_OAUTH_TOKEN) is still kept in", out)   # M13's bounded part
        self.assertIn("run `voidloom thinker login claude` in a terminal and paste the token twice", out)
        self.assertIn("After that the line can go -- delete it yourself; nothing removes it for you.", out)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-fake-for-the-canary", self.env_file.read_text(),
                      "a machine removed the owner's token line")
        (self.brain / "THINKER_RECORD").unlink()
        before = _tree(self.home)
        p = self._run("--tier0-only", "--force-cpu")                          # the same upgrade with no terminal
        self.assertEqual(p.returncode, 2, p.stderr[-1200:])
        self.assertIn("evidence, not an answer", p.stderr)
        self.assertEqual(_tree(self.home), before)


class TestIteration2TheInstallersFindings(_Home):
    """Round 1 iteration 2: MC-R1-05 (an explicitly EMPTY value is refused, never taken as an absent flag), MC-R1-07 (a
    RECORDED answer that no longer proves is reported and the upgrade continues; a NEW answer still refuses) and the
    installer's half of MC-R1-01 (the d) command template is checked by the seam's own check, at the flag and at the
    terminal, before anything is written; the installer never accepts an agentic risk for the owner)."""

    FLAGS = ["--brain-db", "--attach-db", "--tier1-ambient", "--gpu", "--display-gpu", "--thinker", "--thinker-model",
             "--thinker-endpoint", "--thinker-api", "--thinker-command"]

    def test_an_empty_value_is_refused_not_taken_as_no_answer_for_every_flag(self):
        before = _tree(self.home)
        for flag in self.FLAGS:
            p = self._run("--init-blank", "--tier0-only", "--force-cpu", flag, "")
            self.assertEqual(p.returncode, 2, (flag, p.stdout[-400:], p.stderr[-600:]))
            self.assertIn("%s was given an EMPTY value" % flag, p.stderr, flag)
            self.assertIn("Nothing was touched.", p.stderr, flag)
        self.assertEqual(_tree(self.home), before)

    def test_a_recorded_answer_that_no_longer_proves_is_reported_and_the_upgrade_continues(self):
        self._fake("claude")
        self._cred("claude")
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "claude")
        self.assertEqual(p.returncode, 0, (p.stdout[-1200:], p.stderr[-1200:]))
        rec = (self.brain / "THINKER_RECORD").read_bytes()
        self._fake("claude", mode="fail", said="401 unauthorized (fake)")          # the recorded backend now fails
        p = self._run("--tier0-only", "--force-cpu")                                # an upgrade, no flag, no terminal
        self.assertEqual(p.returncode, 0, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("did NOT prove again", p.stderr)
        self.assertIn("The record is kept EXACTLY as it was and this upgrade continues", p.stderr)
        self.assertEqual((self.brain / "THINKER_RECORD").read_bytes(), rec, "the record changed")
        self.assertIn("STEP A", p.stdout, "the upgrade did not continue")
        p = self._run("--tier0-only", "--force-cpu", "--thinker", "claude")              # a NEW answer that fails: refused
        self.assertEqual(p.returncode, 1, (p.stdout[-800:], p.stderr[-800:]))
        self.assertIn("was NOT proven", p.stderr)
        self.assertEqual((self.brain / "THINKER_RECORD").read_bytes(), rec)

    def test_the_flag_template_is_checked_by_the_seam_before_anything_is_written(self):
        before = _tree(self.home)
        for tmpl, want in (("/bin/sh -c {prompt}", "a SHELL"), ("bash -c 'x {prompt}'", "a SHELL"),
                           ("kimi --yolo -p {prompt}", "'--yolo'"), ("env X=1 ollama run {model} {prompt}", "is env"),
                           ("ollama run {model} --p={prompt}", "INSIDE the argument"),
                           ("llama-cli -m {model} -p {prompt}", "never accepts that risk")):
            p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "local",
                          "--thinker-model", "m", "--thinker-command", tmpl)
            self.assertEqual(p.returncode, 2, (tmpl, p.stdout[-400:], p.stderr[-800:]))
            self.assertIn("--thinker-command is refused", p.stderr, tmpl)
            self.assertIn(want, p.stderr, tmpl)
            self.assertIn("Nothing was touched.", p.stderr, tmpl)
        self.assertEqual(_tree(self.home), before)
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "local", "--thinker-model",
                      "m", "--thinker-command", "ollama run {model} {prompt}")
        self.assertEqual(p.returncode, 0, (p.stdout[-600:], p.stderr[-800:]))

    def test_the_terminal_follow_up_asks_the_seam_and_asks_again(self):
        answers = [(Q_THINKER, "f"), (r"which model\?.*:", "tiny"), (r"Answer 1, 2 or 3:", "3"),
                   (r"The command, with \{prompt\} in it:", "/bin/sh -c {prompt}"),
                   (r"The command, with \{prompt\} in it:", "llama-cli -m {model} -p {prompt}"),
                   (r"The command, with \{prompt\} in it:", "ollama run {model} {prompt}")]
        rc, tty, out, err, left = self._tty(answers, "--dry-run", "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-2500:], err[-1200:]))
        self.assertIn("'/bin/sh' is sh, a SHELL", tty)
        self.assertIn("never accepts that risk for you", tty)
        self.assertIn("Your answer: f) Other (which model?) -- tiny.", tty)


class TestTheGateLivesInInstallCrons(_Home):

    def _crons(self, *args, **extra):
        env = self._env(PATH=self.path, WIZARD_LIBRARY_HOME=str(self.brain), WIZARD_LIBRARY_DB=str(self.db),
                        WIZARD_PYTHON_BIN=sys.executable, **extra)
        return subprocess.run(["bash", str(ROOT / "install" / "install_crons.sh")] + list(args), capture_output=True,
                              text=True, env=env, timeout=300, cwd=str(self.tmp))

    def test_no_recorded_answer_refuses_before_writing_and_an_answer_arms(self):
        self.brain.mkdir(parents=True)
        (self.brain / "organs").symlink_to(ROOT / "organs")
        before = _tree(self.home)
        p = self._crons()
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-800:]))
        self.assertIn("REFUSED: dreaming whisper-digest self-eval think overnight", p.stderr)
        self.assertIn(_question()["ask"], p.stderr)
        self.assertIn("it must never choose the answer itself. Nothing was written.", p.stderr)
        self.assertEqual(_tree(self.home), before)
        p = self._crons("--dry-run")                                        # a dry run SAYS it, arms nothing
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("DRY: a wet run would be REFUSED (rc 2)", p.stdout)
        self.assertEqual(_tree(self.home), before)
        p = self._crons("--dry-run", VOIDLOOM_THINKER_SETTLED="off")
        self.assertEqual(p.returncode, 0, p.stderr[-800:])
        self.assertIn("e) not now -- they run their simple nightly routines; nothing thinks overnight.", p.stdout)
        p = self._crons(VOIDLOOM_THINKER_SETTLED="off")                     # honoured ONLY with --dry-run
        self.assertEqual(p.returncode, 2, p.stderr[-800:])
        p = self._crons(WIZARD_CRON_ONLY="sleep-loop")                      # no thinking unit: no gate
        self.assertNotEqual(p.returncode, 2, p.stderr[-800:])


# ================================================================================================ M11
class TestTheAgentFacingInstructions(unittest.TestCase):

    def test_readme_and_onboarding_carry_the_question_word_for_word_and_the_rule(self):
        q = _question()
        for doc in (ROOT / "README.md", ROOT / "docs" / "ONBOARDING_GUIDE.md"):
            t = doc.read_text(encoding="utf-8")
            self.assertIn(q["ask"], t, doc.name)
            for letter, words, _a in q["options"]:
                self.assertIn("%s) %s" % (letter, words), t, (doc.name, letter))
            self.assertIn(q["chat_note"], t, doc.name)
            self.assertIn("--thinker claude|codex|kimi|gemini|off|local", t, doc.name)
            self.assertNotIn("--thinker <a|b|c|d|e>", t, doc.name)
            self.assertRegex(t, r"(?is)installing (?:AI )?agent.{0,200}?word for word", doc.name)
            self.assertRegex(t, r"(?i)never choose", doc.name)


if __name__ == "__main__":
    unittest.main()


# ================================================================================================ the Persona Forge (STEP P)
Q_WIRE = r"Wire the session-start hook now so your first session offers the Persona Forge\? \[Y/n\]"


class TestThePersonaForgeHookQuestion(_Home):
    """STEP P asks, at a terminal, whether to wire the SessionStart hook (the first-run offer rides it) -- ONCE, at the
    run that lays the forge; yes runs `voidloom hooks install --platform claude`; an upgrade that finds the forge
    already laid does not ask again."""

    def _settings_hooks(self):
        p = self.home / ".claude" / "settings.json"
        if not p.exists():
            return []
        d = json.loads(p.read_text(encoding="utf-8"))
        return [h.get("command") for g in d.get("hooks", {}).get("SessionStart", []) for h in g.get("hooks", [])]

    def test_yes_at_the_terminal_wires_the_hook_and_an_upgrade_does_not_ask_again(self):
        env = self._env(**{"PATH": self.path})
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--init-blank", "--tier0-only", "--force-cpu",
                                              "--thinker", "off"], env, [(Q_WIRE, "y")], str(self.tmp),
                                             timeout=TTY_TIMEOUT, ask_wire=True)
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn("Session-start hook wired: yes", out)
        self.assertTrue(any("session_start_inject.py" in (c or "") for c in self._settings_hooks()), self._settings_hooks())
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--tier0-only", "--force-cpu"], env, [], str(self.tmp),
                                             timeout=TTY_TIMEOUT, ask_wire=True)
        self.assertEqual(rc, 0, (tty[-1500:], err[-1500:]))
        self.assertNotIn("Wire the session-start hook now", tty, "the question was asked again on an upgrade")
        self.assertIn("Session-start hook wired: yes (already wired)", out)   # the REAL state, read before asking

    def test_no_at_the_terminal_leaves_settings_json_alone(self):
        env = self._env(**{"PATH": self.path})
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--init-blank", "--tier0-only", "--force-cpu",
                                              "--thinker", "off"], env, [(Q_WIRE, "n")], str(self.tmp),
                                             timeout=TTY_TIMEOUT, ask_wire=True)
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn("Session-start hook wired: no -- your answer", out)
        self.assertFalse((self.home / ".claude" / "settings.json").exists())

