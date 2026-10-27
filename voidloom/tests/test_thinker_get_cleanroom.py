# -*- coding: utf-8 -*-
"""A THINKER PROGRAM THAT IS NOT INSTALLED, in a clean room (Gemini-thinker Round 1, lane L-I; SYNTHESIS R4, DESSA's
clean-room design, KAEL-EM's screens).

The room: a scratch HOME, and PATH = <stubs>:/usr/bin:/bin, where <stubs> holds ONLY a recording `curl` (it serves a
FAKE installer for the vendor URLs and records every argv), the recording `systemd-run` shim and a failing nvidia-smi.
The stubs come FIRST so the recording curl stands in for /usr/bin/curl -- nothing is downloaded, no vendor installer
runs. "Not installed" is ASSERTED, never assumed: `command -v agy claude codex kimi` must fail in the room, else the
test SKIPS LOUDLY (it never passes on a machine where the program is present).

  decline        at the terminal: the vendor's command shown word for word, N goes back to the letter, curl never ran;
                 a flag answer at a terminal that says N is refused rc 2, cause: not-installed, the home unchanged
  no terminal    refused rc 2 BEFORE anything is touched, the exact command and `cause: not-installed` in the refusal
  opt-in         --install-thinker-cli: the catalog's exact one-liner ran (curl's argv), the program is found again by
                 FULL path through binary.candidates (it is not on PATH), proven and RECORDED by that path; a later
                 refusal names the kept program -- never "Nothing was touched"
  candidate-only a program present only at a catalog candidate is found there; nothing is offered
  old letters    --thinker d / f print the one-release note; an old scripted d) Other fails CLOSED
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from voidloom.tests.test_installer_questions import _Home, _tree, SH, ROOT, Q_THINKER, TTY_TIMEOUT
from voidloom.tests.test_tier1_ambient import _drive_tty

sys.path.insert(0, str(ROOT / "organs"))
import thinker  # noqa: E402

PROGRAMS = ("agy", "claude", "codex", "kimi")
Q_RUN = r"Run it now\? \[y/N\]"

CURL_STUB = r'''#!%(py)s
# the RECORDING curl: logs its argv, and for a vendor URL prints a FAKE installer that copies a fake program into
# the program's own folder under $HOME (the folder the vendor's real installer uses)
import json, sys
open(%(log)r, "a").write(json.dumps(sys.argv[1:]) + "\n")
served = json.load(open(%(served)r))
for a in sys.argv[1:]:
    if a in served:
        sys.stdout.write(served[a])
        sys.exit(0)
sys.stderr.write("curl (test stub): no fake for %%r\n" %% (sys.argv[1:],))
sys.exit(22)
'''


def _installer_for(dest_rel, fake_src):
    """A fake vendor installer: bash that puts the prepared fake program (and its behaviour file) at $HOME/<dest_rel>."""
    return ('set -e\nmkdir -p "$HOME/%s"\ncp "%s" "$HOME/%s/"\ncp "%s.json" "$HOME/%s/"\necho "fake installer: done"\n'
            % (os.path.dirname(dest_rel), fake_src, os.path.dirname(dest_rel), fake_src, os.path.dirname(dest_rel)))


class _CleanRoom(_Home):

    def setUp(self):
        super().setUp()
        self.curl_log = self.tmp / "curl.log"
        self.served = {}
        self.vendor = self.tmp / "vendor"          # prepared fake programs: NOT on PATH, copied by a fake installer
        self._write_curl()
        nogpu = self.tmp / "no_gpu"
        self.path = "%s:%s:/usr/bin:/bin" % (self.bin, nogpu)
        env = self._env(PATH=self.path)
        present = [p for p in PROGRAMS
                   if subprocess.run(["/bin/sh", "-c", "command -v %s" % p], env=env, capture_output=True).returncode == 0]
        if present:
            self.skipTest("SKIP (loud): the clean room is not clean -- %s found on PATH=%s; this test never passes "
                          "where the program is present" % (present, self.path))
        for c in ("~/.local/bin/agy", "~/.local/bin/claude", "~/.local/bin/codex", "~/.kimi-code/bin/kimi"):
            self.assertFalse((self.home / c[2:]).exists(), c)

    def _write_curl(self):
        (self.tmp / "served.json").write_text(json.dumps(self.served), encoding="utf-8")
        (self.bin / "curl").write_text(CURL_STUB % {"py": sys.executable, "log": str(self.curl_log),
                                                    "served": str(self.tmp / "served.json")}, encoding="utf-8")
        os.chmod(str(self.bin / "curl"), 0o755)

    def _serve(self, bid, dest_rel, **behaviour):
        """The catalog's URL for bid serves a fake installer that lays a fake `bid` program at $HOME/<dest_rel>."""
        sys.path.insert(0, str(ROOT / "voidloom" / "tests"))
        try:
            import _thinker_fakes as F
        finally:
            sys.path.pop(0)
        src = F.make_fake(self.vendor, os.path.basename(dest_rel), kind=bid, log=str(self.tmp / "fake.log"), **behaviour)
        url = [w for w in thinker.load_catalog()["backends"][bid]["get"]["unix"].split() if w.startswith("https://")][0]
        self.served[url] = _installer_for(dest_rel, src)
        self._write_curl()
        return url

    def _curl_calls(self):
        try:
            return [json.loads(l) for l in self.curl_log.read_text().splitlines() if l.strip()]
        except OSError:
            return []

    def _command(self, bid):
        return thinker.load_catalog()["backends"][bid]["get"]["unix"]


class TestNoTerminal(_CleanRoom):

    def test_a_missing_program_is_refused_before_anything_is_touched_with_the_command(self):
        before = _tree(self.home)
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "kimi")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("    %s\n" % self._command("kimi"), p.stderr, "the vendor's command, word for word")
        self.assertIn("\n  cause: not-installed", p.stderr)
        self.assertIn("Pass --install-thinker-cli", p.stderr)
        self.assertIn("Nothing was touched.", p.stderr)
        self.assertEqual(_tree(self.home), before)
        self.assertEqual(self._curl_calls(), [], "nothing ran from the internet without a yes")

    def test_a_dry_run_says_a_wet_run_would_refuse(self):
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "claude")
        self.assertEqual(p.returncode, 0, p.stderr[-1200:])
        self.assertIn("DRY: Claude's program is not installed: a wet run with no terminal is REFUSED (rc 2) unless "
                      "--install-thinker-cli is passed (the owner's yes to running: %s)." % self._command("claude"), p.stdout)
        self.assertEqual(self._curl_calls(), [])

    def test_opt_in_runs_the_catalogs_one_liner_finds_the_program_by_candidate_and_records_that_path(self):
        url = self._serve("kimi", ".kimi-code/bin/kimi")
        self._cred("kimi")
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "kimi", "--install-thinker-cli")
        self.assertEqual(p.returncode, 0, (p.stdout[-2000:], p.stderr[-2000:]))
        self.assertEqual(self._curl_calls(), [["-fsSL", url]], "exactly the catalog's command ran")
        kimi = str(self.home / ".kimi-code" / "bin" / "kimi")
        self.assertIn("running Moonshot's installer (your yes): %s" % self._command("kimi"), p.stderr)
        self.assertIn("installed: %s -- used by full path from here on" % kimi, p.stdout)
        self.assertEqual(self._record()["binary"], kimi, "recorded by the path the candidate search found")
        self.assertLess(p.stdout.index("installed: "), p.stdout.index("Proven and recorded"))

    def test_opt_in_then_a_refusal_names_the_kept_program_never_nothing_touched(self):
        self._serve("kimi", ".kimi-code/bin/kimi")              # installed, but no credential and no terminal
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "kimi", "--install-thinker-cli")
        self.assertNotEqual(p.returncode, 0, p.stdout[-1500:])
        kimi = str(self.home / ".kimi-code" / "bin" / "kimi")
        self.assertIn("(The Kimi program WAS installed at your yes, at %s, and is kept.)" % kimi, p.stderr)
        self.assertNotIn("Nothing was touched", p.stderr)
        self.assertTrue(Path(kimi).exists(), "the kept program is kept")
        self.assertIsNone(self._record())

    def test_an_installer_that_leaves_no_program_is_refused_and_says_so(self):
        url = self._serve("claude", ".local/bin/claude")
        self.served[url] = "echo 'fake installer that installs nothing'\n"
        self._write_curl()
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "claude", "--install-thinker-cli")
        self.assertEqual(p.returncode, 2, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("is still not found", p.stderr)
        self.assertIn("\n  cause: not-installed", p.stderr)
        self.assertIsNone(self._record())

    def test_a_need_this_installer_never_installs_stops_with_its_link(self):
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "codex", "--install-thinker-cli")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("Node.js (https://nodejs.org/en/download)", p.stderr)
        self.assertIn("never installs for you", p.stderr)
        self.assertEqual(self._curl_calls(), [])

    def test_a_program_present_only_at_a_candidate_is_found_and_nothing_is_offered(self):
        sys.path.insert(0, str(ROOT / "voidloom" / "tests"))
        try:
            import _thinker_fakes as F
        finally:
            sys.path.pop(0)
        F.make_fake(self.home / ".kimi-code" / "bin", "kimi", log=str(self.tmp / "fake.log"))
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "kimi")
        self.assertEqual(p.returncode, 0, p.stderr[-1200:])
        self.assertIn("Kimi's program: found at %s" % (self.home / ".kimi-code" / "bin" / "kimi"), p.stdout)
        self.assertNotIn("not installed", p.stdout)

    def test_old_letters_print_the_one_release_note_and_an_old_d_other_fails_closed(self):
        q = thinker.load_catalog()["question"]
        local = ["--thinker-model", "tiny", "--thinker-endpoint", "http://127.0.0.1:9", "--thinker-api", "ollama"]
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "f", *local)
        self.assertEqual(p.returncode, 0, p.stderr[-1200:])
        self.assertIn("[install] " + q["letter_notes"]["f"], p.stderr)
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "local", *local)
        self.assertEqual(p.returncode, 0, p.stderr[-1200:])
        self.assertNotIn("note: --thinker", p.stderr, "a NAME carries no note")
        p = self._run("--dry-run", "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "d")
        self.assertIn("[install] " + q["letter_notes"]["d"], p.stderr)
        before = _tree(self.home)
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "d", *local)
        self.assertEqual(p.returncode, 2, p.stderr[-1200:])
        self.assertIn("belong to f) Other (--thinker local)", p.stderr)
        self.assertEqual(_tree(self.home), before)


class TestAtTheTerminal(_CleanRoom):

    def test_n_at_the_question_shows_the_command_and_goes_back_to_the_letter(self):
        cmd = self._command("kimi")
        rc, tty, out, err, left = self._tty([(Q_THINKER, "c"), (Q_RUN, "maybe"), (Q_RUN, ""), (Q_THINKER, "e")],
                                            "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-2500:], err[-1500:]))
        self.assertIn("Kimi needs Moonshot's Kimi Code program; it is not on this computer.", tty)
        self.assertIn("    %s" % cmd, tty)
        self.assertIn("No password, no admin rights.", tty)
        self.assertIn("'maybe' is not y or n (a blank line is n).", tty)
        self.assertIn("Pick another letter (or install it yourself and re-run).", tty)
        self.assertLess(tty.index(cmd), tty.rindex("Your answer (a, b, c, d, e or f):"))
        self.assertEqual(self._record()["backend"], "off")
        self.assertEqual(self._curl_calls(), [], "N ran nothing")

    def test_n_to_a_flag_answer_at_a_terminal_is_refused_touching_nothing(self):
        before = _tree(self.home)
        rc, tty, out, err, left = self._tty([(Q_RUN, "n")], "--init-blank", "--tier0-only", "--force-cpu",
                                            "--thinker", "claude")
        self.assertEqual((rc, left), (2, []), (tty[-1500:], err[-1500:]))
        self.assertIn("and your answer was N", err)
        self.assertIn(self._command("claude"), err)
        self.assertIn("\n  cause: not-installed", err)
        self.assertIn("Nothing was touched.", err)
        self.assertEqual(_tree(self.home), before)

    def test_y_runs_it_before_the_part_and_the_proof_uses_the_full_path(self):
        url = self._serve("kimi", ".kimi-code/bin/kimi")
        self._cred("kimi")
        rc, tty, out, err, left = self._tty([(Q_THINKER, "c"), (Q_RUN, "y"), (r"Press Enter when ready:", "")],
                                            "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-2500:], err[-2500:]))
        self.assertEqual(self._curl_calls(), [["-fsSL", url]])
        kimi = str(self.home / ".kimi-code" / "bin" / "kimi")
        self.assertLess(out.index("installed: %s" % kimi), out.index("Proven and recorded"))
        self.assertLess(tty.index("Run it now?"), tty.index("Part A"), "offered BEFORE the guide's Part")
        self.assertEqual(self._record()["binary"], kimi)


if __name__ == "__main__":
    unittest.main()
