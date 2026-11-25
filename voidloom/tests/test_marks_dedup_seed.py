# -*- coding: utf-8 -*-
"""`maintain` on a brain that holds the shipped seed pack and the owner's own near-duplicates.

WHAT WOULD BREAK WITHOUT IT: the release before archived 9 of the 82 shipped rows and one row of each pair a person writes
on purpose: a correction ("Tuesday" -> "Wednesday"), a negation, a clean note beside a page that says the same, a FACT
with two different WHYs. Being alike is not a reason to archive what a person wrote.

Every row here is given the vector of its pair, or of every other row of its category, so each comparison is as close as
it can be; what is left to decide whether a row goes is who wrote it and what it is marked.
"""
import argparse
import contextlib
import io
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT, data

PAIRS = {
    "correction": ("The meeting with the printers is on Tuesday at ten.", "The meeting with the printers is on Wednesday at ten."),
    "negation": ("I like coffee after dinner.", "I do not like coffee after dinner."),
    "clean beside marked": ("I prefer tea without sugar in the morning.", "I prefer tea without sugar each morning."),
    "fact with two whys": ("FACT: the heater starts at nine degrees.\nWHY: seedlings lost leaves at seven.",
                           "FACT: the heater starts at nine degrees.\nWHY: the insurance asks for it."),
    "exact repeat by a person": ("Buy oat milk on Fridays.", "Buy oat milk on Fridays."),
}


class SeedLikeBrain(Case):
    def test_nothing_a_person_or_the_seed_wrote_is_archived_for_being_alike(self):
        b = Brain(self.tmp)
        p = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "claude_memory_lite.py"), "load-seed", "--file",
                            str(ROOT / "seed" / "seed_pack.json")], env=wb.child_env(b.path), capture_output=True, text=True,
                           timeout=300)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])
        seed = [r[0] for r in b.all("SELECT id FROM memories WHERE seed = 1")]
        self.assertGreater(len(seed), 10)
        by_category = {}
        for mid, cat in b.all("SELECT id, category FROM memories WHERE seed = 1"):
            by_category.setdefault(cat, []).append(mid)
        for cat, ids in by_category.items():                 # every seed row of a category alike to every other
            for mid in ids:
                b.c.execute("DELETE FROM memory_embeddings_v2 WHERE memory_id = ?", (mid,))
                b.c.execute("INSERT INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?, ?)", (mid, wb.fake_vec(cat)))
        has_author = "author" in wb.columns(b.c)        # a release before this one could not say who wrote a row

        def by(who):
            return {"author": who} if has_author else {}
        planted = {}
        for name, (one, two) in PAIRS.items():
            a = b.add(one, importance=0.5, category="user_preference", **(by("user") if name != "negation" else {}))
            c = b.add(two, importance=0.9, category="user_preference", marked=name == "clean beside marked",
                      **(by("user") if name not in ("negation", "clean beside marked") else {}))
            b.c.execute("UPDATE memory_embeddings_v2 SET embedding = (SELECT embedding FROM memory_embeddings_v2 WHERE memory_id = ?) "
                        "WHERE memory_id = ?", (a, c))
            planted[name] = (a, c)
        clean_m = b.add("A summary the sleep loop wrote of the owner's notes.", importance=0.4, category="insight",
                        derived_from="consolidation", **by("model"))
        marked_m = b.add("A summary the sleep loop wrote of a web page.", importance=0.8, category="insight",
                         derived_from="consolidation", marked=True, **by("model"))
        b.c.execute("UPDATE memory_embeddings_v2 SET embedding = (SELECT embedding FROM memory_embeddings_v2 WHERE memory_id = ?) "
                    "WHERE memory_id = ?", (clean_m, marked_m))
        planted["the machine's own rows across a mark"] = (clean_m, marked_m)
        twin = [b.add("A digest line the sleep loop wrote.", importance=imp, category="insight", derived_from="consolidation", **by("model"))
                for imp in (0.4, 0.6)]                       # the control: the machine's own repeat does go
        b.c.execute("UPDATE memory_embeddings_v2 SET embedding = (SELECT embedding FROM memory_embeddings_v2 WHERE memory_id = ?) "
                    "WHERE memory_id = ?", (twin[0], twin[1]))
        b.c.commit()
        out = io.StringIO()
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(out):
            cm.cmd_maintain(argparse.Namespace(skip_dedup=False, skip_decay=True))
        self.assertEqual(wb.last_json(out.getvalue())["status"], "ok")
        archived = {r[0] for r in b.all("SELECT id FROM memories WHERE is_archived = 1")}
        self.assertEqual(archived & set(seed), set(), "a shipped row was archived")
        for name, pair in planted.items():
            self.assertEqual(archived & set(pair), set(), name)
        self.assertEqual(archived, {twin[0]}, "what dedup archives on such a brain: the machine's own lower repeat, only")
        self.assertIn("A digest line the sleep loop wrote.", b.one("SELECT content FROM memories WHERE id = ?", (twin[1],)))


if __name__ == "__main__":
    unittest.main()
