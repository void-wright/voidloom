"""The package manifest's own row prints no digest, and the release check still finds it.

THE CLAIM UNDER TEST
  * a manifest listing every tracked file, its own row included, reads CLEAN;
  * a manifest missing its own row, or any other tracked file, reads RED.
"""
from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SELF = "docs/PACKAGE_MANIFEST.md"


def _release_check():
    spec = importlib.util.spec_from_file_location("_vl_release_check", ROOT / "tools" / "release_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class OwnRow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "docs").mkdir()
        (self.d / "a.txt").write_bytes(b"a")
        self.man = self.d / SELF
        self.write(own=True, other=True)
        subprocess.run(["git", "init", "-q", str(self.d)], check=True)
        subprocess.run(["git", "-C", str(self.d), "add", "-A"], check=True)
        self.mod = _release_check()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *, own, other):
        rows = []
        if other:
            rows.append(f"| `a.txt` | 1 | `{hashlib.sha256(b'a').hexdigest()}` |")
        if own:
            rows.append(f"| `{SELF}` | 0 | (self -- not pinnable) |")
        self.man.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def check(self):
        with mock.patch.object(self.mod, "ROOT", self.d):
            return self.mod.manifest_check()

    def test_control_every_row_present_reads_clean(self):
        out = self.check()
        self.assertEqual((out["verdict"], out["unlisted"]), ("CLEAN", 0), out)

    def test_saboteur_a_manifest_without_its_own_row_reads_red(self):
        self.write(own=False, other=True)
        out = self.check()
        self.assertEqual((out["verdict"], out["flagged"]), ("RED", [SELF]), out)

    def test_saboteur_another_file_missing_reads_red(self):
        self.write(own=True, other=False)
        out = self.check()
        self.assertEqual(out["verdict"], "VACUOUS" if not out["rows"] else "RED", out)
        self.assertIn("a.txt", out["flagged"])


if __name__ == "__main__":
    unittest.main()
