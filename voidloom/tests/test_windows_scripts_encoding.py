"""Windows PowerShell 5.1 reads a BOM-less .ps1 in the system ANSI codepage.

Found 2026-09-18 on the first execution of install/install.ps1 ever: 56 em-dashes
in string literals decoded as smart quotes, which PowerShell accepts as string
delimiters, so every one terminated its string early and the file did not
parse. The script had shipped for months. A .ps1 that carries any non-ASCII
byte MUST begin with the UTF-8 BOM (EF BB BF). A .cmd must NOT (cmd.exe
mis-parses a BOM as part of the first command) and must be pure ASCII.

Red at parent: both .ps1 files lacked the BOM at cff3c80.
"""
import json
import os
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
BOM = b"\xef\xbb\xbf"


def _shipped(suffix):
    """The package's OWN scripts with this suffix, from the lay manifest -- never a walk.

    The population used to be `git ls-files` with an `rglob` fallback. On an installed
    brain there is no git, and the walk found either nothing (a POSIX-laid brain lays no
    Windows file: a wrong red) or the Tier-1 venv's own `Activate.ps1` (a file this
    package does not ship: a vacuous green that then graded a foreign file). Found by
    the round-1 iteration-3 generator, 2026-09-19. The manifest lists what ships and
    where it lands; an entry that is not on disk here is "ships, not laid on this
    platform", which is a skip with that reason, not a zero.
    """
    manifest = ROOT / "install" / "lay_manifest.json"
    if not manifest.is_file():
        raise unittest.SkipTest("NOT A PACKAGE TREE: %s has no install/lay_manifest.json "
                                "(run from the checkout or an installed brain, or set VOIDLOOM_ROOT)" % ROOT)
    entries = json.loads(manifest.read_text(encoding="utf-8"))["entries"]
    listed = [e for e in entries if str(e.get("src", "")).endswith(suffix)]
    present = [ROOT / e["src"] for e in listed if (ROOT / e["src"]).is_file()]
    return listed, present


class TestWindowsScriptEncoding(unittest.TestCase):

    def test_population_is_nonempty(self):
        listed, present = _shipped(".ps1")
        self.assertGreater(len(listed), 0, "the manifest lists no .ps1 — the package must SHIP its Windows installer")
        if not present:
            raise unittest.SkipTest("the %d shipped .ps1 are not laid on this platform (other-platform entries); "
                                    "graded only in a checkout or a Windows-laid brain" % len(listed))
        self.assertEqual(len(present), len(listed), "some shipped .ps1 are missing from disk: %s"
                         % sorted(str(e["src"]) for e in listed if not (ROOT / e["src"]).is_file()))

    def test_every_ps1_with_non_ascii_has_a_bom(self):
        bad = []
        for f in _shipped(".ps1")[1]:
            b = f.read_bytes()
            non_ascii = any(x > 0x7F for x in b[3:] if True) if b.startswith(BOM) else any(x > 0x7F for x in b)
            if non_ascii and not b.startswith(BOM):
                bad.append(str(f.relative_to(ROOT)))
        self.assertEqual(bad, [], f".ps1 with non-ASCII and no BOM (PowerShell 5.1 will mis-parse): {bad}")

    def test_every_cmd_is_ascii_without_bom(self):
        bad = []
        for f in _shipped(".cmd")[1]:
            b = f.read_bytes()
            if b.startswith(BOM) or any(x > 0x7F for x in b):
                bad.append(str(f.relative_to(ROOT)))
        self.assertEqual(bad, [], f".cmd with a BOM or non-ASCII (cmd.exe will mis-parse): {bad}")


if __name__ == "__main__":
    unittest.main()
