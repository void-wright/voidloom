"""A brain with no lock file that cannot be read says what was measured: its tier cannot be resolved.

With no lock file and no history beside it, the record that tells a lost lock from a brain that never had one is the
brain's own row, and a brain that cannot be read cannot answer. That is not a record of having none, so every reader
refuses -- but it is not a record of having one either, and LOCK_MISSING ("busy or damaged", was_locked true) claimed
one. The refusal is TIER_UNRESOLVED, naming the error the brain gave, with was_locked unknown:
  * saboteur: resolve, a write's lost-lock check, the loader's lock and the front door's store rule refuse or hold the
    brain to a lock on such a brain, and the installers read TIER_UNRESOLVED, which they refuse by name; none reads it
    as lock-less;
  * control: the same brain, readable, reads as a brain that never had a lock; a lost lock still reads LOCK_MISSING.
"""
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
for p in (str(ROOT / "organs"), str(ROOT / "install")):
    if p not in sys.path:
        sys.path.insert(0, p)

import embedder_lock as el  # noqa: E402
import schema_migrator  # noqa: E402


class _Lockless(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="vl_unread_")
        self.addCleanup(self.td.cleanup)
        self.d = Path(self.td.name)
        self.db = self.d / "claude_memory.db"
        out = schema_migrator.apply(str(self.db), ROOT / "migrations")
        self.assertEqual(out.get("failed") or [], [], out)
        self.addCleanup(lambda: os.chmod(self.db, 0o644))

    def unreadable(self):
        os.chmod(self.db, 0)
        if os.access(self.db, os.R_OK):
            self.skipTest("this user reads a mode-0 file (root): the brain cannot be made unreadable here")


class AnUnreadableBrainWithNoLock(_Lockless):
    def test_saboteur_resolve_refuses_tier_unresolved_and_claims_no_lock(self):
        self.unreadable()
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.resolve(self.db)
        e = cm.exception
        self.assertEqual(e.code, "TIER_UNRESOLVED", str(e))
        self.assertIsNone(e.detail.get("was_locked"), e.detail)
        self.assertTrue(e.detail.get("unreadable"))
        self.assertNotIn("busy or damaged", str(e))

    def test_saboteur_a_writes_lost_lock_check_refuses_it(self):
        self.unreadable()
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.refuse_lost_lock(self.db)
        self.assertEqual(cm.exception.code, "TIER_UNRESOLVED")

    def test_saboteur_the_front_doors_store_rule_and_the_loaders_lock_hold_the_brain_to_a_promise(self):
        sys.path.insert(0, str(ROOT))
        from voidloom import _facade
        import embedding_upgrade as eu
        self.unreadable()
        self.assertTrue(_facade._brain_is_locked(str(self.db)))
        with self.assertRaises(el.EmbedderLockError) as cm:
            eu.brain_lock(self.db, tier="nomic")             # never the catalogue entry of a lock-less brain
        self.assertEqual(cm.exception.code, "TIER_UNRESOLVED")

    def test_saboteur_the_installers_read_it_as_unresolved_never_as_lockless(self):
        import brain_is_new
        self.unreadable()
        self.assertEqual(brain_is_new.lost_lock_tier(str(self.db)), "TIER_UNRESOLVED")


class TheSameBrainReadable(_Lockless):
    def test_control_it_reads_as_a_brain_that_never_had_a_lock(self):
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.resolve(self.db)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        self.assertFalse(cm.exception.detail.get("was_locked"))
        el.refuse_lost_lock(self.db)            # no refusal: a brain that never had a lock is legal

    def test_control_a_lost_lock_still_reads_lock_missing(self):
        el.write_lock(self.d, "cpu")
        (self.d / el.LOCK_NAME).unlink()
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.resolve(self.db)
        self.assertEqual((cm.exception.code, cm.exception.detail.get("was_locked")), ("LOCK_MISSING", True))


if __name__ == "__main__":
    unittest.main()
