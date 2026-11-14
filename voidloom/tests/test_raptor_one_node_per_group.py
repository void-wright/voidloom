# -*- coding: utf-8 -*-
"""RAPTOR keeps ONE synthesis node per group, updates it in place, embeds it when written, never summarises its own
output, and samples the group's newest members.

Before: the cluster signature was a hash of the sorted member ids, so any new member was a new cluster and the churn
branch never ran -- every daytime run CREATED 2-7 new synthesis memories and updated none. The member query excluded
dream and playbook synthesis but not RAPTOR's own nodes (source_project 'dream-pipeline'), so the brain summarised its
summaries. A new node was written with no embedding (invisible to every vector lane until the next morning's
backfill), and the summary prompt read the OLDEST eight members.

Data clean-up of the nodes already written (archiving all but the newest per group) is not done here.

A packaged scratch brain, a stand-in synthesis mouth and a deterministic stand-in encoder: no model, no network.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
for _p in (str(_ROOT), str(_ROOT / "organs"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_dreamfix_tranche_a import Brain, fake_vec  # noqa: E402

import dream_pipeline as dp  # noqa: E402

GROUP = "raptor-test-group"


class _Raptor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vl-raptor-", dir=os.environ.get("TMPDIR")))
        self.b = Brain(self.tmp)
        self.seen = []
        self.cfg = dict(dp.DEFAULT_CONFIG, raptor_min_cluster_size=4, raptor_max_clusters=200,
                        raptor_churn_min_new_members=3,
                        _synthesis_mouth=self._mouth, _encoder=fake_vec)
        self.ids = [self.b.add("group member note %d about queues" % i, summary="member %d" % i, project=GROUP)
                    for i in range(12)]

    def _mouth(self, label, snippets):
        self.seen.append((label, list(snippets)))
        return "Synthesis of %s: %s" % (label, " / ".join(snippets))

    def run_phase(self):
        return dp.phase_raptor_synthesis(self.b.c, self.cfg, None)

    def node_rows(self):
        return self.b.all("SELECT id, synthesis_memory_id, member_ids_json FROM raptor_clusters "
                          "WHERE cluster_label = ?", (GROUP,))

    def vector_of(self, mid):
        return self.b.one("SELECT embedding FROM memory_embeddings_v2 WHERE memory_id = ?", (mid,))


class OneNodePerGroup(_Raptor):

    def test_a_second_run_creates_nothing_and_a_churned_group_is_updated_in_place(self):
        first = self.run_phase()
        self.assertGreaterEqual(first["created"], 1, first)
        rows = self.node_rows()
        self.assertEqual(len(rows), 1, rows)
        syn = rows[0][1]
        self.assertIsNotNone(syn)
        second = self.run_phase()
        self.assertEqual(second["created"], 0, second)
        for i in range(3):
            self.b.add("group member late note %d" % i, summary="late %d" % i, project=GROUP)
        third = self.run_phase()
        self.assertEqual(third["created"], 0, third)
        self.assertGreaterEqual(third["updated"], 1, third)
        rows = self.node_rows()
        self.assertEqual([r[1] for r in rows], [syn], rows)          # the same node, updated in place
        content = self.b.one("SELECT content FROM memories WHERE id = ?", (syn,))[0]
        self.assertIn("late 2", content)
        # re-embedded with the new text: the vector is the encoder's vector of the updated content
        self.assertEqual(self.vector_of(syn)[0], fake_vec(content))

    def test_its_own_output_is_never_a_member(self):
        # earlier nights' nodes, written as _store_synthesis_node writes them (source_project 'dream-pipeline'), one of
        # them tainted (derived_from 'raptor_synthesis') -- enough of them to form a group of their own
        for i in range(5):
            mid = self.b.add("Synthesis of an earlier night %d" % i, kind="semantic", project="dream-pipeline",
                             tags=("synthesis-node", "raptor"),
                             derived_from="raptor_synthesis" if i == 0 else None)
            self.b.c.execute("INSERT INTO raptor_clusters (cluster_signature, member_count, member_ids_json, "
                             "synthesis_memory_id, cluster_label) VALUES (?, 4, '[]', ?, ?)",
                             ("earlier-%d" % i, mid, "earlier-%d" % i))
        self.b.c.commit()
        self.run_phase()
        self.run_phase()
        syn_ids = {r[0] for r in self.b.all("SELECT synthesis_memory_id FROM raptor_clusters "
                                            "WHERE synthesis_memory_id IS NOT NULL")}
        labels = {r[0] for r in self.b.all("SELECT cluster_label FROM raptor_clusters")}
        self.assertNotIn("dream-pipeline", labels)
        import json
        for (members,) in self.b.all("SELECT member_ids_json FROM raptor_clusters"):
            self.assertFalse(syn_ids & set(json.loads(members or "[]")))

    def test_a_new_node_is_embedded_when_it_is_written(self):
        self.run_phase()
        syn = self.node_rows()[0][1]
        self.assertIsNotNone(self.vector_of(syn), "the synthesis node has no vector the night it is written")

    def test_the_prompt_reads_the_newest_members(self):
        self.run_phase()
        label, snippets = [s for s in self.seen if s[0] == GROUP][0]
        self.assertIn("member 11", snippets)
        self.assertNotIn("member 0", snippets)


if __name__ == "__main__":
    unittest.main()
