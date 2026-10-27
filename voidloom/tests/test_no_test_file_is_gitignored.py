"""A test file the tree carries must SHIP (model-choice round 1, found by the Wave C landing rehearsal).

`.gitignore`'s secret patterns (`*credentials*`, `*token*.json`, ...) matched Wave A's
voidloom/tests/test_thinker_credentials.py: the file ran in the build tree (12 tests, M6's proof) and was never staged,
so the artifacts and a fresh clone did not carry it -- the landing rehearsal collected 12 tests fewer than the build
tree. It is renamed (test_thinker_credential_resolver.py) rather than un-ignored: the secret patterns stay whole.

This binds the class: in a checkout, no `voidloom/tests/test_*.py` on disk may be git-IGNORED. (Untracked-but-not-
ignored is a file on its way in and is not judged here; an IGNORED test file can never arrive.)
"""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()


class TestNoTestFileIsIgnored(unittest.TestCase):

    def test_every_test_file_on_disk_can_be_committed(self):
        if not (ROOT / ".git").exists():
            self.skipTest("NOT A CHECKOUT: %s has no .git -- the ignore rules cannot be asked (UNMEASURABLE)" % ROOT)
        on_disk = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "voidloom" / "tests").glob("test_*.py"))
        self.assertGreater(len(on_disk), 10, "the population is empty -- this check would pass vacuously")
        p = subprocess.run(["git", "check-ignore", "--no-index"] + on_disk, cwd=str(ROOT), capture_output=True,
                           text=True, timeout=60)
        ignored = [l for l in p.stdout.splitlines() if l.strip()]
        self.assertEqual(ignored, [], "test files that .gitignore hides -- they run here and never ship: %s" % ignored)


if __name__ == "__main__":
    unittest.main()
