# -*- coding: utf-8 -*-
"""The suite and its harness name no single machine.

CRUCIBLE-3 seat D8-F7. `grep -rn '<owner-home>' voidloom/tests/ tools/harness/`
returned **13 hits across 7 files** — two independent copies of the brain path as
bare string literals, a candidate directory, an anaconda bin dir, and four shell
defaults. There was no way to point the suite at another brain, so on any machine
but one the host-bound classes vanished **silently**:

  > *A skip is indistinguishable from a pass in the summary line, which is how
  > "101 passed" became a sentence about a host.*

The repair was to resolve every one of them — env-first, then from the file's own
location, in the order the engine, the CLI and the installer already use. This
test is what keeps them resolved. It is deliberately a GREP and not an AST walk:
the defect is a string, in Python and in shell alike, and an AST check would have
been blind to four of the seven files.

Scope is the SUITE and its HARNESS. The rest of the package has its own gates
(`tools/pii_gate.py`, `tools/gpu_pin_gate.py`), and widening this one to the whole
tree would duplicate them and disagree with them at the seams.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCOPE = ("voidloom/tests", "tools/harness")
# An absolute path into somebody's home. Not `~` (portable) and not `$HOME`
# (resolved at run time) -- a literal, which is the only unportable form.
HOME_LITERAL = re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/")
READABLE = {".py", ".sh", ".txt", ".json", ".md", ".cfg", ".ini"}
# A home path can be a FIXTURE rather than a setting: several tests feed fake
# absolute paths in as INPUT (cloud-sync detection, hook-marker matching against
# a foreign hook). Those are correct and must not be "fixed" into resolution --
# the whole point of the input is that it is somebody else's machine.
#
# The discriminator is the user segment, and it is a DECLARED list rather than a
# guess: a real account name is not in it, so a new `/home/<realname>/` fails.
# Printed in the failure message so the rule argues for itself.
SYNTHETIC_USERS = {"someone", "x", "user", "you", "example", "test", "alice", "bob"}
# Frozen post-mortems and this file's own docstring quote the literal in order to
# record it. Named, with the reason, the way the release gates publish theirs.
EXEMPT = {
    "voidloom/tests/test_suite_is_portable.py":
        "this file quotes the pattern it enforces, in its own docstring",
}


class TestSuiteIsPortable(unittest.TestCase):

    def test_no_shipped_test_or_harness_file_hardcodes_a_home_path(self):
        files = []
        for rel in SCOPE:
            base = ROOT / rel
            if base.is_dir():
                files += [p for p in base.rglob("*")
                          if p.is_file() and p.suffix in READABLE
                          and "__pycache__" not in p.parts
                          and not p.name.endswith(".bak")]
        # Vacuity guard: an empty population would pass without observing anything.
        self.assertGreater(len(files), 10,
                           f"only {len(files)} files in scope — the population "
                           "is too small to be the suite; the walk is wrong")
        hits = []
        for p in files:
            rel = p.relative_to(ROOT).as_posix()
            if rel in EXEMPT:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                for m in HOME_LITERAL.finditer(line):
                    who = m.group(0).rstrip("/").rsplit("/", 1)[-1]
                    if who.lower() in SYNTHETIC_USERS:
                        continue        # a fixture, not a setting
                    hits.append(f"{rel}:{n}  {line.strip()[:90]}")
        self.assertEqual(hits, [],
                         "the suite hard-codes one machine's paths — resolve them "
                         "env-first, then from the file's own location. (A path "
                         "used as test INPUT may use one of the declared synthetic "
                         f"users {sorted(SYNTHETIC_USERS)}.)\n  "
                         + "\n  ".join(hits))

    def test_the_live_brain_is_resolved_not_typed(self):
        """`LIVE_DB` was a bare literal in two files, independently."""
        for rel in ("voidloom/tests/test_voidloom.py", "voidloom/tests/test_batch.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            m = re.search(r"^LIVE_DB\s*=\s*(.+)$", src, re.M)
            self.assertIsNotNone(m, f"{rel} no longer defines LIVE_DB")
            rhs = m.group(1).strip()
            self.assertFalse(rhs.startswith(('"', "'")),
                             f"{rel}: LIVE_DB is a typed literal ({rhs}) — it must "
                             "be resolved through the product's own env-first order")


if __name__ == "__main__":
    unittest.main()
