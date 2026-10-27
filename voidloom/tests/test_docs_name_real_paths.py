# -*- coding: utf-8 -*-
"""Every path and flag a shipped doc tells the reader to run must exist.

CRUCIBLE-3 D9-F6 and D9-F7. The 2026-09-07 move of the engine and organ modules
into ``organs/`` was correct. The documentation did not follow it, and nobody
noticed because a path in prose is not executed by anything:

  * all four commands in the README's own "Daily Health Checks" failed as
    written (4/4, rc=2) -- the operator-facing health and disaster-recovery
    surface, i.e. the person under the most time pressure;
  * five scripts the onboarding guide tells the reader to run could not be
    invoked at the paths given, including the ONE command in Step 5, the step
    that makes the reader's authored persona load every session;
  * the guide documented ``--no-council``, a flag ``install.sh`` has never
    accepted and ``--help`` has never listed;
  * the README's inventory table pointed at ``$HOME/.claude/memory/*.py``
    (0 files there) and ``migration_*.sql`` (0 there);
  * it named ``Brain_Sync/sync_to_eddie.py``, which does not ship at all;
  * and ``docs/LAYOUT.md`` -- the map a new reader is sent to first -- carried
    three figures under the sentence "derived from the tree at write time, never
    typed", all three stale.

Most of that was repaired across cycle 2. This binds it, because the repair is
worth nothing if the next reorganisation re-opens it silently. The population is
DERIVED from ``git ls-files`` every run; nothing here is a list of known-good
paths that would itself go stale.

Deliberately NOT asserted: that every command SUCCEEDS. Running them would need
a brain, a venv and a network, and a test that needs those is a test that gets
skipped. What is asserted is the property that actually broke -- the thing the
reader is told to run EXISTS, under the name they are told to use.
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The docs a stranger is actually pointed at. docs/cycle2/ is excluded BY NAME
# and with a reason: those are frozen post-mortems that quote paths as they were
# at the time, and re-deriving history is not a thing this check can ask for.
DOCS = ["README.md", "docs/ONBOARDING_GUIDE.md", "docs/LAYOUT.md",
        "docs/FIRST_NIGHT.md", "docs/RESTORE_MANIFEST.md"]

# A path-shaped token ending in a runnable/shipped extension.
PATH_TOK = re.compile(
    r"(?<![\w/.~$-])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:py|sh|ps1|sql|cmd|mjs))")
# A reference to the INSTALLED tree, which mirrors the package layout.
INSTALLED_TOK = re.compile(
    r"(?:~|\$HOME)/\.claude/memory/([A-Za-z0-9_./-]+\.(?:py|sh|ps1|sql|cmd|mjs))")
# A long flag attributed to install.sh in a fenced or inline command.
# Digits allowed (reference-host round, R1): `--tier1-ambient` read as `--tier` under [a-z-]+, so the
# binder named a flag nobody wrote and could not see the one that exists.
INSTALL_FLAG = re.compile(r"(?<![\w-])install\.sh[^\n`]*?\s(--[a-z][a-z0-9-]+)")  # not uninstall.sh

# Names that legitimately appear as bare words rather than as package paths:
# generated artifacts, third-party files, and shapes rather than files.
# A bare filename after one of these is being RUN, not merely named.
INTERPRETERS = {"python3", "python", "bash", "sh", "pwsh", "powershell", "py"}

NOT_PACKAGE_FILES = {
    "claude_memory.db", "voidloom_manifest.json", "seed_pack.json",
    "settings.json", "CLAUDE.md", "AGENTS.md",
}


def tracked():
    out = subprocess.run(["git", "ls-files"], cwd=str(ROOT),
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        return None
    return sorted(p for p in out.stdout.split("\n") if p.strip())


class TestDocsNameRealPaths(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tracked = tracked()
        cls.docs = [(d, (ROOT / d)) for d in DOCS if (ROOT / d).is_file()]

    def setUp(self):
        if self.tracked is None:
            self.skipTest("git unavailable — the population cannot be derived, "
                          "which is UNKNOWN and not the same as clean")
        # Vacuity guard: an empty doc set would pass every assertion below.
        self.assertGreater(len(self.docs), 0, "no shipped docs found")

    def test_every_package_path_in_a_doc_resolves(self):
        by_base = {}
        for t in self.tracked:
            by_base.setdefault(t.rsplit("/", 1)[-1], []).append(t)
        wrong = []
        for name, path in self.docs:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for m in PATH_TOK.finditer(line):
                    # `./install.sh` is the same file as `install.sh` -- a
                    # cwd-relative invocation, not a wrong path.
                    tok = m.group(1)
                    if tok.startswith("./"):
                        tok = tok[2:]
                    base = tok.rsplit("/", 1)[-1]
                    if base in NOT_PACKAGE_FILES:
                        continue
                    if tok in self.tracked:
                        continue
                    homes = by_base.get(base)
                    if homes is None:
                        continue      # not one of ours; another test owns "ships"
                    if any(h == tok or h.endswith("/" + tok) for h in homes):
                        # A bare name is fine in PROSE ("claude_memory.py is the
                        # engine") and fatal in a COMMAND: `python3
                        # claude_memory.py stats` from ~/.claude/memory is
                        # exactly what failed 4/4 in the README's health checks.
                        # The discriminator is the interpreter in front of it.
                        if "/" in tok:
                            continue
                        before = line[:m.start(1)].rstrip().rsplit(" ", 1)[-1]
                        if before not in INTERPRETERS:
                            continue
                        wrong.append(f"{name}:{n}  {before} {tok}  "
                                     f"(runnable only as {', '.join(homes)})")
                        continue
                    wrong.append(f"{name}:{n}  {tok}  ships at {', '.join(homes)}")
        self.assertEqual(wrong, [], "docs name package paths that do not resolve:\n  "
                                    + "\n  ".join(wrong))

    def test_every_installed_path_in_a_doc_mirrors_a_shipped_file(self):
        """``~/.claude/memory/X`` must correspond to a shipped ``X``.

        This is the half that caught Step 5's cron command: the token carries a
        home prefix, so a package-path check walks straight past it.
        """
        wrong = []
        tracked_set = set(self.tracked)
        for name, path in self.docs:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for m in INSTALLED_TOK.finditer(line):
                    rel = m.group(1)
                    if rel.rsplit("/", 1)[-1] in NOT_PACKAGE_FILES:
                        continue
                    if rel in tracked_set:
                        continue
                    wrong.append(f"{name}:{n}  {m.group(0)}  (no shipped {rel})")
        self.assertEqual(wrong, [], "docs point into the installed tree at paths the "
                                    "package does not lay:\n  " + "\n  ".join(wrong))

    def test_every_install_sh_flag_a_doc_names_is_accepted(self):
        """``--no-council`` was documented for months and never existed."""
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        accepted = set(re.findall(r"^\s*(--[a-z][a-z0-9-]*)\)", sh, re.M))
        for alt in re.findall(r"^\s*(--[a-z][a-z0-9-]*)\|", sh, re.M):
            accepted.add(alt)
        accepted.add("--help")
        self.assertGreater(len(accepted), 1, "no flags parsed out of install.sh — "
                                             "the population is empty")
        unknown = []
        for name, path in self.docs:
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "no `--" in line or "no **`--" in line:
                    continue          # a sentence saying a flag does NOT exist
                for m in INSTALL_FLAG.finditer(line):
                    if m.group(1) not in accepted:
                        unknown.append(f"{name}:{n}  {m.group(1)}")
        self.assertEqual(unknown, [], "docs name install.sh flags it does not accept:\n  "
                                      + "\n  ".join(unknown))

    def test_install_sh_help_prints_no_source(self):
        """`--help` used to end with `set -uo pipefail` and two lines under it.

        It extracted its own header with a TYPED line number (`sed -n '2,50p'`),
        which was right when written and three lines past the header as soon as
        the header grew.
        """
        out = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[:400])
        leaked = [l for l in out.stdout.splitlines()
                  if re.match(r"^(set -|[A-Z_]+=)", l)]
        self.assertEqual(leaked, [], "install.sh --help leaks its own source: "
                                     + "; ".join(leaked))
        self.assertGreater(len(out.stdout.splitlines()), 20,
                           "--help printed almost nothing; the extractor stopped early")

    def test_the_verb_reference_documents_every_verb(self):
        """D9-F8: the doc README calls the "Full verb + envelope reference"
        documented 7 of 14, while --version still read 0.1.0 -- so the shipped
        guide for the shipped version omitted half of that version's surface.

        Derived from ``voidloom --help`` both ways: a verb the CLI grew and the
        page did not, AND a verb the page invents, are both failures.
        """
        cli = ROOT / "voidloom" / "voidloom"
        if not cli.exists():
            self.skipTest("the voidloom CLI shim is not present")
        out = subprocess.run([str(cli), "--help"], capture_output=True, text=True,
                             timeout=180)
        self.assertEqual(out.returncode, 0, out.stderr[:400])
        verbs = set(re.findall(r"^\s{4}([a-z][a-z-]+)\s", out.stdout, re.M))
        verbs |= set(re.findall(r"^\s{4}([a-z][a-z-]+)$", out.stdout, re.M))
        self.assertGreater(len(verbs), 5, "no verbs parsed out of --help — "
                                          "the population is empty")
        doc = (ROOT / "voidloom" / "README.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"^voidloom ([a-z][a-z-]+)", doc, re.M))
        self.assertEqual(sorted(verbs - documented), [],
                         "voidloom/README.md omits shipped verbs: "
                         f"{sorted(verbs - documented)}")
        self.assertEqual(sorted(documented - verbs), [],
                         "voidloom/README.md documents verbs the CLI does not have: "
                         f"{sorted(documented - verbs)}")

    def test_seed_inventory_figures_match_the_pack(self):
        """D9-F9: the guide stated the seed inventory three times, three values,
        none correct -- and the Step-7 check passed only because its threshold
        (>= 36, against a pack of 66) was loose enough to survive being wrong."""
        import json
        pack = ROOT / "seed" / "seed_pack.json"
        if not pack.is_file():
            self.skipTest("seed_pack.json is not in this tree")
        n = len(json.loads(pack.read_text(encoding="utf-8"))["memories"])
        self.assertGreater(n, 0, "the seed pack is empty — the population is zero")
        text = (ROOT / "docs" / "ONBOARDING_GUIDE.md").read_text(encoding="utf-8")
        stated = set()
        for m in re.finditer(r"`total_memories` ≥ \*\*(\d+)\*\*", text):
            stated.add(int(m.group(1)))
        for m in re.finditer(r"\*\*(\d+)\*\* agnostic teaching memories", text):
            stated.add(int(m.group(1)))
        self.assertTrue(stated, "the guide no longer states a seed inventory figure "
                                "in either of the shapes this check knows")
        self.assertEqual(stated, {n},
                         f"the guide states seed figure(s) {sorted(stated)}; "
                         f"seed/seed_pack.json holds {n}")

    def test_layout_counts_match_the_tree(self):
        text = (ROOT / "docs" / "LAYOUT.md").read_text(encoding="utf-8")
        n_all = len(self.tracked)
        n_top = len([t for t in self.tracked if "/" not in t])
        n_org = len([t for t in self.tracked
                     if t.startswith("organs/") and t.endswith(".py")])
        m = re.search(r"\*\*(\d+) tracked files, (\d+) of them at the", text)
        self.assertIsNotNone(m, "LAYOUT.md no longer opens with its derived counts")
        self.assertEqual((int(m.group(1)), int(m.group(2))), (n_all, n_top),
                         f"LAYOUT.md says {m.group(1)} tracked / {m.group(2)} top-level; "
                         f"git ls-files says {n_all} / {n_top}")
        organs = set(int(x) for x in re.findall(r"(\d+) (?:tracked )?modules", text))
        self.assertTrue(organs, "LAYOUT.md no longer states an organs module count")
        self.assertEqual(organs, {n_org},
                         f"LAYOUT.md states organs module count(s) {sorted(organs)}; "
                         f"git ls-files says {n_org}")


if __name__ == "__main__":
    unittest.main()
