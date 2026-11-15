"""A missing lock file is never read as a brain that never had a lock, at the install-time readers too.

THE CLAIM UNDER TEST
  * `install/brain_is_new.py --tier`, install.sh's model reader and its "holds a promise" check, the bootstrap's model
    detector and the front door's store rule ask the record a lock leaves behind (the brain's row, the history beside
    it) before they read anything else. A lock that was written and is gone keeps the tier its record names; one whose
    record names none reads LOCK_MISSING, which the installer refuses by name. Only a brain that never had a lock (or
    whose rollback says it has none) is read from its metadata row, as before.
  * the fixtures plant a metadata row that DISAGREES with the record, so a reader that falls back to metadata is caught.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
for p in (str(ROOT / "organs"), str(ROOT / "install"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import brain_is_new  # noqa: E402
import embedder_lock as el  # noqa: E402
import schema_migrator  # noqa: E402


def _fn(sh: str, name: str) -> str:
    i = sh.index("%s() {" % name)
    j = sh.index("\nPYEOF\n}\n", i) + len("\nPYEOF\n}\n")
    return sh[i:j]


class _Brains(unittest.TestCase):
    """A cpu-locked brain whose metadata row names the qwen table: the lock's record and the metadata disagree."""
    STATE = "lost"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_lostinst_")
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), ROOT / "migrations")
        self.assertEqual(out.get("failed") or [], [], out)
        el.write_lock(self.d, "cpu")
        el.mark_brain(self.db, "cpu")
        c = sqlite3.connect(self.db)
        c.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES ('active_embedding_table', 'memory_embeddings_v3')")
        c.commit()
        c.close()
        if self.STATE in ("lost", "lost_untiered", "never", "rolled_back"):
            (self.d / el.LOCK_NAME).unlink()
        if self.STATE == "lost_untiered":
            (self.d / el.LOCK_HISTORY_NAME).write_text("not a receipt\n", encoding="utf-8")
            self._meta("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
        if self.STATE == "never":
            (self.d / el.LOCK_HISTORY_NAME).unlink()
            self._meta("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
        if self.STATE == "rolled_back":
            (self.d / el.LOCK_HISTORY_NAME).unlink()
            self._meta("UPDATE metadata SET value = '' WHERE key = ?", (el.BRAIN_LOCK_KEY,))

    def _meta(self, sql, args):
        c = sqlite3.connect(self.db)
        c.execute(sql, args)
        c.commit()
        c.close()

    def bin_tier(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "brain_is_new.py"), str(self.db), "--tier"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def install_sh(self, call):
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        script = "%s\n%s\nPY_SYS=%s\nPKG_ROOT=%s\nDB_PATH=%s\n%s\n" % (
            _fn(sh, "hd_embed_model_from_db"), _fn(sh, "hd_brain_embedded"), sys.executable, ROOT, self.db, call)
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def models(self):
        from voidloom import _bootstrap
        return _bootstrap._detect_models(self.db)


class ALostLockKeepsItsRecordedTier(_Brains):
    STATE = "lost"

    def test_saboteur_brain_is_new_reads_the_record_not_the_metadata(self):
        self.assertEqual(self.bin_tier(), "nomic")

    def test_saboteur_install_sh_model_reader_reads_the_record_not_the_metadata(self):
        self.assertEqual(self.install_sh("hd_embed_model_from_db"), "nomic")

    def test_saboteur_a_lost_lock_with_no_vector_is_still_a_promise(self):
        self.assertEqual(self.install_sh("hd_brain_embedded nomic"), "yes")

    def test_saboteur_the_bootstrap_reads_a_lost_lock_not_a_lockless_brain(self):
        m = self.models()
        self.assertEqual((m.get("lock_refusal"), m.get("was_locked"), m.get("present")), ("LOCK_MISSING", True, False), m)
        self.assertNotIn("brain_tier", m)


class ALostLockThatRecordsNoTier(_Brains):
    STATE = "lost_untiered"

    def test_saboteur_brain_is_new_reads_lock_missing(self):
        self.assertEqual(self.bin_tier(), "LOCK_MISSING")

    def test_saboteur_install_sh_reads_lock_missing_and_refuses_it_by_name(self):
        self.assertEqual(self.install_sh("hd_embed_model_from_db"), "LOCK_MISSING")
        sh = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("LOCK_MALFORMED|LOCK_MISSING) echo \"$m\"; return ;;", sh)
        self.assertIn('if [ "$EMBED_MODEL" = "LOCK_MISSING" ]; then', sh)
        ps1 = (ROOT / "install" / "install.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('if ($BrainTier -eq "LOCK_MISSING") { Die ', ps1)


class ABrainThatNeverHadALock(_Brains):
    STATE = "never"

    def test_control_brain_is_new_reads_the_metadata(self):
        self.assertEqual(self.bin_tier(), "qwen")

    def test_control_install_sh_reads_the_metadata(self):
        self.assertEqual(self.install_sh("hd_embed_model_from_db"), "qwen")
        self.assertEqual(self.install_sh("hd_brain_embedded nomic"), "no")

    def test_control_the_bootstrap_reads_a_lockless_brain(self):
        m = self.models()
        self.assertEqual(m.get("brain_tier"), "qwen", m)
        self.assertNotIn("lock_refusal", m)


class ABrainRolledBackToNoLock(_Brains):
    STATE = "rolled_back"

    def test_control_a_rollback_is_a_record_of_having_none(self):
        self.assertEqual(self.bin_tier(), "qwen")
        self.assertEqual(self.install_sh("hd_embed_model_from_db"), "qwen")


class ALockThatIsThere(_Brains):
    STATE = "locked"

    def test_control_the_lock_is_read_first(self):
        self.assertEqual(self.bin_tier(), "nomic")
        self.assertEqual(self.install_sh("hd_embed_model_from_db"), "nomic")
        self.assertEqual(self.install_sh("hd_brain_embedded nomic"), "yes")


class TheStandaloneReaderWithoutTheOrgans(_Brains):
    """brain_is_new.py runs before an environment is built; with no organs beside it, it reads the same two records."""
    STATE = "lost"

    def test_saboteur_the_records_are_read_without_the_lock_module(self):
        lone = self.d / "lone" / "install"
        lone.mkdir(parents=True)
        (lone / "brain_is_new.py").write_bytes((ROOT / "install" / "brain_is_new.py").read_bytes())
        r = subprocess.run([sys.executable, "-B", "-I", str(lone / "brain_is_new.py"), str(self.db), "--tier"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "nomic", r.stderr)
        self._meta("DELETE FROM metadata WHERE key = ?", (el.BRAIN_LOCK_KEY,))
        r = subprocess.run([sys.executable, "-B", "-I", str(lone / "brain_is_new.py"), str(self.db), "--tier"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.stdout.strip(), "LOCK_MISSING", r.stderr)


if __name__ == "__main__":
    unittest.main()
