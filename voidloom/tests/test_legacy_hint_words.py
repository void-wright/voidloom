"""The words beside `legacy_unverified` state only what the brain recorded.

THE CLAIM UNDER TEST
  * a legacy vector carries no stamp under the lock; that is all the brain knows about it. A vector a bypassing writer
    planted after the lock was written is legacy too, so the words never say a legacy vector predates the lock.
"""
from __future__ import annotations

import os
import unittest

from voidloom.tests.test_search_set_foreign_stamps import _Stamped, el, mw


class TheLegacyWords(_Stamped):

    def test_saboteur_a_vector_planted_after_the_lock_is_not_said_to_predate_it(self):
        lock_written = (self.d / el.LOCK_NAME).stat().st_mtime
        mid = self.memory("planted after the lock, past the stamp keeper")     # vector, and no stamp at all
        self.assertIsNone(self.conn.execute(f"SELECT 1 FROM {mw.PROVENANCE_TABLE} WHERE memory_id = ?",
                                            (mid,)).fetchone())
        self.assertGreaterEqual(os.path.getmtime(self.db), lock_written)
        cov = mw.coverage_envelope(self.conn, detail=False)
        hint = cov["legacy_unverified"]["hint"]
        self.assertEqual(cov["legacy_unverified"]["legacy"], 1, cov)
        self.assertNotIn("before", hint)
        self.assertIn("no stamp under this lock", hint)
        self.assertIn("not recorded", hint)


if __name__ == "__main__":
    unittest.main()
