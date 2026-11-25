# -*- coding: utf-8 -*-
"""R2 + the late-mark triggers: no descendant of a marked row is unmarked by any writer, a clean twin stays clean, an
edge_inference edge is NOT descent, and a child is never worth more than the rows it came from."""
import argparse
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, data

from unittest import mock

WORDS = "gradle cache daemon memory pressure kills the build lane"
TRUE_DERIVATIONS = ("consolidation", "reflection", "distillation", "dream_synthesis", "council_deliberation", "manual_derivation")


def plant(b, n, marked=0, importance=0.5, words=WORDS, **kw):
    ids = [b.add("%s variant %s" % (words, "abcdefghij"[i] * 4), importance=importance, **kw) for i in range(n)]
    for i in ids[:marked]:
        b.mark(i)
    return ids


def rcfg(**kw):
    import dream_pipeline as dp
    cfg = dict(getattr(dp, "DEFAULT_CONFIG", {}))
    cfg.update(_synthesis_mouth=lambda l, s: "One shared theme about the build lane.", _encoder=wb.fake_vec,
               raptor_min_cluster_size=3, raptor_max_clusters=5, raptor_churn_min_new_members=1,
               playbook_min_occurrences=2, playbook_min_sequence_len=2, playbook_evidence_bar=0.1)
    cfg.update(kw)
    return cfg


# one writer per entry: (parents -> child id), each through the organ's own function
def w_consolidate(b, marked):
    import sleep_loop as sl
    ids = plant(b, 4, marked)
    r = sl.consolidate_episodics(b.c, min_cluster_size=3, similarity_threshold=0.5)
    return ids, r[0]["consolidated_memory_id"]


def w_reflect(b, marked):
    import sleep_loop as sl
    ids = plant(b, 5, marked, importance=0.3)
    return ids, sl.generate_reflections(b.c, min_unreflected=3)[0]


def w_dream(b, marked):
    import memory_dreaming as md
    ids = plant(b, 2, marked, importance=0.3)
    return ids, md._store_dream_as_reflection(
        b.c, "RELATION: connection\nThe build cache and the daemon pressure share one cause in the lane.", ids[0], ids[1],
        "divergent", encoder=wb.fake_vec)


def w_raptor(b, marked):
    import dream_pipeline as dp
    ids, top = plant(b, 4, marked, importance=0.3), b.top()
    dp.phase_raptor_synthesis(b.c, rcfg(), None)
    return ids, b.new_ids(top)[0]


def w_playbook(b, marked):
    import dream_pipeline as dp
    ids, top = plant(b, 6, marked, importance=0.3, kind="procedural"), b.top()
    dp.phase_playbook_emission(b.c, rcfg(), None)
    return ids, b.new_ids(top)[0]


def w_write_memory(b, marked):
    import memory_write as mw
    ids = plant(b, 3, marked, importance=0.3)
    r = mw.write_memory(b.c, content="A distilled line over three rows", category="insight", importance=0.9,
                        encoder=wb.fake_vec, sources=ids, transformation="distillation")
    b.c.commit()
    return ids, r.memory_id


WRITERS = [("consolidate", w_consolidate), ("reflections", w_reflect), ("dream", w_dream), ("raptor", w_raptor),
           ("playbook", w_playbook), ("write_memory", w_write_memory)]


class _W(Case):
    def writer(self, name, marked):
        b = Brain(self.tmp / ("%s%d" % (name, marked)))
        with wb.engine(b.path):
            ids, child = dict(WRITERS)[name](b, marked)
        self.assertIsNotNone(child)
        return b, ids, child


class Control(_W):
    def test_control_marked_dream_raptor_playbook_inherit_already(self):
        for w in ("dream", "raptor", "playbook"):
            b, _ids, child = self.writer(w, 1)
            self.assertTrue(b.marked(child), w)

    def test_control_clean_twin_of_every_writer_stays_clean(self):
        got = {w: (lambda t: t[0].marked(t[2]))(self.writer(w, 0)) for w, _f in WRITERS}
        self.assertEqual(got, {w: False for w, _f in WRITERS})

    def test_control_marking_a_stranger_does_not_taint(self):
        got = {}
        for w, _f in WRITERS:
            b, ids, child = self.writer(w, 0)
            stranger = b.add("an unrelated note on violin rosin", project="elsewhere", kind="semantic")
            b.mark(stranger)
            got[w] = (b.marked(child), [b.marked(i) for i in ids].count(True))
        self.assertEqual(set(got.values()), {(False, 0)}, got)

    def test_control_edge_inference_is_not_descent(self):
        """An owner row an inferred edge links to a marked row stays the owner's: at the edge, and at a late mark."""
        b = Brain(self.tmp)
        owner, web, later = b.add("owner row"), b.add("web row", marked=True), b.add("row marked later")
        for src in (web, later):
            b.c.execute("INSERT INTO memory_provenance (derived_memory_id, source_memory_id, transformation_type) "
                        "VALUES (?,?,'edge_inference')", (owner, src))
        b.mark(later)
        b.c.execute("INSERT INTO memory_links (source_id, target_id, link_type) VALUES (?,?,'related')", (owner, web))
        b.c.commit()
        self.assertFalse(b.marked(owner))

    def test_control_parents_are_never_tainted_by_a_marked_child(self):
        b = Brain(self.tmp)
        p, c = b.add("parent"), b.add("child")
        b.c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (?,?,1)", (c, p))
        b.mark(c)
        self.assertFalse(b.marked(p))


def _inherit(name):
    def t(self):
        b, _ids, child = self.writer(name, 1)
        self.assertTrue(b.marked(child), "%s wrote an unmarked child of a marked parent" % name)
    return t


def _late(name):
    def t(self):
        b, ids, child = self.writer(name, 0)
        self.assertFalse(b.marked(child))
        b.mark(ids[-1])
        self.assertTrue(b.marked(child), "%s: a parent marked later left its child unmarked" % name)
    return t


def _importance(name):
    def t(self):
        bad = []
        for marked in (0, 1):
            b, ids, child = self.writer(name, marked)
            b.c.execute("SELECT 1")
            mean = sum(b.importance(i) for i in ids) / len(ids)
            if b.importance(child) > mean + 1e-9:
                bad.append("marked parents=%d: child %.3f > parents' mean %.3f" % (marked, b.importance(child), mean))
        self.assertEqual(bad, [], name)
    return t


class Inherit(_W):
    pass


for _n in ("consolidate", "reflections", "write_memory"):
    setattr(Inherit, "test_red_%s_child_is_marked" % _n, _inherit(_n))
for _n, _f in WRITERS:
    setattr(Inherit, "test_red_%s_late_mark" % _n, _late(_n))
    setattr(Inherit, "test_red_%s_importance_capped" % _n, _importance(_n))


class Capped(_W):
    def test_red_marked_child_capped_by_its_marked_parents(self):
        """A low-importance outside parent among important ones: the child is worth no more than the outside part."""
        import sleep_loop as sl
        b = Brain(self.tmp)
        ids = plant(b, 4, importance=0.9)
        b.c.execute("UPDATE memories SET importance=0.2 WHERE id=?", (ids[0],))
        b.mark(ids[0])
        with wb.engine(b.path):
            child = sl.consolidate_episodics(b.c, min_cluster_size=3, similarity_threshold=0.5)[0]["consolidated_memory_id"]
        self.assertLessEqual(b.importance(child), 0.2 + 1e-9)

    def test_red_update_content_rewrite_stays_capped(self):
        """`update --content --importance` over a derived row of marked parents: the rewrite obeys the parents' cap."""
        b, ids, child = self.writer("dream", 1)
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(io.StringIO()):
            cm.cmd_update(argparse.Namespace(id=child, content="rewritten outside text", summary=None, importance=0.99,
                                             category=None, kind=None, tags=None))
        self.assertEqual(b.one("SELECT content FROM memories WHERE id=?", (child,))[0], "rewritten outside text")
        self.assertTrue(b.marked(child))
        self.assertLessEqual(b.importance(child), 0.3 + 1e-9)

    def test_red_raptor_rewrite_stays_capped(self):
        import dream_pipeline as dp
        b = Brain(self.tmp)
        top0 = plant(b, 4, importance=0.6)[-1]
        with wb.engine(b.path):
            dp.phase_raptor_synthesis(b.c, rcfg(), None)
            node = b.new_ids(top0)[0]
            plant(b, 3, marked=3, importance=0.1, words="second wave cache daemon build lane")
            dp.phase_raptor_synthesis(b.c, rcfg(_synthesis_mouth=lambda l, s: "REWRITTEN theme."), None)
        self.assertEqual(b.one("SELECT content FROM memories WHERE id=?", (node,))[0], "REWRITTEN theme.")
        self.assertTrue(b.marked(node))
        self.assertLessEqual(b.importance(node), 0.1 + 1e-9, "the rewritten node outranks its marked members")


class NoNamedParents(_W):
    def test_red_whisper_digest_is_low_trust(self):
        import whisper_network as wn
        b = Brain(self.tmp)
        b.c.execute("INSERT INTO whisper_observations (observation, observation_type) VALUES ('restated web text','insight')")
        b.c.commit()
        top = b.top()
        with wb.engine(b.path), mock.patch.object(wn, "_thinker_week", lambda *a, **k: {"line": "", "alerts": [], "checked": False}):
            wn.generate_weekly_digest(b.path)
        df, conf = b.one("SELECT derived_from, confidence FROM memories WHERE id=?", (b.new_ids(top)[0],))
        self.assertTrue(df)
        self.assertLessEqual(conf, 0.50)

    def test_red_arcane_manifest_is_low_trust(self):
        import arcane_manifest as am
        b = Brain(self.tmp)
        b.c.execute("INSERT INTO arcane_manifests (quarter, year, quarter_number, manifest_content, sections, word_count) "
                    "VALUES ('2026-Q4',2026,4,'prose restating notable dreams','[]',5)")
        b.c.commit()
        top = b.top()
        with wb.engine(b.path):
            am.finalize_manifest(b.path, b.one("SELECT MAX(id) FROM arcane_manifests")[0])
        df, conf = b.one("SELECT derived_from, confidence FROM memories WHERE id=?", (b.new_ids(top)[0],))
        self.assertTrue(df, "a synthesis row carries no sentinel and no parent")
        self.assertLessEqual(conf, 0.50)

    def test_red_dream_restamp_of_a_marked_endpoint_is_tainted(self):
        """dream_ledger.apply rewrites a legacy dream row; its endpoint is marked (executed census: the child stayed 0)."""
        import dream_ledger as dl
        b = Brain(self.tmp)
        a, c = plant(b, 2, marked=1)
        d = b.add("[Dream synthesis — divergent]\nSource memories: #%d and #%d\n\nThe cache and the daemon share one cause." % (a, c),
                  summary="memories #%d + #%d" % (a, c), kind="reflection", project="memory-dreaming", tags=("dream",))
        with wb.engine(b.path):
            dl.apply(b.c, dl.plan(b.c), ["restamp"], str(self.tmp / "pre.json"), encoder=wb.fake_vec, stamp_date="20261125")
        self.assertEqual(b.one("SELECT derived_from FROM memories WHERE id=?", (d,))[0], "dream_synthesis", "the restamp did not run")
        self.assertTrue(b.marked(d))


class Triggers(_W):
    """Pure SQL on the migrated schema: the database itself carries descent, whichever client wrote the edge."""

    def edge(self, b, table, child, parent, ttype="consolidation"):
        if table == "consolidation_sources":
            b.c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (?,?,1)", (child, parent))
        elif table == "memory_provenance":
            b.c.execute("INSERT INTO memory_provenance (derived_memory_id, source_memory_id, transformation_type) VALUES (?,?,?)", (child, parent, ttype))
        else:
            self.needs(b.one("SELECT 1 FROM sqlite_master WHERE name='memory_parents'"), "table memory_parents(child_id, parent_id)")
            b.c.execute("INSERT INTO memory_parents (child_id, parent_id) VALUES (?,?)", (child, parent))
        b.c.commit()

    def test_red_an_edge_to_a_marked_row_marks_the_child(self):
        got = {}
        b = Brain(self.tmp)
        for table, ttype in [("consolidation_sources", "")] + [("memory_provenance", t) for t in TRUE_DERIVATIONS]:
            p, c, twin = b.add("p", marked=True), b.add("c"), b.add("clean twin")
            self.edge(b, table, c, p, ttype)
            self.edge(b, table, twin, b.add("clean parent"), ttype)
            got[table + ":" + ttype] = (b.marked(c), b.marked(twin))
        self.assertEqual(set(got.values()), {(True, False)}, "an edge to a marked parent left the child unmarked: %s" % got)

    def test_red_memory_parents_edge_marks_the_child(self):
        b = Brain(self.tmp)
        p, c, twin = b.add("p", marked=True), b.add("c"), b.add("twin")
        self.edge(b, "memory_parents", c, p)
        self.edge(b, "memory_parents", twin, b.add("clean"))
        self.assertEqual((b.marked(c), b.marked(twin)), (True, False))

    def test_red_late_mark_is_transitive_over_mixed_edges(self):
        b = Brain(self.tmp)
        r = [b.add("generation %d" % i) for i in range(6)]
        twin = b.add("clean twin")
        for i, t in enumerate(("consolidation_sources", "memory_provenance", "consolidation_sources", "memory_provenance")):
            self.edge(b, t, r[i + 1], r[i])
        b.mark(r[0])
        self.assertEqual([b.marked(x) for x in r[:5]], [True] * 5, "a late mark stopped before the last generation")
        self.assertEqual((b.marked(r[5]), b.marked(twin)), (False, False))

    def test_red_a_cycle_neither_hangs_nor_aborts(self):
        b = Brain(self.tmp)
        x, y = b.add("x"), b.add("y")
        self.edge(b, "memory_provenance", x, y)
        self.edge(b, "memory_provenance", y, x)
        b.mark(x)
        self.assertTrue(b.marked(y))

    def test_red_missing_parent_means_outside(self):
        b = Brain(self.tmp)
        b.c.execute("PRAGMA foreign_keys=OFF")
        c = b.add("child of a row nobody can find")
        b.c.execute("INSERT INTO consolidation_sources (consolidated_memory_id, source_memory_id, cluster_id) VALUES (?, 987654, 1)", (c,))
        b.c.commit()
        self.assertTrue(b.marked(c))

    def test_red_raptor_membership_marks_the_node(self):
        b = Brain(self.tmp)
        m, web, node, clean, later = (b.add(t) for t in ("member", "web member", "node", "clean node", "later node"))
        b.mark(web)
        ins = "INSERT INTO raptor_clusters (cluster_signature, member_count, member_ids_json, synthesis_memory_id) VALUES (?,?,?,?)"
        b.c.execute(ins, ("s1", 2, json.dumps([m, web]), node))
        b.c.execute(ins, ("s2", 1, json.dumps([m]), clean))
        b.c.execute(ins, ("s3", 1, "{not json", b.add("odd node")))                      # guarded: never an abort
        b.c.execute(ins, ("s4", 1, json.dumps([m]), later))
        b.c.execute("UPDATE raptor_clusters SET member_ids_json=? WHERE cluster_signature='s4'", (json.dumps([m, web]),))
        b.c.commit()
        self.assertEqual((b.marked(node), b.marked(clean), b.marked(later)), (True, False, True))

    def test_red_signature_rows_of_an_older_writer_are_marked(self):
        b = Brain(self.tmp)
        rows = dict(project=b.add("a", project="gap-research"), sentinel=b.add("b", derived_from="untrusted_web_source"),
                    tag=b.add("c", tags=("gap-research",)), clean=b.add("d", tags=("gap",)))
        self.assertEqual({k: b.marked(v) for k, v in rows.items()}, dict(project=True, sentinel=True, tag=True, clean=False))

    def test_red_any_other_mark_value_is_refused_on_write(self):
        b = Brain(self.tmp)
        mid = b.add("row")
        taken = []
        for v in (2, -1, "yes", 0.5):
            for sql, args in (("UPDATE memories SET untrusted_source=? WHERE id=?", (v, mid)),
                              ("INSERT INTO memories (content, category, untrusted_source) VALUES ('x','insight',?)", (v,))):
                try:
                    b.c.execute(sql, args)
                    taken.append((sql[:6], v))
                except Exception:
                    pass
                b.c.rollback()
        self.assertEqual(taken, [], "the mark column took a value that is neither 0 nor 1")

    def test_red_dropped_triggers_are_back_at_the_next_open(self):
        b = Brain(self.tmp)
        for (n,) in b.all("SELECT name FROM sqlite_master WHERE type='trigger' AND sql LIKE '%untrusted_source%'"):
            b.c.execute("DROP TRIGGER %s" % n)
        b.c.commit()
        with wb.engine(b.path) as cm:
            cm._get_conn().close()
        p, c = b.add("p", marked=True), b.add("c")
        self.edge(b, "consolidation_sources", c, p)
        self.assertTrue(b.marked(c))


class Corroboration(_W):
    def corr(self, b, mid):
        import trust_tier as tt
        return tt._trust_components(b.c, mid, _depth=1)["corroboration"] if "_depth" in tt._trust_components.__code__.co_varnames \
            else tt._trust_components(b.c, mid)["corroboration"]

    def fixture(self, marked):
        b = Brain(self.tmp / str(marked))
        p = b.add("parent row", marked=marked)
        for i in range(5):
            b.c.execute("INSERT INTO memory_provenance (derived_memory_id, source_memory_id, transformation_type) VALUES (?,?,'dream_synthesis')",
                        (b.add("own child %d" % i, kind="reflection"), p))
        b.c.commit()
        return b, p

    def test_control_children_corroborate_a_clean_parent(self):
        self.assertGreater(self.corr(*self.fixture(False)), 0.0)

    def test_red_own_children_do_not_corroborate_a_marked_parent(self):
        self.assertEqual(self.corr(*self.fixture(True)), 0.0)


if __name__ == "__main__":
    unittest.main()
