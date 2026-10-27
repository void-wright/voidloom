# -*- coding: utf-8 -*-
"""Round 3 G1, items 1(c) and 3, through install.sh itself: a refusal names ONE cause and the step for it, and only a
credential cause sends the owner to the guide's Part; when a code is already saved, Part C says so -- "a code is already
saved; press Enter to prove it" -- instead of sending the owner to make a new one.

MEASURED (memory #23617): the owner's run 1 refused on a provider STALL, and the installer said "Fix it with
docs/OVERNIGHT_THINKER_SETUP.md Part C" -- the step for a bad credential -- over a VALID token. Each row runs the real
install.sh in a scratch HOME (the installer-questions harness: a recording systemd-run stand-in, a Claude double), so the
words checked are the words a stranger reads. RED on the Round 2 bytes (the blanket "Fix it with ... Part C").
"""
import os

from voidloom.tests.test_installer_questions import _Home, Q_THINKER


class TestTheRefusalNamesItsCause(_Home):

    def _refused(self, said):
        self._fake("claude", mode="fail", said=said)
        self._cred("claude")
        p = self._run("--init-blank", "--tier0-only", "--force-cpu", "--thinker", "claude")
        self.assertEqual(p.returncode, 1, (p.stdout[-1500:], p.stderr[-1500:]))
        self.assertIn("was NOT proven", p.stderr)
        self.assertIsNone(self._record(), "a record was written without a passing proof")
        return p

    def test_a_quota_refusal_names_quota_and_never_sends_the_owner_to_part_c(self):
        p = self._refused("Claude AI usage limit reached (fake)")
        self.assertIn("  cause: quota", p.stdout, p.stdout[-1500:])
        self.assertIn("was NOT proven (cause: quota;", p.stderr, p.stderr[-1500:])
        self.assertIn("nothing in docs/OVERNIGHT_THINKER_SETUP.md needs redoing", p.stderr)
        self.assertNotIn("OVERNIGHT_THINKER_SETUP.md Part C", p.stdout + p.stderr)

    def test_a_network_refusal_names_network_and_never_sends_the_owner_to_part_c(self):
        p = self._refused("API Error: Connection error. (fake: could not connect)")
        self.assertIn("  cause: network", p.stdout, p.stdout[-1500:])
        self.assertNotIn("OVERNIGHT_THINKER_SETUP.md Part C", p.stdout + p.stderr)

    def test_an_auth_refusal_is_the_one_that_sends_the_owner_to_part_c(self):
        p = self._refused("401 unauthorized (fake)")
        self.assertIn("  cause: auth", p.stdout, p.stdout[-1500:])
        self.assertIn("was NOT proven (cause: auth;", p.stderr)
        self.assertIn("Fix the credential with docs/OVERNIGHT_THINKER_SETUP.md Part C", p.stderr, p.stderr[-1500:])


class TestPartCSaysASavedCodeIsSaved(_Home):

    def test_with_a_saved_code_part_c_says_press_enter_to_prove_it(self):
        self._fake("claude")
        self._cred("claude")
        rc, tty, out, err, left = self._tty([(Q_THINKER, "a"), (r"Press Enter when ready:", "")],
                                            "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn(">>> a code is already saved; press Enter to prove it -- nothing to do in this Part", tty)
        self.assertIn("[install] a code is already saved; press Enter to prove it. (To replace it instead", tty)
        self.assertNotIn("Do the one step above", tty, "the owner was told to make a credential that is already saved")
        self.assertLess(tty.index(">>> a code is already saved"), tty.index("Press Enter when ready:"))
        self.assertNotIn("fake-credential-0123456789abcdef", tty + out + err, "a credential value was printed")
        self.assertEqual(self._record()["backend"], "claude")

    def test_r3_07_a_legacy_env_file_token_is_already_saved_and_proven_without_a_paste(self):
        """R3-07, through install.sh: a Claude-era brain whose ONLY code is the older CLAUDE_CODE_OAUTH_TOKEN= line in the
        env file (no claude.env, nothing exported). Part C says the code is already saved; Enter proves THAT line (the
        double answers only to it) and nothing asks for a paste -- the owner's Oct 1 experience, now the guide's words."""
        tok = "sk-ant-oat01-fake-envfile-line-" + "0123456789" * 4
        self._fake("claude", good_key=tok)
        self.env_file.parent.mkdir(parents=True, exist_ok=True)
        self.env_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=%s\n" % tok, encoding="utf-8")
        rc, tty, out, err, left = self._tty([(Q_THINKER, "a"), (r"Press Enter when ready:", "")],
                                            "--init-blank", "--tier0-only", "--force-cpu")
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn(">>> a code is already saved; press Enter to prove it -- nothing to do in this Part", tty)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", tty[tty.index(">>> a code is already saved"):][:400],
                      "the saved code's place (the env file's line) is not named")
        self.assertNotIn("Do the one step above", tty, "the owner was told to make a credential that is already saved")
        self.assertNotIn("Paste", tty + out, "the owner was asked to paste a code that is already saved")
        self.assertNotIn(tok, tty + out + err, "a credential value was printed")
        rec = self._record()
        self.assertEqual((rec["backend"], rec["credential_source"]), ("claude", "legacy"), rec)
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=%s" % tok, self.env_file.read_text(), "the owner's line was touched")
