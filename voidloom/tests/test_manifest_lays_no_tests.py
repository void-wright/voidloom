# -*- coding: utf-8 -*-
"""The lay manifest keeps the test suite out of a user's brain directory, and says so; the inventory's own row claims
nothing it cannot keep.

The suite is a CHECKOUT surface: it derives its population from `git ls-files`, refuses or skips outside a checkout,
and its gate files carry the needles they gate. So it ships in the repository and is not laid. A declared exclusion
differs from a silent one by being named, given a reason and counted in the manifest's `_meta.not_laid`.

The derivation is exercised directly (`lay_rule`, `lay_manifest_doc`), and through a real `--package` emission in a
scratch clone, so a claim about the emitted bytes is read from emitted bytes.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import build_manifest as bm  # noqa: E402


class TheSuiteIsNotLaid(unittest.TestCase):

    def test_a_test_file_has_no_root(self):
        for src in ("voidloom/tests/test_lay_manifest.py", "voidloom/tests/harness/outside_target_diff.py",
                    "voidloom/tests/fixtures/x.json", "voidloom/tests/__init__.py"):
            self.assertIsNone(bm.lay_rule(src)[0], src)

    def test_control_the_package_beside_it_is_still_laid(self):
        for src in ("voidloom/cli.py", "voidloom/__main__.py", "voidloom/voidloom", "organs/claude_memory.py"):
            self.assertEqual(bm.lay_rule(src)[0], "brain", src)

    def test_a_path_that_only_starts_alike_is_not_excluded(self):
        self.assertEqual(bm.lay_rule("voidloom/tests_helper.py")[0], "brain")

    def test_every_prefix_has_a_reason_a_person_can_read(self):
        self.assertTrue(bm.NOT_LAID)
        for prefix, why in bm.NOT_LAID.items():
            self.assertTrue(prefix.endswith("/"), prefix)
            self.assertGreater(len(why.split()), 8, prefix)


class TheManifestDeclaresWhatItLeavesOut(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.entries, refusals, cls.head = bm.derive_lay_entries(ROOT)
        if cls.entries is None:
            raise unittest.SkipTest("not a checkout: %s" % refusals)
        cls.meta = bm.lay_manifest_doc(cls.entries, cls.head)["_meta"]

    def test_not_laid_is_present_and_counted(self):
        nl = self.meta["not_laid"]
        self.assertEqual(nl["total"], sum(1 for e in self.entries if e["root"] is None))
        self.assertEqual(nl["total"], len(nl["front_door"]) + sum(d["count"] for d in nl["declared"].values()))

    def test_the_declared_count_is_the_tracked_count(self):
        tracked = subprocess.run(["git", "ls-files", "voidloom/tests/"], cwd=str(ROOT), capture_output=True, text=True)
        self.assertEqual(tracked.returncode, 0)
        n = len(tracked.stdout.splitlines())
        self.assertGreater(n, 100)
        self.assertEqual(self.meta["not_laid"]["declared"]["voidloom/tests/"]["count"], n)

    def test_nothing_laid_comes_from_the_suite(self):
        laid = [e["src"] for e in self.entries if e["root"] is not None and e["src"].startswith("voidloom/tests/")]
        self.assertEqual(laid, [])

    def test_the_suite_still_ships(self):
        self.assertTrue([e for e in self.entries if e["src"].startswith("voidloom/tests/")],
                        "the exclusion dropped the files from the manifest instead of marking them not laid")


class TheInventorysOwnRow(unittest.TestCase):
    """Emitted in a scratch clone: the checkout under test is never rewritten."""

    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.mkdtemp(dir=str(Path.home() / ".cache"))
        cls.addClassCleanup(shutil.rmtree, cls.td, True)
        dst = Path(cls.td) / "t"
        r = subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(dst)], capture_output=True, text=True)
        if r.returncode != 0:
            raise unittest.SkipTest("cannot clone: %s" % r.stderr[-200:])
        shutil.copy(str(ROOT / "tools" / "build_manifest.py"), str(dst / "tools" / "build_manifest.py"))
        r = subprocess.run([sys.executable, "tools/build_manifest.py", "--package"], cwd=str(dst),
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-400:]
        cls.tree = dst
        cls.text = (dst / "docs" / "PACKAGE_MANIFEST.md").read_text(encoding="utf-8")

    def test_the_row_says_it_is_itself_and_prints_no_digest(self):
        self.assertRegex(self.text, r"\| `docs/PACKAGE_MANIFEST\.md` \| \d+ \| \(self -- not pinnable\) \|")

    def test_every_digest_printed_verifies_against_the_file(self):
        rows = re.findall(r"^\| `([^`]+)` \| \d+ \| `([0-9a-f]{16})` \|$", self.text, re.M)
        self.assertGreater(len(rows), 300)
        wrong = [rel for rel, h in rows
                 if hashlib.sha256((self.tree / rel).read_bytes()).hexdigest()[:16] != h]
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
