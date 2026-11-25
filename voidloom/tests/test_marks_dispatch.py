# -*- coding: utf-8 -*-
"""What a research dispatch may say, and which file may allow it.

WHAT WOULD BREAK WITHOUT IT: the owner approves one topic, and a sentence in the environment adds another to every prompt;
or a link somebody makes to the brain brings its own approval file along.
"""
import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _wall_brain as wb
from _wall_brain import Brain, Case, ROOT

sys.path.insert(0, str(ROOT / "organs"))
import gap_mapping as gm

TOPIC, OTHER = "Zanzibar quokka freezing", "Patagonian mara thawing"
FILE = "approved_research_topics.json"


class Dispatch(Case):
    def brain(self, name="b", topics=(TOPIC,), approve=(TOPIC,)):
        b = Brain(self.tmp / name)
        b.ids = [b.c.execute("INSERT INTO gap_inventory (entity, gap_type, score, auto_research_eligible) VALUES (?, 'thin_entity', 0.9, 1)",
                             (t,)).lastrowid for t in topics]
        b.c.commit()
        if approve is not None:
            (self.tmp / name / FILE).write_text(json.dumps(list(approve)), encoding="utf-8")
        return b

    def go(self, b, db=None, env=None, **k):
        sent = []
        with wb.engine(b.path, env), mock.patch.object(gm._thinker, "think", side_effect=AssertionError("the network seam")), \
                mock.patch.object(gm, "_dispatch_research", lambda p, t=0: sent.append(p) or {
                    "status": "success", "output": "Quokkas survive, see doi 10.1/x for the measured rate.", "wall_clock_seconds": 0.1}):
            return sent, gm.dispatch_auto_research(str(db or b.path), **k)

    def refused(self, got, reason):
        sent, r = got
        self.assertEqual(sent, [])
        self.assertEqual((r["code"], r["reason"]), ("RESEARCH_DISPATCH_CLOSED", reason))

    # ---- the prompt ------------------------------------------------------------------------------------------------
    def test_no_setting_adds_a_word_to_what_is_sent(self):
        plain, _r = self.go(self.brain("plain"))
        by_env, _r = self.go(self.brain("env"), env={"WIZARD_GAP_RESEARCH_FOCUS": "Also research the SECRETTOPIC"})
        by_config, _r = self.go(self.brain("config"), config={"research_focus": "Also research the SECRETTOPIC"})
        self.assertEqual(len(plain), 1)
        self.assertEqual(by_env, plain)
        self.assertEqual(by_config, plain)

    def test_a_prompt_is_one_template_and_the_topic(self):
        b = self.brain("two", topics=(TOPIC, OTHER), approve=(TOPIC, OTHER))
        b.c.execute("UPDATE gap_inventory SET gap_type = 'unresolved_contradiction' WHERE entity = ?", (OTHER,))
        b.c.commit()
        sent, _r = self.go(b, gap_ids=b.ids)
        self.assertEqual(len(sent), 2)
        self.assertEqual({p.replace(TOPIC, "{}").replace(OTHER, "{}") for p in sent},
                         {gm._build_research_prompt("{}")})

    # ---- which file ------------------------------------------------------------------------------------------------
    def test_a_link_to_the_brain_does_not_bring_its_own_approval(self):
        b = self.brain("real", approve=None)
        link = self.tmp / "link"
        link.mkdir()
        os.symlink(b.path, link / "claude_memory.db")
        (link / FILE).write_text(json.dumps([TOPIC]), encoding="utf-8")
        self.refused(self.go(b, db=link / "claude_memory.db"), "brain-path-is-a-link")
        (self.tmp / "real" / FILE).write_text(json.dumps([TOPIC]), encoding="utf-8")
        self.refused(self.go(b, db=link / "claude_memory.db"), "brain-path-is-a-link")

    def test_a_linked_folder_is_the_folder_it_leads_to(self):
        b = self.brain("real")
        os.symlink(self.tmp / "real", self.tmp / "alias")
        sent, _r = self.go(b, db=self.tmp / "alias" / "claude_memory.db")
        self.assertEqual(len(sent), 1)

    def test_an_approval_file_that_is_a_link_elsewhere_grants_nothing(self):
        b = self.brain("real", approve=None)
        (self.tmp / "elsewhere.json").write_text(json.dumps([TOPIC]), encoding="utf-8")
        os.symlink(self.tmp / "elsewhere.json", self.tmp / "real" / FILE)
        self.refused(self.go(b), "approval-file-is-a-link")

    def test_a_file_that_cannot_be_parsed_is_a_refusal_with_a_row(self):
        for n, raw in enumerate(("[" * 200000, '{"topics":' * 100000, "\x00", "[1e999999", "")):
            b = self.brain(f"deep{n}", approve=None)
            (self.tmp / f"deep{n}" / FILE).write_text(raw, encoding="utf-8")
            self.refused(self.go(b), "approval-file-unreadable")
            self.assertEqual(b.all("SELECT reason FROM research_dispatch_refusals"), [("approval-file-unreadable",)])

    def test_ids_that_are_not_ids_are_refused_by_name(self):
        b = self.brain()
        for ids in ([10 ** 30], ["1 OR 1=1"], [True], [1.5]):
            sent, r = self.go(b, gap_ids=ids)
            self.assertEqual((sent, r["code"]), ([], "GAP_IDS_INVALID"), ids)
        self.assertEqual(self.go(b, max_gaps=-1)[0], [])
        self.assertEqual(len(self.go(b, max_gaps=None)[0]), 1)

    # ---- how the refusal is said -----------------------------------------------------------------------------------
    def verb(self, b, *argv):
        p = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "gap_mapping.py"), "--research", "--method", "claude_cli",
                            "--max-gaps", "5", *argv, b.path], text=True, capture_output=True, cwd=str(self.tmp), timeout=180,
                           env=wb.child_env(b.path, PYTHONPATH=f"{ROOT}:{ROOT / 'organs'}"))
        out, rc = io.StringIO(), 0
        with wb.engine(b.path) as cm, contextlib.redirect_stdout(out):
            try:
                cm.cmd_gap_research(argparse.Namespace(gap_ids=argv[1] if argv else None, ack=None, config=None,
                                                       method="thinker", max_gaps=5))
            except SystemExit as e:
                rc = e.code
        return [(p.returncode, p.stdout), (rc, out.getvalue())]

    def test_an_explicit_id_that_is_not_approved_exits_non_zero_and_a_scheduled_run_does_not(self):
        for name, approve in (("closed", None), ("other", (OTHER,))):
            b = self.brain(name, approve=approve)
            for rc, out in self.verb(b, "--gap-ids", str(b.ids[0])):
                self.assertEqual(rc, 1, out)
                self.assertIn("RESEARCH_DISPATCH_CLOSED", out)
            for rc, out in self.verb(b):
                self.assertEqual(rc, 0, out)
                self.assertIn("RESEARCH_DISPATCH_CLOSED", out)
            for rc, out in self.verb(b, "--gap-ids", "1,x"):
                self.assertEqual(rc, 1, out)
                self.assertIn("GAP_IDS_INVALID", out)

    def test_a_scheduled_run_with_an_approval_and_nothing_eligible_says_how_one_is_sent(self):
        b = self.brain("quiet")
        b.c.execute("UPDATE gap_inventory SET auto_research_eligible = 0")
        b.c.commit()
        for rc, out in self.verb(b):
            self.assertEqual(rc, 0, out)
            self.assertIn("--gap-ids", out)

    def test_a_run_that_sends_one_and_refuses_one_says_so(self):
        b = self.brain("half", topics=(TOPIC, OTHER), approve=(TOPIC,))
        sent, r = self.go(b, gap_ids=b.ids)
        self.assertEqual((len(sent), r["refused"], r["code"]), (1, 1, "RESEARCH_DISPATCH_CLOSED"))
        self.assertEqual(gm.refused_exit(r, b.ids), 1)


if __name__ == "__main__":
    unittest.main()
