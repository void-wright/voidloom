"""A lock that was written and is gone is a defect; a brain that never had one is not.

THE CLAIM UNDER TEST
  * history beside the database and no lock: `doctor`, `status` and the reader report LOCK_MISSING,
    the brain is not healthy, and nothing searches the whole table in the lock's place;
  * no history and no lock stays a legal pre-lock brain, and so does a brain a rollback took back to
    having no lock, and one whose only receipt is a re-embed: those histories never promised a lock;
  * the refusal names the tier and a command, and that command repairs the brain.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from voidloom.tests.test_doctor_brain_verdict import JINA, JINA_DIM, _Brain, cli, el, mw

ROOT = Path(__file__).resolve().parents[2]


class _Lost(_Brain):
    def lose_the_lock(self):
        (self.d / el.LOCK_NAME).unlink()

    def history(self, *receipts):
        with open(self.d / el.LOCK_HISTORY_NAME, "a", encoding="utf-8") as h:
            for r in receipts:
                h.write(json.dumps(r) + "\n")

    def status(self):
        env = cli.verb_status(argparse.Namespace(db=str(self.db), json=True))
        self.assertEqual(env["status"], "ok", env)
        return env["data"]


class LockWrittenThenGone(_Lost):
    def setUp(self):
        super().setUp()
        self.memory(whash=self.h)
        self.memory(whash=self.h)
        self.lose_the_lock()

    def test_saboteur_doctor_reads_a_typed_defect_and_not_healthy(self):
        d = self.doctor()
        coh = self.coherence(d)
        self.assertIs(d["brain_healthy"], False, d["reason"])
        self.assertIn("LOCK_MISSING", d["reason"])
        self.assertEqual(coh["refusal"]["error"], "LOCK_MISSING")
        self.assertIs(coh["lockless"], False)

    def test_saboteur_status_reports_the_refusal_and_no_lockless_note(self):
        data = self.status()
        self.assertIsNone(data["lock"]["present"], data["lock"])
        self.assertEqual(data["lock"]["refusal"], "LOCK_MISSING")
        self.assertEqual(data["coverage"]["error"], "LOCK_MISSING")

    def test_saboteur_the_reader_refuses_and_never_searches_the_whole_table(self):
        with self.assertRaises(el.EmbedderLockError) as cm:
            mw.provenance_filter(self.conn)
        self.assertEqual(cm.exception.code, "LOCK_MISSING")
        env = mw.coverage_envelope(self.conn)
        self.assertEqual((env["ok"], env["error"], env["matched"]), (False, "LOCK_MISSING", 0), env)

    def test_saboteur_the_refusal_names_the_tier_and_a_command(self):
        with self.assertRaises(el.EmbedderLockError) as cm:
            el.resolve(self.db)
        self.assertEqual(cm.exception.detail["tier"], "cpu")
        self.assertIn("init --tier cpu", cm.exception.detail["retry"][0])

    def test_saboteur_the_derived_report_does_not_call_it_a_brain_with_no_lock(self):
        self.assertNotIn("no lock on this brain", mw.derived_layer_report(self.conn)["basis"])

    def test_control_rewriting_the_lock_heals_it(self):
        self.assertIs(self.doctor()["brain_healthy"], False)
        el.write_lock(self.d, "cpu")
        self.assertIs(self.doctor()["brain_healthy"], True)

    def test_saboteur_keyword_recall_reports_the_lock_as_gone(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("WIZARD_")}
        env.update(WIZARD_LIBRARY_DB=str(self.db), HOME=str(self.d))
        r = subprocess.run([sys.executable, str(ROOT / "organs" / "claude_memory_lite.py"), "stats"], cwd=str(self.d),
                           env=env, capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["coverage"]["matched"], 0, out["coverage"])
        self.assertIn("LOCK_MISSING", out["coverage"]["basis"])
        self.assertIn("was locked", out["coverage"]["basis"])


class NoPromiseWasMade(_Lost):
    LOCK = None

    def test_control_no_history_and_no_lock_is_a_legal_brain(self):
        self.memory(whash="unresolved")
        self.assertIsNone(el.lost_lock(self.d))
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertIs(self.coherence(d)["lockless"], True)
        self.assertIs(self.status()["lock"]["present"], False)

    def test_control_a_rollback_to_no_lock_stays_legal(self):
        self.memory(whash="unresolved")
        self.history({"action": "tier-set", "to": {"tier": "gpu"}},
                     {"action": "tier-rollback", "restored_lock": False, "restored_vec_table": self.TABLE})
        self.assertIsNone(el.lost_lock(self.d))
        self.assertIs(self.doctor()["brain_healthy"], True)

    def test_control_a_receipt_after_that_rollback_says_nothing_about_the_lock(self):
        self.history({"action": "init", "tier": "cpu"}, {"action": "tier-rollback", "restored_lock": False},
                     {"action": "tier-cleanup"})
        self.assertIsNone(el.lost_lock(self.d))

    def test_control_a_re_embed_receipt_on_a_brain_that_never_had_a_lock(self):
        self.memory(whash="unresolved")
        self.history({"action": "re-embed", "to": {"tier": "cpu"}})
        self.assertIsNone(el.lost_lock(self.d))
        self.assertIs(self.doctor()["brain_healthy"], True)

    def test_saboteur_a_rollback_that_restored_a_lock_promises_one(self):
        self.history({"action": "init", "tier": "cpu"}, {"action": "tier-set", "to": {"tier": "gpu"}},
                     {"action": "tier-rollback", "restored_lock": True})
        self.assertEqual(el.lost_lock(self.d), {"tier": "gpu"})

    def test_saboteur_a_history_nothing_can_read_still_proves_a_lock(self):
        (self.d / el.LOCK_HISTORY_NAME).write_text("{torn", encoding="utf-8")
        self.assertEqual(el.lost_lock(self.d), {"tier": None})

    def test_control_an_empty_history_proves_nothing(self):
        (self.d / el.LOCK_HISTORY_NAME).write_text("", encoding="utf-8")
        self.assertIsNone(el.lost_lock(self.d))


class AJinaBrain(_Lost):
    LOCK = None
    TABLE, DIM = JINA, JINA_DIM

    def setUp(self):
        super().setUp()
        self.conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {JINA} USING vec0("
                          f"memory_id INTEGER PRIMARY KEY, embedding FLOAT[{JINA_DIM}])")
        self.conn.commit()

    def test_control_a_jina_brain_with_no_history_is_healthy(self):
        self.memory(whash="unresolved")
        d = self.doctor()
        self.assertIs(d["brain_healthy"], True, d["reason"])
        self.assertEqual(mw.provenance_filter(self.conn).vec_table, JINA)


if __name__ == "__main__":
    unittest.main()
