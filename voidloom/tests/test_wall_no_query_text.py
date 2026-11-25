# -*- coding: utf-8 -*-
"""R3: no text of a query is ever written under the brain by a recall. Proved by a byte search of every file there."""
import argparse
import contextlib
import io
import json
from unittest import mock
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, data, unknown_flag

CANARY = "zqxcanaryword"
Q = "gradle build cache daemon " + CANARY


class _R(Case):
    def brain(self, name="b"):
        b = Brain(self.tmp / name)
        self.proc = b.add("gradle build cache warm daemon speeds the pipeline", kind="procedural", project="build")
        p = (self.proc, b.add("gradle daemon memory pressure kills the build", project="build"))
        for i in range(8):
            b.add("orchid watering bark row %s" % ("abcdefgh"[i] * 4), project="garden")
        d = b.add("[Dream synthesis — divergent]\nSource memories: #%d and #%d\n\nThe gradle build cache and the daemon share "
                  "one cause." % p, summary="The gradle build cache and the daemon share one cause.", kind="reflection",
                  derived_from="dream_synthesis", confidence=0.4, project="memory-dreaming")
        b.c.execute("DELETE FROM memory_embeddings_v2 WHERE memory_id=?", (d,))      # a dream's vector is of its BODY
        b.c.execute("INSERT INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?,?)",
                    (d, wb.fake_vec("The gradle build cache and the daemon share one cause.")))
        b.c.execute("INSERT INTO dream_log (dream_type, memory_a_id, memory_b_id, dream_content, dream_memory_id, is_meaningful, "
                    "context_difference) VALUES ('divergent',?,?,'x',?,1,?)", (*p, d, json.dumps({"relation": "connection"})))
        b.c.commit()
        self.assert_no_canary(b)                     # the fixture itself must not hold it
        return b

    def recall(self, b, q=Q, *flags, env=None):
        rc, e, err = wb.full_cli(b.path, "recall", "--query", q, *flags, env=env)
        self.needs(not unknown_flag(err), "recall " + " ".join(flags))
        self.assertEqual(rc, 0, e)
        self.assertTrue(data(e)["results"], "the recall found nothing, so it proves nothing")
        return data(e)

    def where(self, b):
        b.c.commit()
        hits = sorted(n for n, raw in wb.brain_text_files(Path(b.path).parent).items() if CANARY.encode() in raw.lower())
        cols = []
        for (t,) in b.all("SELECT name FROM sqlite_master WHERE type='table'"):
            if t.startswith(("memory_embeddings", "memories_fts")):
                continue
            for r in b.c.execute("PRAGMA table_info(%s)" % t).fetchall():
                if b.one("SELECT 1 FROM %s WHERE CAST(%s AS TEXT) LIKE ? LIMIT 1" % (t, r[1]), ("%%%s%%" % CANARY,)):
                    cols.append("%s.%s" % (t, r[1]))
        return hits, cols

    def assert_no_canary(self, b):
        self.assertEqual(self.where(b), ([], []), "query text was written under the brain")


class Control(_R):
    def test_control_the_byte_search_sees_planted_text(self):
        b = self.brain()
        b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text) VALUES (?, 'recall:x', ?)", (self.proc, Q))
        b.c.commit()
        self.assertEqual(self.where(b)[1], ["access_log.query_text"])
        self.assertTrue(self.where(b)[0])

    def test_control_lite_recall_writes_nothing(self):
        b = self.brain()
        rc, env, _o, err = wb.lite_cli(b.path, "recall", "--query", Q)
        self.assertEqual(rc, 0, err)
        self.assertTrue(data(env)["results"])
        self.assert_no_canary(b)

    def test_control_a_recall_still_leaves_an_access_row_and_bumps_the_count(self):
        b = self.brain()
        self.recall(b)
        self.assertTrue(b.one("SELECT COUNT(*) FROM access_log WHERE access_type LIKE 'recall:%'")[0])
        self.assertTrue(b.one("SELECT MAX(access_count) FROM memories")[0])
        self.assertTrue(b.one("SELECT COUNT(*) FROM access_log WHERE access_type='recall:dream-lead'")[0],
                        "the fixture never reaches the met ledger")

    def test_control_existing_query_text_is_left_as_it_is(self):
        b = self.brain()
        b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text) VALUES (?, 'recall:hybrid', 'an old question')", (self.proc,))
        b.c.commit()
        self.recall(b)
        self.assertEqual(b.one("SELECT COUNT(*) FROM access_log WHERE query_text='an old question'")[0], 1)

    def test_control_quickening_counts_a_person_and_not_a_machine(self):
        import quickening as qk
        from datetime import datetime, timedelta
        got = []
        for name, q in (("person", Q), ("machine", "[cron] " + Q)):
            b = self.brain(name)
            self.recall(b, q)
            got.append(len([e for e in qk._fetch_clean_events(b.c, 7, datetime.utcnow() + timedelta(hours=1))]) > 0)
        self.assertEqual(got, [True, False])

    def test_control_two_recalls_are_two_events_in_the_surfacing_report(self):
        import dream_ledger as dl
        b = self.brain()
        self.recall(b, "gradle cache daemon first question")
        self.recall(b, "gradle cache daemon second question")
        from datetime import datetime, timedelta
        rep = dl.surfacing_report(b.c, datetime.utcnow() + timedelta(minutes=1), days=7.0)
        self.assertIn(2, [v for v in rep.values() if isinstance(v, int)], rep)


class NoText(_R):
    def test_red_hybrid_recall_writes_no_query_text(self):
        b = self.brain()
        self.recall(b)
        self.assert_no_canary(b)

    def test_red_batch_recall_writes_no_query_text(self):
        b = self.brain()
        rc, env, err = wb.full_cli(b.path, "batch-recall", "--items", json.dumps([Q, {"query": Q + " again", "top_k": 3}]))
        self.assertEqual(rc, 0, err)
        self.assert_no_canary(b)

    def test_red_vector_only_legacy_path_writes_no_query_text(self):
        import hybrid_retrieval
        b = self.brain()
        with wb.engine(b.path) as cm, mock.patch.object(cm, "hybrid_retrieval", None), contextlib.redirect_stdout(io.StringIO()) as o:
            cm.cmd_recall(argparse.Namespace(query=Q, top_k=3, threshold=None, category=None, project=None, kind=None, leads=None))
        self.assertTrue(wb.last_json(o.getvalue())["results"])
        self.assert_no_canary(b)

    def test_red_no_environment_switch_turns_it_back_on(self):
        b = self.brain()
        self.recall(b, env={"VOIDLOOM_LOG_QUERY_TEXT": "1"})
        self.assert_no_canary(b)

    def test_red_each_site_alone(self):
        """Every writer this recall reaches, named: one left behind is one leak. (skill_usage_log is not in the spec list.)"""
        b = self.brain()
        self.recall(b)
        self.assertEqual(self.where(b)[1], [])

    def test_red_website_memory_recall_writes_no_query_text(self):
        import website_memory as w
        db = self.tmp / "website" / "website_memory.db"
        db.parent.mkdir()
        vec = lambda t, p="x", **k: wb.fake_vec(t)[:768 * 4]

        def write(conn, mid, text, emb, *a, **k):
            conn.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?,?)", (mid, emb))
        a = dict(content="gradle cache daemon site row", category="insight", tags="", importance=0.5, project=None, context=None, summary=None)
        with mock.patch.object(w, "DB_PATH", db), mock.patch.object(w, "SCHEMA_PATH", wb.ROOT / "migrations" / "schema.sql"), \
                mock.patch.object(w, "_encode", vec), mock.patch.object(w, "_write_vector", write), \
                contextlib.redirect_stdout(io.StringIO()) as o:
            w.cmd_init(argparse.Namespace())
            w.cmd_store(argparse.Namespace(**a))
            w.cmd_recall(argparse.Namespace(query=Q, top_k=3, threshold=None, category=None, project=None))
        self.assertTrue(wb.last_json(o.getvalue())["results"], o.getvalue()[-300:])
        self.assertEqual([n for n, raw in wb.brain_text_files(db.parent).items() if CANARY.encode() in raw], [])

    def test_red_rows_of_one_recall_share_a_random_event_id(self):
        b = self.brain()
        self.needs("event_id" in wb.columns(b.c, "access_log"), "access_log.event_id / access_log.machine")
        self.recall(b)
        self.recall(b, "[cron] " + Q)
        ev = b.all("SELECT event_id, MIN(machine), COUNT(*) FROM access_log WHERE access_type LIKE 'recall:%' GROUP BY event_id")
        self.assertEqual((len(ev), all(e[0] for e in ev), sorted(bool(e[1]) for e in ev)), (2, True, [False, True]), ev)
        self.assertEqual(b.one("SELECT COUNT(*) FROM access_log WHERE query_text IS NOT NULL")[0], 0)

    def test_red_no_log_writes_no_access_row(self):
        b = self.brain()
        self.recall(b, Q, "--no-log")
        self.assertEqual(b.one("SELECT COUNT(*) FROM access_log")[0], 0)
        self.assert_no_canary(b)

    def test_red_facade_no_log(self):
        import inspect
        from voidloom import _facade
        self.needs("no_log" in inspect.signature(_facade.recall).parameters, "_facade.recall(no_log=)")
        b = self.brain()
        with wb.engine(b.path):
            _facade.recall(Q, db_path=b.path, no_log=True)
        self.assertEqual(b.one("SELECT COUNT(*) FROM access_log")[0], 0)


class Detectors(_R):
    def weak(self, b):
        for q in ("a private question about a diagnosis", ) * 3:
            b.c.execute("INSERT INTO access_log (memory_id, access_type, query_text, relevance_score) VALUES (?, 'recall:hybrid', ?, 0.4)", (self.proc, q))
        b.c.commit()

    def test_red_weak_recall_detection_is_retired(self):
        import gap_mapping as gm
        b = self.brain()
        self.weak(b)
        self.assertEqual(gm.detect_weak_recalls(b.c), [])

    def test_red_inventory_makes_nothing_dispatchable_from_private_text(self):
        import gap_mapping as gm
        b = self.brain()
        self.weak(b)
        b.add("Zanzibar Quokka Protocol appears once", tags=("zanzibar-quokka",), project="lonelyproject", importance=0.9)
        with wb.engine(b.path):
            r = gm.run_gap_inventory(b.path)
        rows = b.all("SELECT gap_type, entity FROM gap_inventory")
        self.assertTrue(rows, r)
        self.assertEqual(b.all("SELECT gap_type, entity FROM gap_inventory WHERE auto_research_eligible=1"), [],
                         "private text became a dispatchable row")


if __name__ == "__main__":
    unittest.main()
