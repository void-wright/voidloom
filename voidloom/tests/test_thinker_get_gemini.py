# -*- coding: utf-8 -*-
"""d) GEMINI WITH NO agy ON THE MACHINE -- the friend's path (Gemini-thinker Round 1, lane L-I; KAEL-EM screens 1 + 2;
the owner's rulings of 2026-10-16: agy only, and the sign-in runs through `voidloom thinker login gemini`).

The same clean room as test_thinker_get_cleanroom.py (a recording curl serves a FAKE installer; `command -v agy` must
fail or the test SKIPS loudly). These need lane L-G's backends.gemini with its `get` field (L-G cdbd621): before the merge
they SKIP, loudly.

  no terminal   refused rc 2 before anything is touched, Google's command word for word, cause: not-installed
  opt-in        --install-thinker-cli runs exactly Google's one-liner, finds agy at ~/.local/bin/agy (not on PATH),
                and the refusal that follows (no sign-in, no terminal) names the KEPT program
  terminal      d -> Google's command shown -> y -> installed BEFORE the Part; KAEL-EM's sign-in screen (its own home,
                "your everyday Gemini sign-in is not touched", the training line, the pinned model) BEFORE Part E;
                end of input at the step names the kept program -- never "Nothing was touched"
"""
import sys
import unittest

from voidloom.tests.test_installer_questions import _tree, Q_THINKER
from voidloom.tests.test_thinker_get_cleanroom import _CleanRoom, Q_RUN, ROOT

sys.path.insert(0, str(ROOT / "organs"))
import thinker  # noqa: E402

GOOGLE = "curl -fsSL https://antigravity.google/cli/install.sh | bash"


class _Gemini(_CleanRoom):
    def setUp(self):
        e = thinker.load_catalog()["backends"].get("gemini")
        if not e or not e.get("get"):
            self.skipTest("SKIP (loud): backends.gemini (lane L-G) with its `get` field is not in this catalog -- "
                          "runs after the L-G + L-I merge")
        if thinker.platform_refusal(e):
            self.skipTest("SKIP (loud): Gemini is refused on this OS (%s)" % sys.platform)
        bw, why = thinker._bwrap_ready()
        if not bw:   # the installer refuses Gemini here BY DESIGN; these cases test the path where it can sandbox
            self.skipTest("SKIP (loud): bubblewrap cannot make a sandbox on this machine (%s)" % why)
        super().setUp()
        self.assertEqual(e["get"]["unix"], GOOGLE)
        self.agy = str(self.home / ".local" / "bin" / "agy")


class TestGeminiWithoutAgy(_Gemini):

    def test_no_terminal_is_refused_with_googles_command(self):
        before = _tree(self.home)
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "gemini")
        self.assertEqual(p.returncode, 2, (p.stdout[-800:], p.stderr[-1500:]))
        self.assertIn("d) Gemini (Google; runs through Google's Antigravity program) needs Google's program", p.stderr)
        self.assertIn("    %s\n" % GOOGLE, p.stderr)
        self.assertIn("\n  cause: not-installed", p.stderr)
        self.assertEqual(_tree(self.home), before)
        self.assertEqual(self._curl_calls(), [])

    def test_opt_in_installs_agy_and_the_sign_in_refusal_names_the_kept_program(self):
        url = self._serve("gemini", ".local/bin/agy")
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "gemini", "--install-thinker-cli")
        self.assertNotEqual(p.returncode, 0, p.stdout[-1500:])
        self.assertEqual(self._curl_calls(), [["-fsSL", url]], "exactly Google's one-liner")
        self.assertIn("installed: %s -- used by full path from here on" % self.agy, p.stdout)
        self.assertIn("voidloom thinker login gemini", p.stdout + p.stderr, "the one sign-in path is named")
        self.assertIn("(The Gemini program WAS installed at your yes, at %s, and is kept.)" % self.agy, p.stderr)
        self.assertNotIn("Nothing was touched", p.stderr)
        self.assertIsNone(self._record())

    def test_at_the_terminal_the_offer_then_the_sign_in_screen_then_part_e(self):
        self._serve("gemini", ".local/bin/agy")
        rc, tty, out, err, left = self._tty([(Q_THINKER, "d"), (Q_RUN, "y"), (r"Press Enter when ready:", "\x04")],
                                            "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual(left, [], (tty[-2500:], err[-1500:]))
        self.assertEqual(rc, 2, (tty[-2500:], err[-1500:]))
        self.assertIn("Gemini needs Google's Antigravity program; it is not on this computer.", tty)
        self.assertIn("    " + GOOGLE, tty)
        pin = thinker.load_catalog()["backends"]["gemini"]["model"]["pin"]
        order = [tty.index(x) for x in ("Run it now?", "Found Antigravity at %s." % self.agy,
                                        "Your everyday Gemini sign-in is not touched.",
                                        "Overnight model: %s." % pin,
                                        "Google may use these conversations to improve its products.",
                                        "OVERNIGHT_THINKER_SETUP.md, Part E (the one step", "Press Enter when ready:")]
        self.assertEqual(order, sorted(order), "offer -> install -> sign-in screen -> Part E -> the step")
        self.assertIn("installed: %s" % self.agy, out)
        self.assertIn("the one change this run made is the Gemini program it installed at your yes (%s), which is kept"
                      % self.agy, err)
        self.assertIsNone(self._record())


if __name__ == "__main__":
    unittest.main()
