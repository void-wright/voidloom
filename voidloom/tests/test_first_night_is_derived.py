# -*- coding: utf-8 -*-
"""docs/FIRST_NIGHT.md says "Derived from `cron/*.timer`". This makes that true.

CRUCIBLE-3 D9-F4: one page carried THREE values — a headline of 13, a heading of
13, a table of 12 — for a directory holding 17, under a sentence claiming the
figure was derived rather than typed. It also named three shipped units as absent
and declared eight units dead that were not.

The numbers were never the defect. NOTHING RE-DERIVED THEM WHEN `cron/` CHANGED,
so a page that was accurate when written rotted silently while remaining the most
trusted document in the package — it is the page a reader consults BEFORE arming
jobs that cost money and mutate the brain.

So this is the sensor, not a second copy of the numbers: every assertion below
reads the DIRECTORY and the INSTALLER, and compares them to what the page claims.
A future unit file makes this fail until the page is updated, which is the only
arrangement under which "derived" is a fact rather than a flattering adjective.

The armed set is read from install/cron_roster.json -- the ONE declaration both
installers consume (Council #12) -- never from either installer's text.

It also binds three packaging properties the same measurement surfaced:
  * every packaged unit's ExecStart target actually ships (it used to be 8 of 13
    that did not — and the page's own verification loop, which compared an
    unexpanded `%h` against the filesystem, reported ALL of them dead);
  * every packaged unit reads ONE sidecar env file (there were two paths, one of
    which nothing created, and four units that would hard-fail on its absence);
  * no packaged unit hardcodes a CUDA device (two shipped `=1`, one machine's
    answer baked into the package — on a single-GPU host index 1 does not exist).
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

# VOIDLOOM_ROOT points this test at another tree (the round-1 red-at-parent arm);
# the default is this file's own package.
ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
CRON = ROOT / "cron"
DOC = ROOT / "docs" / "FIRST_NIGHT.md"
CRONS_SH = ROOT / "install" / "install_crons.sh"

SIDECAR = "EnvironmentFile=-%h/.voidloom/wizard-library-env"
PREFIX = "wizard-library-"

def _tracked_timers():
    """The PACKAGED timers — enumerated from git, never from the directory.

    Until 2026-09-18 this globbed the directory, and in the live tree that
    included an untracked timer (the personal Drive backup, untracked on
    2026-09-07 and left on disk), so the doc's "derived" count read 18 for a
    package that ships 17. A count derived from the install is not derived
    from the package. Outside a checkout (no git) it falls back to the glob and
    the test still runs; inside one it cannot be inflated by a stray file.
    """
    import subprocess
    try:
        out = subprocess.run(["git", "ls-files", "--", str(CRON.relative_to(ROOT)) + "/" + PREFIX + "*.timer"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        if out:
            return [ROOT / f for f in sorted(set(out))]   # set(): a half-merged path prints once per stage
    except Exception:
        pass
    return sorted(CRON.glob(PREFIX + "*.timer"))



def shipped_units():
    """The population, DERIVED at run time — never a literal list in this file."""
    return sorted(p.name[len(PREFIX):-len(".timer")]
                  for p in _tracked_timers())


def doc_table_rows(text):
    """The unit names in the page's schedule table, in order."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"^\|\s*`([a-z0-9-]+)`\s*\|", line)
        if m:
            rows.append(m.group(1))
    return rows


class TestFirstNightIsDerived(unittest.TestCase):

    def setUp(self):
        self.assertTrue(DOC.is_file(), f"{DOC} is missing")
        self.text = DOC.read_text(encoding="utf-8")
        self.units = shipped_units()
        # A population of zero would make every assertion below vacuously true.
        # 0/0 CHECKS HELD is the failure this suite exists to not repeat.
        self.assertGreater(len(self.units), 0, "no packaged timers found — "
                           "the population is empty and every check below would "
                           "pass without observing anything")

    def test_headline_count_matches_the_directory(self):
        m = re.search(r"\|\s*scheduled jobs\s*\|\s*\*\*(\d+)\*\*", self.text)
        self.assertIsNotNone(m, "the short-version table no longer declares a "
                                "scheduled-jobs count in the expected shape")
        self.assertEqual(int(m.group(1)), len(self.units),
                         f"FIRST_NIGHT.md declares {m.group(1)} packaged timers; "
                         f"cron/ holds {len(self.units)}")

    def test_schedule_table_lists_every_shipped_unit_and_no_other(self):
        rows = doc_table_rows(self.text)
        self.assertEqual(sorted(rows), self.units,
                         "the schedule table and cron/ disagree: "
                         f"only in the doc={sorted(set(rows) - set(self.units))}, "
                         f"only in cron/={sorted(set(self.units) - set(rows))}")
        self.assertEqual(len(rows), len(set(rows)), "duplicate rows in the table")

    def test_llm_marked_rows_match_the_stated_count(self):
        stated = re.search(r"\*\*(?:Eight|Six|Seven|Nine|Ten|\d+) of the "
                           r"(?:eighteen|seventeen|\d+) call an LLM\*\*", self.text)
        self.assertIsNotNone(stated, "the LLM warning no longer states a count")
        marked = sum(1 for line in self.text.splitlines()
                     if re.match(r"^\|\s*`[a-z0-9-]+`\s*\|", line)
                     and line.rstrip().endswith("**yes** |"))
        words = {"six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        head = stated.group(0).split()[0].strip("*").lower()
        self.assertEqual(words.get(head, -1), marked,
                         f"the page says {head} jobs call an LLM; the table marks "
                         f"{marked} rows **yes**")

    def test_installer_arm_count_and_names_match_the_roster(self):
        """The page says which jobs the installers arm. Derive both from the ROSTER.

        Council #12 (2026-09-18): this used to read the armed set out of
        install_crons.sh's hand-written `install_linux <name>` calls -- four of
        them, disagreeing with install.sh's six-row CRON_DEFS, with sleep-loop
        in neither. The one declaration is now install/cron_roster.json; both
        installers read it; this page follows it. The count alone would pass
        while naming the wrong units, so the fenced block naming them is bound
        too.
        """
        roster = ROOT / "install" / "cron_roster.json"
        self.assertTrue(roster.is_file(), "install/cron_roster.json is absent -- the armed set "
                                          "is not declared once")
        import json
        units = json.loads(roster.read_text(encoding="utf-8"))["units"]
        armed = sorted(n for n, u in units.items() if u.get("arm") == "armed-by-default")
        self.assertIn("sleep-loop", armed, "the armed set lacks sleep-loop")
        self.assertEqual(sorted(units), self.units,
                         "the roster and cron/ disagree: only in roster=%s, only in cron/=%s"
                         % (sorted(set(units) - set(self.units)), sorted(set(self.units) - set(units))))
        m = re.search(r"arms? \*\*(\d+)\*\* of them", self.text)
        self.assertIsNotNone(m, "the page no longer states how many units the "
                                "installers arm")
        self.assertEqual(int(m.group(1)), len(armed),
                         f"the page says the installers arm {m.group(1)}; the roster "
                         f"arms {len(armed)}: {armed}")
        # And the page NAMES them in a fenced block; those names must be the ones.
        named = set()
        for blk in re.findall(r"```\n([a-z0-9-\s]+?)\n```", self.text):
            toks = blk.split()
            if toks and set(toks) <= set(self.units):
                named = set(toks)
                break
        self.assertEqual(sorted(named), armed,
                         f"the page names {sorted(named)} as the armed set; "
                         f"the roster arms {armed}")

    def test_every_packaged_unit_targets_a_file_that_ships(self):
        missing = []
        for svc in sorted(CRON.glob(PREFIX + "*.service")):
            line = next((l for l in svc.read_text(encoding="utf-8").splitlines()
                         if l.startswith("ExecStart=")), "")
            tgt = next((tok for tok in line.split() if tok.endswith(".py")), None)
            if tgt is None:
                continue
            rel = tgt.replace("%h/.claude/memory/", "")
            if not (ROOT / rel).exists():
                missing.append(f"{svc.name} -> {rel}")
        self.assertEqual(missing, [], "packaged units point at scripts this "
                                      "package does not ship: " + "; ".join(missing))

    def test_every_packaged_unit_reads_the_one_sidecar_env_file(self):
        wrong = []
        for svc in sorted(CRON.glob(PREFIX + "*.service")):
            for line in svc.read_text(encoding="utf-8").splitlines():
                if line.startswith("EnvironmentFile=") and line.strip() != SIDECAR:
                    wrong.append(f"{svc.name}: {line.strip()}")
        self.assertEqual(wrong, [],
                         "packaged units disagree about the sidecar env file "
                         f"(the one path is {SIDECAR!r}): " + "; ".join(wrong))

    def test_no_packaged_unit_hardcodes_a_cuda_device(self):
        pinned = []
        for svc in sorted(CRON.glob(PREFIX + "*.service")):
            for n, line in enumerate(svc.read_text(encoding="utf-8").splitlines(), 1):
                if re.match(r"^Environment=CUDA_VISIBLE_DEVICES=", line):
                    pinned.append(f"{svc.name}:{n} {line.strip()}")
        self.assertEqual(pinned, [],
                         "a packaged unit hardcodes a CUDA device — the device is "
                         "resolved at install time and reaches the unit through the "
                         "env file, which has one writer: " + "; ".join(pinned))


if __name__ == "__main__":
    unittest.main()
