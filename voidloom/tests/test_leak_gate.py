# -*- coding: utf-8 -*-
"""The leak gate (tools/leak_gate.py + tools/git-hooks/) refuses what it exists to refuse.

Each case is a POSITIVE CONTROL: a planted violation must be refused, and its clean twin
must pass. A gate shown only to pass has not been shown to work. The denylist here is
SYNTHETIC ("zebracorp", "quill hollow") and passed through $VOIDLOOM_DENYLIST, so the
suite never reads, needs or reveals anyone's real list.

Every case builds a throwaway git repository holding a copy of the gate; nothing touches
this checkout's own git state. stdlib + git only; no GPU, no network.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
TERMS = ["zebracorp", "quill hollow", "northern lantern works"]
AT = "@"                                   # fixtures are built at runtime: the shipped text holds no address
NOREPLY = "12345+someone" + AT + "users.noreply.github.com"
PUBLIC_URL = "git" + AT + "github.com:void-wright/voidloom.git"


@unittest.skipUnless(shutil.which("git"), "git not installed")
class LeakGateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl_leakgate_"))
        self.repo = self.tmp / "repo"
        (self.repo / "tools" / "git-hooks").mkdir(parents=True)
        for f in ("leak_gate.py", "pii_gate.py"):
            shutil.copy2(ROOT / "tools" / f, self.repo / "tools" / f)
        for f in ("pre-commit", "pre-push"):
            shutil.copy2(ROOT / "tools" / "git-hooks" / f, self.repo / "tools" / "git-hooks" / f)
        deny = self.tmp / "deny.txt"
        deny.write_text("# synthetic\n" + "\n".join(TERMS) + "\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "VOIDLOOM_DENYLIST"))}
        self.env.update(HOME=str(self.tmp / "home"), VOIDLOOM_DENYLIST=str(deny),
                        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.tmp / "gitconfig"))
        (self.tmp / "home").mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Someone")
        self.git("config", "user.email", NOREPLY)
        self.git("add", "tools")
        self.git("commit", "-q", "-m", "the gate", "--no-verify")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def git(self, *args, check=True, inp=None):
        p = subprocess.run(["git", *args], cwd=self.repo, env=self.env, capture_output=True, text=True, input=inp)
        if check:
            self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def gate(self, *args, env=None):
        return subprocess.run(["python3", str(self.repo / "tools" / "leak_gate.py"), "--no-gitleaks", *args],
                              cwd=self.repo, env=env or self.env, capture_output=True, text=True)

    def stage(self, name, text):
        (self.repo / name).write_text(text)
        self.git("add", name)

    def assertRefused(self, p, check_word):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(check_word, p.stderr)
        for t in TERMS:
            self.assertNotIn(t, (p.stdout + p.stderr).lower(), "the gate printed a denylist term")


class TestStaged(LeakGateCase):
    def test_a_clean_change_passes(self):
        self.stage("a.txt", "an ordinary line about memory recall\n")
        self.assertEqual(self.gate("--staged").returncode, 0, self.gate("--staged").stderr)

    def test_a_denylisted_name_is_refused_whole_word_and_never_printed(self):
        self.stage("a.txt", "we shipped this for ZebraCorp last week\n")
        self.assertRefused(self.gate("--staged"), "denylist#0 (whole word)")

    def test_a_multiword_name_is_refused(self):
        self.stage("a.txt", "notes from the Quill Hollow call\n")
        self.assertRefused(self.gate("--staged"), "denylist#1")

    def test_a_name_split_by_punctuation_is_refused(self):
        """The first public push carried client names as Python tuples -- ("First", "Second") -- that the plain
        matcher read past and only the hashed CI matcher caught."""
        self.stage("a.txt", 'ALLOW = {("Quill", "Hollow"), ("Other", "Pair")}\n')
        self.assertRefused(self.gate("--staged"), "denylist#1")

    def test_the_derived_forms_of_a_name_are_refused(self):
        """A three-word name leaks as its acronym, its words run together, or its leading two words."""
        for text in ("the NLW account", "see northernlanternworks for details", "Northern Lantern called"):
            self.stage("a.txt", text + "\n")
            self.assertRefused(self.gate("--staged"), "denylist#")

    def test_a_name_inside_a_word_is_refused_as_a_substring(self):
        self.stage("a.txt", "megazebracorpx\n")
        self.assertRefused(self.gate("--staged"), "(substring)")

    def test_a_real_home_path_is_refused_and_a_placeholder_passes(self):
        self.stage("a.txt", "see /home/you/.claude and /Users/someone/x\n")
        self.assertEqual(self.gate("--staged").returncode, 0)
        self.stage("b.txt", "open " + "/home/" + "realperson" + "/.claude/memory\n")  # built at runtime: the tree ships no real-looking home path
        self.assertRefused(self.gate("--staged"), "personal path")

    def test_an_email_is_refused(self):
        self.stage("a.txt", "write to jane.doe" + AT + "zz-example-co.com for access\n")
        self.assertRefused(self.gate("--staged"), "pii")

    def test_a_denylisted_identity_is_refused(self):
        self.git("config", "user.email", "me" + AT + "zebracorp.ai")
        self.stage("a.txt", "fine\n")
        self.assertRefused(self.gate("--staged"), "identity")

    def test_strict_mode_refuses_any_non_noreply_identity(self):
        self.git("config", "user.email", "someone" + AT + "personal-mail.example")
        self.stage("a.txt", "fine\n")
        self.assertEqual(self.gate("--staged").returncode, 0, "lenient by default")
        self.git("config", "voidloom.requireNoreply", "true")
        self.assertRefused(self.gate("--staged"), "noreply")

    def test_the_pre_commit_hook_blocks_the_commit(self):
        shutil.copy2(self.repo / "tools" / "git-hooks" / "pre-commit", self.repo / ".git" / "hooks" / "pre-commit")
        self.stage("a.txt", "zebracorp\n")
        p = self.git("commit", "-q", "-m", "x", check=False)
        self.assertNotEqual(p.returncode, 0, "the hook let a denylisted name through")
        self.assertEqual(self.git("rev-list", "--count", "HEAD").stdout.strip(), "1")


class TestRangeAndPush(LeakGateCase):
    def commit(self, text, email=NOREPLY, msg="change"):
        self.stage("f.txt", text)
        self.git("-c", f"user.email={email}", "commit", "-q", "-m", msg, "--no-verify")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def test_a_range_checks_identity_message_and_lines(self):
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.commit("clean\n")
        self.assertEqual(self.gate("--range", f"{base}..HEAD").returncode, 0)
        self.commit("clean again\n", email="dev" + AT + "zebracorp.com")
        self.assertRefused(self.gate("--range", f"{base}..HEAD"), "identity")

    def test_a_commit_message_is_checked(self):
        base = self.git("rev-parse", "HEAD").stdout.strip()
        self.commit("clean\n", msg="fix for the zebracorp rollout")
        self.assertRefused(self.gate("--range", f"{base}..HEAD"), "message")

    def test_the_hashed_denylist_works_without_the_plain_list(self):
        env = dict(self.env, VOIDLOOM_DENYLIST=str(self.tmp / "absent.txt"),
                   VOIDLOOM_DENYLIST_SHA256=",".join(hashlib.sha256(t.encode()).hexdigest() for t in TERMS))
        self.stage("a.txt", "Notes: Quill Hollow, again.\n")
        p = self.gate("--staged", env=env)
        self.assertRefused(p, "hashed")

    def test_require_denylist_is_an_error_when_none_is_configured(self):
        env = dict(self.env, VOIDLOOM_DENYLIST=str(self.tmp / "absent.txt"))
        self.assertEqual(self.gate("--tree", "--require-denylist", env=env).returncode, 2)

    def _push(self, ref, url):
        sha = self.git("rev-parse", "HEAD").stdout.strip()
        stdin = f"refs/heads/x {sha} {ref} {'0' * 40}\n"
        return subprocess.run(["bash", str(self.repo / "tools" / "git-hooks" / "pre-push"), "origin", url],
                              cwd=self.repo, env=self.env, input=stdin, capture_output=True, text=True)

    def test_pre_push_allows_only_main_and_version_tags_to_the_public_repo(self):
        public = PUBLIC_URL
        self.assertEqual(self._push("refs/heads/main", public).returncode, 0)
        p = self._push("refs/heads/some-old-branch", public)
        self.assertEqual(p.returncode, 1)
        self.assertIn("only main and v* tags", p.stderr)
        self.assertEqual(self._push("refs/heads/some-old-branch", "/tmp/elsewhere.git").returncode, 0)

    def test_pre_push_refuses_a_leaking_commit(self):
        self.commit("we did this for ZEBRACORP\n")
        p = self._push("refs/heads/main", PUBLIC_URL)
        self.assertEqual(p.returncode, 1, p.stderr)


class TestThisTree(unittest.TestCase):
    def test_this_checkout_is_clean_apart_from_the_private_list(self):
        """The shipped tree passes the gate's own checks (paths, PII). The private-list check runs where the list
        exists (the maintainer's machine, and CI with the hashed list)."""
        if not (ROOT / ".git").exists():
            self.skipTest("not a checkout")
        p = subprocess.run(["python3", str(ROOT / "tools" / "leak_gate.py"), "--tree", "--no-gitleaks"],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
