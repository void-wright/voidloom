"""`embedding_upgrade.py` as a shell gate reads it: exit status, `stats`, `test`, history.

THE CLAIM UNDER TEST
  * a failed `test`, an error envelope, a refusal and a failed pipeline all exit non-zero;
    a success exits 0 (the shell gate's reading);
  * `stats` reports the reader's coverage (matched/total) and the lock, names no model the
    brain does not use, and carries no presence percentage;
  * upgrade history names the model of each run;
  * `test` runs on a locked temp brain with no jina anywhere: an absent snapshot is a SKIP
    with a reason, and it fetches nothing.
No model is loaded.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from voidloom.tests.test_tier_set import TABLES, _connect, _vec_available, make_brain


def _run(eu, *argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = eu.main(list(argv))
    return rc, out.getvalue()


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class ExitStatus(unittest.TestCase):
    def setUp(self):
        import embedding_upgrade as eu
        self.eu = eu
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_saboteur_an_error_envelope_exits_non_zero(self):
        rc, out = _run(self.eu, "--db-path", str(self.dir / "absent.db"), "migrate")
        self.assertEqual(rc, 1, out)
        self.assertEqual(json.loads(out)["status"], "error")

    def test_saboteur_a_failed_self_test_exits_non_zero(self):
        with mock.patch.object(self.eu, "cmd_test", return_value={"status": "failed", "failed": 1}):
            rc, out = _run(self.eu, "--db-path", str(self.dir / "x.db"), "test")
        self.assertEqual(rc, 1, out)

    def test_saboteur_a_refusal_exits_non_zero(self):
        rc, out = _run(self.eu, "--db-path", str(self.dir / "x.db"), "swap")
        self.assertEqual((rc, json.loads(out)["error"]), (1, "SWAP_REFUSED"))

    def test_saboteur_any_coded_exception_is_an_envelope_and_exits_non_zero(self):
        import tier_set
        with mock.patch.object(self.eu, "cmd_stats", side_effect=tier_set.TierRefused("TIER_BUSY", "held")):
            rc, out = _run(self.eu, "--db-path", str(self.dir / "x.db"), "stats")
        self.assertEqual((rc, json.loads(out)["error"]), (1, "TIER_BUSY"))

    def test_saboteur_an_uncoded_exception_still_raises(self):
        with mock.patch.object(self.eu, "cmd_stats", side_effect=ValueError("a bug")):
            with self.assertRaises(ValueError):
                _run(self.eu, "--db-path", str(self.dir / "x.db"), "stats")

    def test_saboteur_a_partial_jina_run_and_a_failed_pipeline_exit_non_zero(self):
        for result in ({"status": "completed_with_errors", "errors": 3},
                       {"pipeline_status": "failed_at_re_embed"},
                       {"pipeline_status": "failed_at_ab_compare"}):
            with mock.patch.object(self.eu, "cmd_stats", return_value=result):
                rc, _o = _run(self.eu, "--db-path", str(self.dir / "x.db"), "stats")
            self.assertEqual(rc, 1, result)

    def test_control_a_success_exits_zero(self):
        for result in ({"status": "success"}, {"status": "ok"}, {"pipeline_status": "completed"}):
            with mock.patch.object(self.eu, "cmd_stats", return_value=result):
                rc, _o = _run(self.eu, "--db-path", str(self.dir / "x.db"), "stats")
            self.assertEqual(rc, 0, result)

    def test_saboteur_full_refuses_before_any_fetch_on_a_brain_that_is_not_jina(self):
        for tier in ("nomic", "qwen"):
            d = Path(tempfile.mkdtemp(dir=self.dir))
            db = make_brain(d, tier)
            for locked in (True, False):
                if not locked:
                    (d / "embedder.lock.json").unlink()
                    (d / "embedder.lock.history.jsonl").unlink()
                with mock.patch.object(self.eu, "cmd_download", side_effect=AssertionError("it fetched")):
                    with self.assertRaises(self.eu.VerbRefused) as cm:
                        self.eu.cmd_full(type("A", (), {"db_path": str(db), "batch_size": 2})())
                self.assertEqual(cm.exception.code, "JINA_PIPELINE_ONLY")

    def test_saboteur_full_stops_when_the_comparison_errors(self):
        db = make_brain(self.dir, "jina")
        A = type("A", (), {"db_path": str(db), "batch_size": 2})
        with mock.patch.object(self.eu, "cmd_download", return_value={}), \
                mock.patch.object(self.eu, "cmd_migrate", return_value={}), \
                mock.patch.object(self.eu, "cmd_re_embed", return_value={"status": "ok"}), \
                mock.patch.object(self.eu, "cmd_ab_compare", return_value={"status": "error", "message": "m"}):
            self.assertEqual(self.eu.cmd_full(A())["pipeline_status"], "failed_at_ab_compare")


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class Stats(unittest.TestCase):
    def setUp(self):
        import embedding_upgrade as eu
        self.eu = eu
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def _stats(self, db):
        return self.eu.cmd_stats(type("A", (), {"db_path": str(db)})())

    def test_control_locked_brain_reports_matched_over_total_and_the_lock(self):
        db = make_brain(self.dir, "qwen")
        r = self._stats(db)
        self.assertEqual((r["lock"]["tier"], r["lock"]["vec_table"]), ("gpu", "memory_embeddings_v3"))
        self.assertEqual(r["coverage"]["basis"], "lock")
        self.assertEqual((r["coverage"]["matched"], r["coverage"]["total"]), (5, 5))
        self.assertEqual(r["vectors_in_lock_table"], 7, "a raw count, archived rows included")

    def test_saboteur_no_install_is_told_about_a_model_it_does_not_use(self):
        import embedding_upgrade as eu
        db = make_brain(self.dir, "nomic")
        text = json.dumps(self._stats(db), default=str)
        self.assertNotIn(eu.JINA_MODEL_NAME, text)
        self.assertNotIn("coverage_v1_pct", text)
        self.assertNotIn("coverage_v2_pct", text)

    def test_control_jina_brain_reads_without_a_fault(self):
        db = make_brain(self.dir, "jina")
        r = self._stats(db)
        self.assertEqual(r["active_table"], "memory_embeddings_v2")
        self.assertEqual(r["lock"]["present"], False)
        self.assertIsNone(r["lock_error"], "no lock on a jina brain is not a fault")
        self.assertEqual(r["coverage"]["basis"], "lockless")

    def test_saboteur_a_malformed_lock_is_a_fault_and_not_absent(self):
        db = make_brain(self.dir, "nomic")
        (self.dir / "embedder.lock.json").write_text("{nope")
        r = self._stats(db)
        self.assertEqual(r["lock"]["error"], "LOCK_MALFORMED")
        self.assertIn("LOCK_MALFORMED", r["lock_error"])

    def test_control_history_names_the_model_of_each_run(self):
        db = make_brain(self.dir, "nomic")
        c = _connect(db)
        c.execute("CREATE TABLE embedding_upgrade_log (id INTEGER PRIMARY KEY, started_at TEXT, completed_at TEXT, "
                  "model_name TEXT NOT NULL, embedding_dim INTEGER, memories_processed INTEGER, "
                  "memories_total INTEGER, status TEXT, error_message TEXT, notes TEXT)")
        c.execute("INSERT INTO embedding_upgrade_log (started_at, model_name, status) VALUES ('t', 'a/model', 'completed')")
        c.commit(); c.close()
        self.assertEqual(self._stats(db)["recent_upgrade_log"][0]["model"], "a/model")


@unittest.skipUnless(_vec_available(), "sqlite_vec absent on this host: NOT MEASURED")
class SelfTest(unittest.TestCase):
    def setUp(self):
        import embedding_upgrade as eu
        self.eu = eu
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self._hub = mock.patch.dict(os.environ, {"HF_HUB_CACHE": str(self.dir / "hub"), "HF_HUB_OFFLINE": "1"})
        self._hub.start()

    def tearDown(self):
        self._hub.stop()
        self._td.cleanup()

    def test_control_a_cold_host_skips_the_model_test_and_passes_without_jina(self):
        def no_jina():
            raise AssertionError("the self-test must not load jina")
        with mock.patch.object(self.eu, "_get_jina_model", no_jina):
            r = self.eu.cmd_test(type("A", (), {"db_path": "unused"})())
        self.assertEqual(r["status"], "success", r)
        self.assertEqual(r["failed"], 0)
        self.assertEqual(r["skipped"], 1)
        skipped = [t for t in r["tests"] if t["status"] == "SKIP"]
        self.assertEqual(skipped[0]["name"], "re_embed_on_locked_brain")
        self.assertIn("not in this host's HF cache", skipped[0]["message"])
        self.assertIn("unit_of_work_before", [t["name"] for t in r["tests"] if t["status"] == "PASS"])

    def test_control_a_warm_host_re_embeds_the_locked_brain_through_the_writer(self):
        import embedder_lock as el
        import tier_set
        from voidloom.tests.test_tier_set import fake_encode, fake_load
        real_snap = el.snapshot_dir_for
        seams = (tier_set._load, tier_set._encode, self.eu.encode_locked)
        tier_set._load, tier_set._encode = fake_load, fake_encode
        self.eu.encode_locked = lambda t, *, is_query, lock: fake_encode([t], lock)[0]
        el.snapshot_dir_for = lambda *a, **k: self.dir
        try:
            r = self.eu.cmd_test(type("A", (), {"db_path": "unused"})())
        finally:
            el.snapshot_dir_for = real_snap
            tier_set._load, tier_set._encode, self.eu.encode_locked = seams
            el.clear_loaded()
        names = {t["name"]: t["status"] for t in r["tests"]}
        self.assertEqual(r["failed"], 0, r)
        self.assertEqual(r["skipped"], 0, r)
        for n in ("re_embed_completes", "re_embed_processed_two", "coverage_two_of_two", "no_unstamped_vectors",
                  "stamp_carries_lock_hash", "knn_over_search_set", "unit_of_work_after", "second_run_is_idempotent"):
            self.assertEqual(names.get(n), "PASS", n)


if __name__ == "__main__":
    unittest.main()
