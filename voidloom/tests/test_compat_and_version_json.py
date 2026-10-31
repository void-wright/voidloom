"""COMPAT.json (`voidloom.compat/1`) is derived from the tree, and `voidloom --version --json` reports the same
CLI surface it records.

A program that installs this package reads COMPAT.json BEFORE it writes anything: it refuses a package that ships
none, a version it would downgrade, a Python outside the range, and a roster that disagrees with the unit set.
  * control: the shipped COMPAT.json equals tools/build_compat.py's derivation (every field but schema_sha256,
    which needs a model-backed fresh brain: `build_compat.py --check --schema` rebuilds it);
  * control: a fresh brain hashes to schema_sha256, and a brain carrying only schema.sql reaches the same hash
    after the migrator and `init` (which is what oldest_migratable_schema = [] claims);
  * control: its fields have the shapes an installer reads (schema, a PEP 440 version, a parseable range that admits
    3.12, a string unit list per profile) and the unit set IS the roster's;
  * saboteur: a copy with a unit added, or a version moved, reads DRIFT and exits 1;
  * control: `--version --json` prints the version and cli_surface_version, and equals COMPAT's;
  * saboteur: one option added to one verb moves cli_surface_version; plain `--version` keeps its text line;
  * control: no field names a commit or what an install lays; install_verb_contract is the installers' option
    surface as a version (saboteur: an option added to either installer moves it; an unreadable loop fails closed).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))


def _run(*argv, timeout=180):
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, *argv], capture_output=True, text=True, cwd=str(ROOT), env=env,
                          timeout=timeout)


class CompatIsDerived(unittest.TestCase):
    def setUp(self):
        self.compat = json.loads((ROOT / "COMPAT.json").read_text(encoding="utf-8"))

    def test_control_the_shipped_file_equals_its_derivation(self):
        r = _run(str(ROOT / "tools" / "build_compat.py"), "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-800:])
        self.assertIn("CLEAN", r.stdout)

    def test_control_the_fields_an_installer_reads(self):
        c = self.compat
        self.assertEqual(c["schema"], "voidloom.compat/1")
        self.assertRegex(c["version"], r"^\d+\.\d+\.\d+([ab]|rc)?\d*$")
        self.assertRegex(c["schema_produced"]["schema_sha256"] or "", r"^[0-9a-f]{64}$")
        self.assertEqual(c["schema_produced"]["migrations"],
                         sorted(p.name for p in (ROOT / "migrations").glob("migration_*.sql")))
        self.assertTrue(set(c["oldest_migratable_schema"]["migrations"]) <= set(c["schema_produced"]["migrations"]))
        m = re.fullmatch(r">=3\.(\d+)", c["python_range"])
        self.assertIsNotNone(m, c["python_range"])
        self.assertLessEqual(int(m.group(1)), 12)                      # admits the 3.12 a clean room uses
        roster = sorted(json.loads((ROOT / "install" / "cron_roster.json").read_text(encoding="utf-8"))["units"])
        for prof in ("personal", "workbench"):
            us = c["profiles"][prof]["unit_set"]
            self.assertTrue(us and all(isinstance(u, str) for u in us))
            self.assertEqual(us, roster)

    def _tampered(self, mutate):
        doc = json.loads(json.dumps(self.compat))
        mutate(doc)
        td = tempfile.TemporaryDirectory(prefix="vl_compat_t_")
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "COMPAT.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        return _run(str(ROOT / "tools" / "build_compat.py"), "--check", "--compat", str(p))

    def test_saboteur_a_unit_the_roster_lacks_reads_drift(self):
        r = self._tampered(lambda d: d["profiles"]["workbench"]["unit_set"].append("voidloom-extra"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("DRIFT: profiles", r.stdout)

    def test_saboteur_a_moved_version_reads_drift(self):
        r = self._tampered(lambda d: d.__setitem__("version", "9.9.9"))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("version", r.stdout)


class CompatNamesNoCommitAndNoLay(unittest.TestCase):
    """COMPAT carries only what the package derives from its own tree. The commit is named by the consumer's pin (a
    file cannot hold the hash of the commit it sits in), and what an install lays is measured by the consumer, so
    neither may appear here; install_verb_contract is the installers' option surface, as a version."""

    def setUp(self):
        self.compat = json.loads((ROOT / "COMPAT.json").read_text(encoding="utf-8"))
        import build_compat
        self.bc = build_compat

    def test_control_no_field_names_a_commit_or_a_lay(self):
        text = json.dumps(self.compat)
        self.assertIsNone(re.search(r"\b[0-9a-f]{40}\b", text), "a commit-shaped hash in COMPAT.json")
        for key in ("referent", "commit", "write_set_hash", "model_cache_layout"):
            self.assertNotIn(key, text)
        self.assertRegex(self.compat["install_verb_contract"], r"^install-verb/1:[0-9a-f]{16}$")
        self.assertEqual(self.compat["install_verb_contract"], self.bc.install_verb_contract(ROOT))

    def _copy_installers(self, td):
        root = Path(td)
        (root / "install").mkdir()
        for rel in ("install.sh", "install/install.ps1"):
            (root / rel).write_bytes((ROOT / rel).read_bytes())
        return root

    def test_saboteur_an_option_added_to_an_installer_moves_the_contract(self):
        before = self.bc.install_verb_contract(ROOT)
        with tempfile.TemporaryDirectory(prefix="vl_ivc_") as td:
            root = self._copy_installers(td)
            sh = root / "install.sh"
            src = sh.read_text(encoding="utf-8")
            sh.write_text(src.replace("        --dry-run)      DRY_RUN=1 ;;",
                                      "        --dry-run)      DRY_RUN=1 ;;\n        --planted-opt) : ;;", 1),
                          encoding="utf-8")
            self.assertNotEqual(self.bc.install_verb_contract(root), before)
            sh.write_text(src, encoding="utf-8")
            ps = root / "install" / "install.ps1"
            ps.write_text(ps.read_text(encoding="utf-8").replace("    [switch]$DryRun,",
                                                                 "    [switch]$DryRun,\n    [switch]$Planted,", 1),
                          encoding="utf-8")
            self.assertNotEqual(self.bc.install_verb_contract(root), before)

    def test_saboteur_an_unreadable_option_loop_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="vl_ivc_") as td:
            root = self._copy_installers(td)
            sh = root / "install.sh"
            sh.write_text(sh.read_text(encoding="utf-8").replace('for arg in "$@"; do', 'for a in "$@"; do', 1),
                          encoding="utf-8")
            with self.assertRaises(SystemExit):
                self.bc.install_verb_contract(root)


try:
    import sqlite_vec  # noqa: F401
    HAVE_VEC = True
except Exception:
    HAVE_VEC = False


@unittest.skipUnless(HAVE_VEC, "sqlite_vec is not importable here: no brain can be built")
class CompatSchemaIsMeasured(unittest.TestCase):
    """schema_sha256 and oldest_migratable_schema are claims about brains: build the brains and hash them."""

    def test_control_a_fresh_brain_hashes_to_schema_sha256(self):
        r = _run(str(ROOT / "tools" / "build_compat.py"), "--check", "--schema", timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-800:])

    def test_control_a_schema_sql_only_brain_migrates_to_the_same_schema(self):
        import shutil
        import sqlite3
        import build_compat as bc
        with tempfile.TemporaryDirectory(prefix="vl_compat_old_") as td:
            brain = Path(td) / "brain"
            shutil.copytree(ROOT, brain, ignore=shutil.ignore_patterns(
                ".git", "tests", "__pycache__", "*.db", "*.db-*", "embedder.lock*.json", ".pytest_boxes"))
            db = brain / "claude_memory.db"
            c = sqlite3.connect(str(db))
            c.executescript((ROOT / "migrations" / "schema.sql").read_text(encoding="utf-8"))
            c.commit()
            c.close()
            env = {**os.environ, "HOME": td, "WIZARD_LIBRARY_DB": str(db), "WIZARD_EMBED_MODEL": "nomic",
                   "HF_HUB_OFFLINE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
            for argv in (["organs/schema_migrator.py", "apply", "--db", str(db), "--json"],
                         ["organs/claude_memory.py", "init"]):
                r = subprocess.run([sys.executable] + argv, cwd=str(brain), env=env, capture_output=True,
                                   text=True, timeout=300)
                self.assertEqual(r.returncode, 0, (argv, r.stdout[-400:], r.stderr[-400:]))
            compat = json.loads((ROOT / "COMPAT.json").read_text(encoding="utf-8"))
            self.assertEqual(compat["oldest_migratable_schema"]["migrations"], [])
            self.assertEqual(bc.schema_sha256(db), compat["schema_produced"]["schema_sha256"])


class VersionJsonCarriesTheSurface(unittest.TestCase):
    def test_control_version_json_matches_compat(self):
        r = _run("-m", "voidloom", "--version", "--json")
        self.assertEqual(r.returncode, 0, r.stderr[-800:])
        env = json.loads(r.stdout.strip().splitlines()[-1])
        from voidloom import __version__
        compat = json.loads((ROOT / "COMPAT.json").read_text(encoding="utf-8"))
        self.assertEqual((env["status"], env["version"]), ("ok", __version__))
        self.assertRegex(env["cli_surface_version"], r"^cli-surface/1:[0-9a-f]{16}$")
        self.assertEqual(env["cli_surface_version"], compat["cli_surface_version"])

    def test_control_plain_version_keeps_its_text_line(self):
        r = _run("-m", "voidloom", "--version")
        from voidloom import __version__
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"voidloom {__version__}"))

    def test_saboteur_one_added_option_moves_the_surface(self):
        from voidloom import cli
        p = cli.build_parser()
        before = cli.cli_surface_version(p)
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                a.choices["recall"].add_argument("--p9-planted-option")
        self.assertNotEqual(cli.cli_surface_version(p), before)
        self.assertEqual(cli.cli_surface_version(), before)              # a fresh parser is unmoved


if __name__ == "__main__":
    unittest.main()
