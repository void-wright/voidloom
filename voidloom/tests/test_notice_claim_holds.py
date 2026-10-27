"""NOTICE makes a checkable claim about the tree. This re-derives it.

Until 2026-09-17 NOTICE said "No organisation's name, infrastructure,
credentials, or data appear in it" -- and 17 tracked files carried
organisation names: a config directory named for the author's organisation
as a bare default in three cron scripts, two launchd label prefixes under its
reverse-DNS, five client companies and two client products as the entity
extractor's test fixtures, and three organs whose defaults were paths on this
one workstation. A claim nobody checked, in the file a recipient reads to
learn what they are receiving.

Two layers:

  STRUCTURAL (always runs, names nothing): the shell entry points assign no
  ``${HOME}/.<dir>`` config root outside the product's own directories; NOTICE
  names this test so the binding is visible in the artifact.

  HOST-BOUND (runs when ~/.voidloom/org_denylist.txt exists, else SKIPs with
  the reason): no name on the list appears in ANY tracked file -- code,
  comments and docs alike, because the tree is public. The list is personal and
  lives outside the repository on purpose -- shipping it would ship the names.

Red at parent: with the denylist present, the pre-repair tree fails the
host-bound layer on 10 files.

Both layers bind the CHECKOUT (the git index, and install.sh / NOTICE, which
are root=null: they ship, they are not laid). On an installed brain they SKIP
with the reason (UNMEASURABLE, not PASS) -- _require_checkout().
"""
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
DENYLIST = Path.home() / ".voidloom" / "org_denylist.txt"


def _require_checkout():
    """These tests bind the CHECKOUT (git index + the null-root front-door files). On an installed
    brain neither exists; a skip with the reason is the honest verdict (UNMEASURABLE, not PASS)."""
    if not (ROOT / "install.sh").is_file() or not (ROOT / ".git").exists():
        raise unittest.SkipTest("NOT A CHECKOUT: %s has no install.sh/.git (install.sh is root=null: ships, not laid). "
                                "Run from the package checkout or set VOIDLOOM_ROOT=<checkout>." % ROOT)


def _tracked_non_doc():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    for rel in out.stdout.split():
        if rel.startswith("docs/") or rel.endswith(".md"):
            continue
        yield rel


def _tracked_all():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    yield from out.stdout.split()


def _load_denylist():
    names = []
    for ln in DENYLIST.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            names.append(ln.lower())
    return names


_COMMENT = re.compile(r'^\s*#')
# A line that ASSIGNS a config root: CONFIG_*, ENV_FILE*, *_DIR, LOG_DIR ...
# A python-interpreter probe list (${HOME}/.pyenv/...) is not a config root.
_CONFIG_ASSIGN = re.compile(r'^\s*[A-Z_]*(CONFIG|ENV_FILE|_DIR)[A-Z_]*=')
_HOME_DOT_LITERAL = re.compile(r'\$\{HOME\}/\.[A-Za-z]')
_ALLOWED_ROOTS = ("${HOME}/.voidloom", "${HOME}/.config/", "${HOME}/.local/",
                  "${HOME}/.claude", "${HOME}/.cache")


class TestStructural(unittest.TestCase):
    """Names nothing; runs on any checkout."""

    def setUp(self):
        _require_checkout()

    def test_shell_entry_points_assign_no_foreign_config_root(self):
        offenders = []
        for rel in ("install.sh", "cron/_config_dir.sh", "cron/platform-detection.sh",
                    "cron/setup-sleep-loop-cron.sh", "cron/setup-dreaming-cron.sh"):
            for n, ln in enumerate((ROOT / rel).read_text().splitlines(), 1):
                if _COMMENT.match(ln):
                    continue
                if not _CONFIG_ASSIGN.match(ln):
                    continue
                if _HOME_DOT_LITERAL.search(ln) and not any(a in ln for a in _ALLOWED_ROOTS):
                    offenders.append(f"{rel}:{n}: {ln.strip()[:80]}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_notice_names_this_test(self):
        text = (ROOT / "NOTICE").read_text()
        self.assertIn("test_notice_claim_holds.py", text,
                      "NOTICE no longer names the test that re-derives its attribution claim")

    def test_notice_no_longer_makes_the_absolute_claim(self):
        # Whitespace-normalised: the parent's sentence wrapped across a line
        # break, and a single-line assertNotIn PASSED against it -- a check
        # that passes at parent is vacuous. (Caught by running it at parent.)
        text = " ".join((ROOT / "NOTICE").read_text().split())
        self.assertNotIn("No organisation's name, infrastructure, credentials, or data appear in it",
                         text, "the absolute sentence is back; it was false against 17 files")


@unittest.skipUnless(DENYLIST.exists(), f"host-bound: {DENYLIST} absent")
class TestHostBound(unittest.TestCase):
    """Runs only where the personal denylist exists."""

    def setUp(self):
        _require_checkout()

    def test_no_listed_organisation_anywhere_in_the_tree(self):
        names = _load_denylist()
        self.assertGreater(len(names), 0, "denylist present but empty -- UNKNOWN, not zero")
        hits = []
        for rel in _tracked_all():
            try:
                lines = (ROOT / rel).read_text(errors="replace").splitlines()
            except OSError:
                continue
            for n, ln in enumerate(lines, 1):
                low = ln.lower()
                flat = " " + re.sub(r"[^a-z0-9]+", " ", low) + " "   # a name split by punctuation is still the name
                if not any(nm in low or (" " + re.sub(r"[^a-z0-9]+", " ", nm).strip() + " ") in flat for nm in names):
                    continue
                if "tensorflow" in low and not any(nm in low.replace("tensorflow", "") for nm in names):
                    continue  # "tensorflow": a known substring collision, not a name
                if rel == "voidloom/tests/test_notice_claim_holds.py":
                    continue
                hits.append(f"{rel}:{n}: {ln.strip()[:90]}")
        self.assertEqual(hits, [], "organisation names in the tree:\n" + "\n".join(hits))

    def test_population_is_nonempty(self):
        """UNKNOWN, NOT ZERO: a clean result over no files is not clean."""
        n = sum(1 for _ in _tracked_non_doc())
        self.assertGreater(n, 100, f"only {n} non-doc tracked files enumerated; is this a checkout?")


if __name__ == "__main__":
    unittest.main()
