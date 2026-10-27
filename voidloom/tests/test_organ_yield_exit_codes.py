"""R6 (reference-host round): the organ-yield sentinel RUNS, and a crash is not a finding.

Measured on the reference host (journal + organ_run_log, 2026-09-18..09-25):
`organs/organ_yield_sentinel.py` used `Path` in main()'s argparse default without
importing it -- a NameError on every run, before any work -- and systemd recorded
"Finished", the ledger "success", five mornings of five. Why: the sentinel used exit 1
for "BROKEN organs found" and the unit + the roster declared `SuccessExitStatus=0 1`,
while an uncaught Python exception ALSO exits 1. A crash and a finding were one status.

Bound here, each executed:
  * main() RUNS on a scratch brain: every watched organ STARVED (all inputs empty)
    -> EXIT_CLEAN (0); one planted episodic memory with no consolidation output ->
    that organ BROKEN -> EXIT_BROKEN_FOUND, a code distinct from 0, 1 and 2;
  * a forced crash (the DB path is a directory) exits 1 -- the interpreter's code --
    and 1 is NOT in the unit's SuccessExitStatus, so systemd reports it failed;
  * the packaged unit and the roster's service_extra declare EXACTLY
    {0, EXIT_BROKEN_FOUND}, read from the module, never typed twice.
At c6d7af2: main() dies with NameError (rc 1) on both brains, and both declarations
admit 1 -- red on every assertion below.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
ORGAN = ROOT / "organs" / "organ_yield_sentinel.py"
UNIT = ROOT / "cron" / "wizard-library-organ-yield.service"
ROSTER = ROOT / "install" / "cron_roster.json"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))

# The minimal substrate every WATCH entry reads: each organ's output table (with its clock)
# and the tables its input_sql consumes -- plus the dream row invariant's (dream_log, its phase
# log, the backfill tags, the met ledger; the dream fix, A6). The fixture is checked against the module's own
# WATCH list below, so an organ added there without a table here fails loudly, not silently.
SCHEMA = """
CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT, summary TEXT, kind TEXT, is_archived INTEGER DEFAULT 0,
                       derived_from TEXT, created_at TEXT);
CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT);
CREATE TABLE dream_log (id INTEGER PRIMARY KEY, dream_type TEXT, memory_a_id INTEGER, memory_b_id INTEGER,
                        dream_memory_id INTEGER, is_meaningful INTEGER DEFAULT 0, created_at TEXT);
CREATE TABLE dream_phase_log (id INTEGER PRIMARY KEY, phase_name TEXT, detail TEXT, created_at TEXT);
CREATE TABLE access_log (id INTEGER PRIMARY KEY, memory_id INTEGER, access_type TEXT, accessed_at TEXT);
CREATE TABLE consolidation_sources (id INTEGER PRIMARY KEY, created_at TEXT);
CREATE TABLE memory_edges (id INTEGER PRIMARY KEY, source_id INTEGER, target_id INTEGER, predicate TEXT,
                           valid_until TEXT, metadata TEXT, extraction_method TEXT, created_at TEXT);
CREATE TABLE memory_provenance (id INTEGER PRIMARY KEY, derived_memory_id INTEGER, created_at TEXT);
CREATE TABLE belief_scan_queue (id INTEGER PRIMARY KEY, memory_id INTEGER, processed_at TEXT);
CREATE TABLE embedding_provenance (memory_id INTEGER, organ TEXT);
CREATE TABLE edge_proposals (id INTEGER PRIMARY KEY, proposal_kind TEXT, source_id INTEGER, target_id INTEGER,
                             status TEXT, created_at TEXT);
CREATE TABLE sleep_loop_runs (id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT, status TEXT,
                              stage_reached TEXT, error_log TEXT);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE cross_tier_bridges (id INTEGER PRIMARY KEY, created_at TEXT);
CREATE TABLE memory_embeddings_v2_rowids (rowid INTEGER PRIMARY KEY, id INTEGER);
"""


def _module_constants():
    """EXIT_* and WATCH, read from the organ's own source by a subprocess (no import side
    effects in this process; the module reads ~/.config at import)."""
    code = ("import json, runpy, sys; g = runpy.run_path(sys.argv[1]); "
            "d = {k: g.get(k) for k in ('EXIT_CLEAN', 'EXIT_BLIND', 'EXIT_BROKEN_FOUND')}; "
            "d['WATCH'] = [{'out_table': w['out_table'], 'input_sql': w['input_sql']} for w in g['WATCH']]; "
            "d['RUN_WATCH'] = [{'table': w['table']} for w in g.get('RUN_WATCH', [])]; "
            "print(json.dumps(d))")
    p = subprocess.run([sys.executable, "-B", "-c", code, str(ORGAN)], capture_output=True, text=True, timeout=120,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if p.returncode != 0:
        raise AssertionError("could not read the organ's constants: " + p.stderr[-600:])
    return json.loads(p.stdout)


def _success_set(lines):
    vals = [l.split("=", 1)[1] for l in lines if l.strip().startswith("SuccessExitStatus=")]
    if len(vals) != 1:
        raise AssertionError("expected exactly one SuccessExitStatus line, got %r" % vals)
    return {int(t) for t in vals[0].split()}


class TestOrganYieldExitCodes(unittest.TestCase):
    def setUp(self):
        if not ORGAN.is_file():
            raise unittest.SkipTest("tree lacks %s" % ORGAN)
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="oyield_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.consts = _module_constants()
        # HOME is the scratch dir: the sentinel's unit-dir oracle (~/.config/systemd/user) is
        # ABSENT there -- "units unreadable", which is not the same as "no units" -- so no
        # verdict depends on the operator's real units.
        self.env = {**os.environ, "HOME": str(self.tmp), "PYTHONDONTWRITEBYTECODE": "1"}
        for k in ("XDG_CONFIG_HOME", "WIZARD_LIBRARY_DB"):
            self.env.pop(k, None)

    def _brain(self, name, planted_episodic=False):
        db = self.tmp / name
        c = sqlite3.connect(str(db))
        c.executescript(SCHEMA)
        for spec in self.consts["WATCH"]:          # the fixture covers the module's own population
            c.execute("SELECT COUNT(*) FROM \"%s\"" % spec["out_table"])
            c.execute(spec["input_sql"])
        for spec in self.consts.get("RUN_WATCH", []):   # round 1: the run ledger the sentinel reads directly
            c.execute("SELECT COUNT(*) FROM \"%s\"" % spec["table"])
        if planted_episodic:
            c.execute("INSERT INTO memories (content, kind, is_archived, created_at) VALUES "
                      "('an unconsolidated episode', 'episodic', 0, datetime('now'))")
        c.commit()
        c.close()
        return db

    def _run(self, db):
        return subprocess.run([sys.executable, "-B", str(ORGAN), str(db)], capture_output=True, text=True,
                              env=self.env, timeout=300)

    def test_broken_found_is_a_distinct_code(self):
        code = self.consts.get("EXIT_BROKEN_FOUND")
        self.assertIsInstance(code, int, "the organ declares no EXIT_BROKEN_FOUND")
        self.assertNotIn(code, (0, 1, 2), "BROKEN-found must not share a code with clean (0), a crash (1) "
                                          "or a blind sensor (2)")
        self.assertEqual((self.consts.get("EXIT_CLEAN"), self.consts.get("EXIT_BLIND")), (0, 2))

    def test_main_runs_clean_on_a_starved_brain(self):
        p = self._run(self._brain("clean.db"))
        self.assertNotIn("NameError", p.stderr, "main() crashed before doing any work")
        self.assertEqual(p.returncode, 0, (p.stdout[-800:], p.stderr[-800:]))
        self.assertIn("0 BROKEN", p.stdout)

    def test_a_planted_broken_organ_exits_the_distinct_code(self):
        p = self._run(self._brain("broken.db", planted_episodic=True))
        self.assertNotIn("Traceback", p.stderr, p.stderr[-800:])
        self.assertEqual(p.returncode, self.consts.get("EXIT_BROKEN_FOUND"), (p.stdout[-800:], p.stderr[-800:]))
        m = re.search(r"^\s*(\d+) BROKEN,", p.stdout, re.M)
        self.assertTrue(m and int(m.group(1)) >= 1, "no BROKEN organ reported: %r" % p.stdout[-600:])

    def test_a_crash_exits_1_and_the_unit_does_not_accept_it(self):
        crash_db = self.tmp / "not_a_db"
        crash_db.mkdir()                      # sqlite cannot open a directory: an uncaught error
        p = self._run(crash_db)
        self.assertIn("Traceback", p.stderr, "the forced crash did not crash: %r" % p.stderr[-400:])
        self.assertEqual(p.returncode, 1, p.stderr[-400:])
        declared = _success_set(UNIT.read_text(encoding="utf-8").splitlines())
        self.assertNotIn(p.returncode, declared, "the packaged unit reports a crash as success: %s" % declared)

    def test_unit_and_roster_declare_exactly_clean_and_broken_found(self):
        want = {0, self.consts.get("EXIT_BROKEN_FOUND")}
        unit = _success_set(UNIT.read_text(encoding="utf-8").splitlines())
        self.assertEqual(unit, want, "cron/wizard-library-organ-yield.service")
        extra = json.loads(ROSTER.read_text(encoding="utf-8"))["units"]["organ-yield"]["service_extra"]
        self.assertEqual(_success_set(extra), want, "install/cron_roster.json organ-yield service_extra")


class TestTheDocumentedOptInReArmsTheTemplate(unittest.TestCase):
    """Iteration 2, item 3 (HIGH). organ-yield ships AS A TEMPLATE (install/cron_roster.json), so
    `install.sh --with-crons` never re-renders it: on the reference host the live, hand-armed unit still
    says `SuccessExitStatus=0 1` (read by the iteration-1 reflector), so after a landing that only ran
    install.sh a finding (3) would read FAILED and a crash (1) would still read success -- R6's defect,
    surviving on the host it was measured on. The package documents the opt-in that re-arms it (README:
    `WIZARD_CRON_ONLY=organ-yield ... install_crons.sh`); here that exact opt-in -- dry, then WET into a
    scratch home with a logging systemctl double (no manager is touched) -- renders a unit whose
    SuccessExitStatus is exactly {0, EXIT_BROKEN_FOUND}, with 1 absent."""

    def setUp(self):
        self.crons = ROOT / "install" / "install_crons.sh"
        if not self.crons.is_file() or not ORGAN.is_file():
            raise unittest.SkipTest("tree lacks install/install_crons.sh or the organ")
        self.consts = _module_constants()
        SCRATCH.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="oyarm_", dir=str(SCRATCH)))
        self.addCleanup(lambda: shutil.rmtree(str(self.tmp), ignore_errors=True))
        self.home = self.tmp / "home"
        self.brain = self.home / ".claude" / "memory"
        (self.brain / "organs").mkdir(parents=True)
        (self.home / "tmp").mkdir()
        shutil.copy2(str(ORGAN), str(self.brain / "organs" / ORGAN.name))   # the organ beside the brain, as laid
        shim = self.tmp / "shim"
        shim.mkdir()
        (shim / "systemctl").write_text('#!/bin/sh\necho "systemctl $*" >> "$(dirname "$0")/systemctl.log"\nexit 0\n',
                                        encoding="utf-8")
        os.chmod(str(shim / "systemctl"), 0o755)
        self.log = shim / "systemctl.log"
        self.env = {"PATH": str(shim) + ":/usr/local/bin:/usr/bin:/bin", "HOME": str(self.home),
                    "WIZARD_LIBRARY_HOME": str(self.brain), "WIZARD_LIBRARY_DB": str(self.brain / "claude_memory.db"),
                    "WIZARD_ENV_FILE": str(self.home / ".voidloom" / "wizard-library-env"),
                    "WIZARD_PYTHON_BIN": sys.executable, "WIZARD_CRON_ONLY": "organ-yield",
                    "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_CACHE_HOME": str(self.home / ".cache"),
                    "XDG_DATA_HOME": str(self.home / ".local" / "share"),
                    "XDG_STATE_HOME": str(self.home / ".local" / "state"),
                    "TMPDIR": str(self.home / "tmp"), "PYTHONDONTWRITEBYTECODE": "1"}

    def test_the_opt_in_renders_success_exit_status_clean_and_broken_found(self):
        want = {0, self.consts.get("EXIT_BROKEN_FOUND")}
        p = subprocess.run(["bash", str(self.crons), "--dry-run"], capture_output=True, text=True, env=self.env,
                           timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        body, cur = [], False
        for line in p.stdout.splitlines():
            if line.strip() == "DRY-UNIT: wizard-library-organ-yield.service" or line.strip() == "DRY-UNIT: organ-yield.service":
                cur = True
                continue
            if cur and line.startswith("    "):
                body.append(line[4:])
                continue
            cur = False
        self.assertTrue(body, "the opt-in's dry run printed no organ-yield.service block: %r" % p.stdout[-1500:])
        self.assertEqual(_success_set(body), want, "\n".join(body))
        p = subprocess.run(["bash", str(self.crons)], capture_output=True, text=True, env=self.env, timeout=300)
        self.assertEqual(p.returncode, 0, (p.stdout + p.stderr)[-2000:])
        unit = self.home / ".config" / "systemd" / "user" / "wizard-library-organ-yield.service"
        self.assertTrue(unit.is_file(), "the opt-in did not render the unit: %r" % p.stdout[-1500:])
        lines = unit.read_text(encoding="utf-8").splitlines()
        self.assertEqual(_success_set(lines), want, "rendered unit:\n" + "\n".join(lines))
        self.assertNotIn(1, _success_set(lines), "a crash (1) must not read as success")
        first = [l for l in lines if l.startswith("ExecStart=")][0].split("=", 1)[1].split()[0]
        self.assertEqual(first, sys.executable, "the unit must run the interpreter it was given")
        self.assertIn("enable --now wizard-library-organ-yield.timer", self.log.read_text(encoding="utf-8"))

    def test_the_readme_names_the_opt_in_and_the_new_status(self):
        readme = ROOT / "README.md"
        if not readme.is_file():
            self.skipTest("README.md ships but is not laid (root=null): run from the checkout")
        text = readme.read_text(encoding="utf-8")
        self.assertIn("WIZARD_CRON_ONLY=organ-yield", text)
        self.assertIn("SuccessExitStatus=0 %d" % self.consts.get("EXIT_BROKEN_FOUND"), text)


if __name__ == "__main__":
    unittest.main()
