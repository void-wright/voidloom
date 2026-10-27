# -*- coding: utf-8 -*-
"""2026-10-26, the owner's ruling: "default to auto approvals ... turn this in the install to default on, with the
option to turn it to manual approval as a step during installation."

  * the setting: approvals.mode in wizard-config.json; a MISSING or unreadable setting reads manual and says it was
    not recorded (a missing key is not a choice); writing it keeps every other key and refuses an unreadable file;
  * the policy step the monthly unit runs: in auto mode it approves every proposal on the report JUST written, signed
    actor 'policy:auto' in BOTH ledgers (decision_events and approval_audit_log); manual holds it; an older report is
    never decided retroactively;
  * the actor: a decision nobody attributed is signed by the derived actor, never the literal 'drew';
  * the oversight line the session-start hook injects (a Claude Code Desktop user never sees the terminal banner);
  * the installer: asked at a terminal (blank = automatic), re-used when recorded, a NEW install with no terminal
    takes automatic, an UPGRADE with no recorded choice is left as it is.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
for _p in (str(_ROOT), str(_ROOT / "organs"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from test_dreamfix_tranche_a import Brain  # noqa: E402

import approvals_policy as AP  # noqa: E402
import decision_units as DU  # noqa: E402
import monthly_self_eval as SE  # noqa: E402
import voidloom_preamble as PRE  # noqa: E402

ENH = [{"title": "Tune the recall cache", "rationale": "r", "evidence": "e", "priority": "low",
        "estimated_effort": "1h", "piece_affected": "L2-C1-hybrid-retrieval"},
       {"title": "Prune stale gaps", "rationale": "r", "evidence": "e", "priority": "low",
        "estimated_effort": "1h", "piece_affected": "L4-P6-gap-mapping"}]


def _report(path: str, month: str, enh=ENH) -> int:
    c = sqlite3.connect(path)
    try:
        cur = c.execute(
            "INSERT INTO self_eval_reports (month, health_scorecard, proposed_enhancements, proposed_deprecations, "
            "threshold_assessment, persona_evolution, status) VALUES (?, '{}', ?, '[]', '{}', '{}', 'pending')",
            (month, json.dumps(enh)))
        rid = cur.lastrowid
        DU.create_units_for_report(c, rid, enh, [])
        c.commit()
        return rid
    finally:
        c.close()


def _events(path: str, rid: int):
    c = sqlite3.connect(path)
    try:
        return c.execute("SELECT e.outcome, e.actor FROM decision_events e JOIN decision_units u ON u.id = e.unit_id "
                         "WHERE u.report_id = ? ORDER BY e.id", (rid,)).fetchall()
    finally:
        c.close()


def _report_has_proposals(path: str, rid: int) -> bool:
    c = sqlite3.connect(path)
    try:
        row = c.execute("SELECT proposed_enhancements, proposed_deprecations FROM self_eval_reports WHERE id = ?",
                        (rid,)).fetchone()
    finally:
        c.close()
    return bool(row) and any(json.loads(x or "[]") for x in row)


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="d26-ap-", dir=os.environ.get("TMPDIR")))
        self.cfg = str(self.tmp / "wizard-config.json")
        Path(self.cfg).write_text(json.dumps({"reranker_enabled": True}) + "\n")
        self.env = mock.patch.dict(os.environ, {"WIZARD_CONFIG_PATH": self.cfg, "VOIDLOOM_ACTOR": "the-owner"})
        self.env.start()

    def tearDown(self):
        self.env.stop()


class TestTheSetting(_Case):
    def test_a_missing_choice_reads_manual_and_says_so(self):
        st = AP.show()
        self.assertEqual((st["mode"], st["recorded"]), ("manual", False))
        self.assertIn("no approvals choice is recorded", st["reason"])

    def test_writing_keeps_every_other_key(self):
        rc, _ = AP.set_mode("auto", set_by="owner")
        self.assertEqual(rc, 0)
        data = json.loads(Path(self.cfg).read_text())
        self.assertTrue(data["reranker_enabled"])
        self.assertEqual(data["approvals"]["mode"], "auto")
        self.assertTrue(AP.show()["recorded"])

    def test_an_unreadable_file_is_refused_and_left_untouched(self):
        Path(self.cfg).write_text("{not json")
        rc, msg = AP.set_mode("auto", set_by="owner")
        self.assertEqual(rc, 3, msg)
        self.assertEqual(Path(self.cfg).read_text(), "{not json")
        self.assertEqual(AP.show()["mode"], "manual")


class TestThePolicyStep(_Case):
    def setUp(self):
        super().setUp()
        self.brain = Brain(self.tmp)
        self.brain.c.close()

    def test_auto_decides_the_new_report_signed_by_the_policy(self):
        old = _report(self.brain.path, "2026-08")
        new = _report(self.brain.path, "2026-09")
        AP.set_mode("auto", set_by="owner")
        out = AP.decide_new_self_eval_report(self.brain.path, new)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(sorted(out["approved"]), sorted(p["title"] for p in ENH))
        ev = _events(self.brain.path, new)
        self.assertTrue(ev and all(a == AP.POLICY_ACTOR for _o, a in ev), ev)
        self.assertEqual({o for o, _a in ev}, {"approved", "enacted"})
        c = sqlite3.connect(self.brain.path)
        audit = c.execute("SELECT actor, confirmed_via FROM approval_audit_log WHERE ref = ?",
                          ("self-eval:%d" % new,)).fetchall()
        c.close()
        self.assertEqual(audit, [(AP.POLICY_ACTOR, AP.POLICY_CONFIRMED_VIA)])
        self.assertEqual(_events(self.brain.path, old), [])       # never retroactive
        self.assertEqual(len(AP.recent_policy_decisions(self.brain.path)), 1)

    def test_manual_holds_the_report_for_a_person(self):
        rid = _report(self.brain.path, "2026-09")
        AP.set_mode("manual", set_by="owner")
        out = AP.decide_new_self_eval_report(self.brain.path, rid)
        self.assertEqual(out["status"], "held")
        self.assertEqual(_events(self.brain.path, rid), [])

    def test_no_recorded_choice_holds_too(self):
        rid = _report(self.brain.path, "2026-09")
        self.assertEqual(AP.decide_new_self_eval_report(self.brain.path, rid)["status"], "held")

    def test_a_person_s_decision_is_signed_by_the_derived_actor_never_a_literal(self):
        rid = _report(self.brain.path, "2026-09")
        SE.drew_review(self.brain.path, rid, approved=[ENH[0]["title"]])
        self.assertEqual(_events(self.brain.path, rid), [("approved", "the-owner")])

    def test_the_session_start_line_says_what_the_brain_decided(self):
        organs = str(_ROOT / "organs")
        self.assertEqual(PRE.read_policy_decisions(self.brain.path, organs), [])
        rid = _report(self.brain.path, "2026-09")
        AP.set_mode("auto", set_by="owner")
        AP.decide_new_self_eval_report(self.brain.path, rid)
        lines = PRE.read_policy_decisions(self.brain.path, organs)
        self.assertEqual(len(lines), 1)
        self.assertIn("VOIDLOOM AUTO-DECIDED", lines[0])
        self.assertIn("self-eval:%d" % rid, lines[0])
        self.assertIn("voidloom approvals --mode manual", lines[0])


# --------------------------------------------------------------------------- the installer
from voidloom.tests.test_tier1_ambient import _AmbientHome, _drive_tty, SH  # noqa: E402

Q_APPROVALS = r"Your answer, a or b \(a blank line is a\):"
TTY_TIMEOUT = int(os.environ.get("VOIDLOOM_TTY_TIMEOUT") or 600)


class TestTheInstallerQuestion(_AmbientHome):
    def setUp(self):
        super().setUp()
        if not sys.platform.startswith("linux"):
            self.skipTest("the scripted terminal is a Linux pty")

    def _mode(self, env):
        r = subprocess.run([sys.executable, str(_ROOT / "organs" / "approvals_policy.py"), "show", "--json"],
                           env=env, capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout)

    def _run_tty(self, extra, answers):
        env = self._env()
        return env, _drive_tty(["bash", str(SH), "--tier0-only", "--force-cpu", "--thinker", "off"] + extra, env,
                               answers, str(self.tmp), timeout=TTY_TIMEOUT, ask_approvals=True)

    def test_a_blank_answer_at_the_terminal_is_automatic_and_an_upgrade_does_not_ask_again(self):
        env, (rc, tty, out, err, left) = self._run_tty(["--init-blank"], [(Q_APPROVALS, "")])
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        st = self._mode(env)
        self.assertEqual((st["mode"], st["recorded"], st["set_by"]), ("auto", True, "owner"))
        env, (rc, tty, out, err, left) = self._run_tty([], [])
        self.assertEqual(rc, 0, (tty[-1500:], err[-1500:]))
        self.assertNotRegex(tty, Q_APPROVALS)
        self.assertIn("approvals: auto (your recorded choice)", out)

    def test_b_at_the_terminal_is_manual(self):
        env, (rc, tty, out, err, left) = self._run_tty(["--init-blank"], [(Q_APPROVALS, "b")])
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertEqual(self._mode(env)["mode"], "manual")

    def test_no_terminal_a_new_install_is_automatic_and_an_upgrade_without_a_choice_is_left_alone(self):
        env = self._env()
        r = subprocess.run(["bash", str(SH), "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "off",
                            "--no-wire-hooks"], env=env, cwd=str(self.tmp), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=TTY_TIMEOUT, start_new_session=True)
        self.assertEqual(r.returncode, 0, (r.stdout[-1500:], r.stderr[-1500:]))
        st = self._mode(env)
        self.assertEqual((st["mode"], st["set_by"]), ("auto", "installer-default"))
        cfg = Path(st["path"])
        data = json.loads(cfg.read_text())
        data.pop("approvals")
        cfg.write_text(json.dumps(data))
        r = subprocess.run(["bash", str(SH), "--tier0-only", "--force-cpu", "--no-wire-hooks"], env=env,
                           cwd=str(self.tmp), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=TTY_TIMEOUT, start_new_session=True)
        self.assertEqual(r.returncode, 0, (r.stdout[-1500:], r.stderr[-1500:]))
        self.assertFalse(self._mode(env)["recorded"])
        self.assertIn("no choice is recorded on this brain", r.stdout)

    def test_the_flag_answers_and_a_wrong_value_touches_nothing(self):
        env = self._env()
        r = subprocess.run(["bash", str(SH), "--init-blank", "--tier0-only", "--approvals", "sometimes"], env=env,
                           cwd=str(self.tmp), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                           start_new_session=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--approvals takes auto or manual", r.stderr)
        self.assertFalse((self.home / ".claude" / "memory" / "claude_memory.db").exists())




# --------------------------------------------------------------------------- review 2
class TestReview2Policy(_Case):
    def setUp(self):
        super().setUp()
        self.brain = Brain(self.tmp)
        self.brain.c.close()

    def test_a_title_a_person_declined_is_declined_again_never_approved(self):
        """N2 + recheck R3-6: a decline is 'don't raise again'; the heuristic proposer
        re-proposes monthly. The policy repeats the person's no (signed policy:auto) rather
        than approving it or leaving it in the queue every month."""
        r1 = _report(self.brain.path, "2026-08")
        SE.drew_review(self.brain.path, r1, rejected=[{"title": ENH[0]["title"], "reason": "no"}])
        r2 = _report(self.brain.path, "2026-09")
        AP.set_mode("auto", set_by="owner")
        out = AP.decide_new_self_eval_report(self.brain.path, r2)
        self.assertEqual(out["declined_again"], [ENH[0]["title"]])
        self.assertEqual(out["approved"], [ENH[1]["title"]])
        c = sqlite3.connect(self.brain.path)
        ev = c.execute("SELECT e.outcome, e.actor FROM decision_events e JOIN decision_units u ON u.id = e.unit_id "
                       "WHERE u.report_id = ? AND u.proposal_title = ?", (r2, ENH[0]["title"])).fetchall()
        c.close()
        self.assertEqual(ev, [("declined", AP.POLICY_ACTOR)])
        line = PRE.read_policy_decisions(self.brain.path, str(_ROOT / "organs"))[0]
        self.assertIn("declined again", line)

    def test_an_unreadable_ledger_holds_the_whole_report_and_says_why(self):
        """recheck R3-9: no earlier 'no' can be honoured if the ledger cannot be read."""
        rid = _report(self.brain.path, "2026-09")
        AP.set_mode("auto", set_by="owner")
        with mock.patch.object(AP, "_titles_a_person_declined", return_value=(set(), False)):
            out = AP.decide_new_self_eval_report(self.brain.path, rid)
        self.assertEqual(out["status"], "held")
        self.assertIn("could not be read", out["reason"])
        self.assertEqual(_events(self.brain.path, rid), [])

    def test_the_migration_signs_with_the_derived_actor(self):
        """recheck R3-4 (N10.4): the ledger migration's backfill never signs a literal handle."""
        rid = _report(self.brain.path, "2026-09")
        SE.drew_review(self.brain.path, rid, approved=[ENH[0]["title"]])
        c = sqlite3.connect(self.brain.path)
        c.execute("DELETE FROM decision_events")
        c.commit()
        c.close()
        DU.migrate(self.brain.path, dry_run=False)
        actors = {a for _o, a in _events(self.brain.path, rid)}
        self.assertNotIn("drew", actors)

    def test_the_session_line_never_promises_to_undo_past_decisions(self):
        """recheck R3-4 (N6)."""
        rid = _report(self.brain.path, "2026-09")
        AP.set_mode("auto", set_by="owner")
        AP.decide_new_self_eval_report(self.brain.path, rid)
        line = PRE.read_policy_decisions(self.brain.path, str(_ROOT / "organs"))[0]
        self.assertNotIn("returns these decisions", line)
        self.assertIn("stand", line)
        AP.set_mode("manual", set_by="owner")
        line = PRE.read_policy_decisions(self.brain.path, str(_ROOT / "organs"))[0]
        self.assertIn("approvals mode is now manual", line)

    def test_the_approvals_backup_never_overwrites_the_gate_pre_image(self):
        """recheck R3-4 (N16)."""
        Path(self.cfg + ".bak").write_text("GATE PRE-IMAGE")
        AP.set_mode("auto", set_by="owner")
        self.assertEqual(Path(self.cfg + ".bak").read_text(), "GATE PRE-IMAGE")
        self.assertTrue(Path(self.cfg + ".approvals.bak").exists())

    def test_the_organ_s_own_set_records_where_it_came_from(self):
        """recheck R3-5: only the installer's three labels are taken as given."""
        r = subprocess.run([sys.executable, str(_ROOT / "organs" / "approvals_policy.py"), "set", "auto",
                            "--by", "the boss"], env=dict(os.environ), stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("via organ no-terminal", AP.show()["set_by"])

    def test_a_failed_enactment_is_never_announced_as_done(self):
        """N1: enact_approved reports an error without raising -> status error, and the
        session line says NOT enacted."""
        rid = _report(self.brain.path, "2026-09")
        AP.set_mode("auto", set_by="owner")
        with mock.patch.object(SE, "enact_approved", return_value={"status": "error", "error": "planted"}):
            out = AP.decide_new_self_eval_report(self.brain.path, rid)
        self.assertEqual(out["status"], "error", out)
        lines = PRE.read_policy_decisions(self.brain.path, str(_ROOT / "organs"))
        self.assertIn("NOT enacted", lines[0])
        self.assertIn("monthly_self_eval.py --enact %d" % rid, lines[0])

    def test_auto_means_the_whole_report_even_past_the_variance_gate(self):
        """N5, the owner's ruling ("straight to full approval"): a report larger than the
        variance gate is decided whole in auto mode; the audit row names the policy."""
        many = [dict(ENH[0], title="Proposal %d" % i) for i in range(SE.VARIANCE_GATE_MAX_PROPOSALS + 3)]
        rid = _report(self.brain.path, "2026-09", enh=many)
        AP.set_mode("auto", set_by="owner")
        out = AP.decide_new_self_eval_report(self.brain.path, rid)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(len(out["approved"]), len(many))
        self.assertTrue(all(o in ("approved", "enacted") for o, _a in _events(self.brain.path, rid)))

    def test_the_monthly_run_hands_its_new_report_to_the_policy(self):
        """N10: the production wiring, end to end through the unit's own command (a real
        heuristic self-eval on a packaged brain, no model)."""
        AP.set_mode("auto", set_by="owner")
        rid_before = _report(self.brain.path, "2026-07")
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="", PYTORCH_NVML_BASED_CUDA_CHECK="1",
                   WIZARD_CONFIG_PATH=self.cfg)
        r = subprocess.run([sys.executable, str(_ROOT / "organs" / "monthly_self_eval.py"), "--run",
                            self.brain.path, "--no-llm"], env=env, capture_output=True, text=True, timeout=600)
        out = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(out.get("status"), "ok", (out, r.stderr[-800:]))
        pol = out["approvals_policy"]
        new = out["report_id"]
        if _report_has_proposals(self.brain.path, new):
            self.assertEqual(pol["status"], "ok", pol)
            self.assertTrue(_events(self.brain.path, new))
            self.assertTrue(all(a == AP.POLICY_ACTOR for _o, a in _events(self.brain.path, new)))
        else:
            self.assertEqual(pol["status"], "skipped", pol)
        self.assertEqual(_events(self.brain.path, rid_before), [])

    def test_the_oversight_line_is_its_own_segment_and_is_never_dropped(self):
        """N11: under a tight budget the line survives."""
        rid = _report(self.brain.path, "2026-09")
        AP.set_mode("auto", set_by="owner")
        AP.decide_new_self_eval_report(self.brain.path, rid)
        organs = str(_ROOT / "organs")
        segs, _diag = PRE.build_segments(db=self.brain.path, home=str(self.tmp), organs=organs)
        pol = [s for s in segs if s.id == "policy"]
        self.assertEqual(len(pol), 1)
        self.assertIsNone(pol[0].drop)
        self.assertNotIn("policy", PRE.DROP_ORDER)

    def test_an_audit_row_with_no_actor_is_signed_by_the_derived_actor(self):
        """N10: write_audit's default is never a literal handle."""
        import voidloom_approvals as VA
        VA.write_audit("self-eval:999", "approve", confirmed_via="test", db=self.brain.path)
        c = sqlite3.connect(self.brain.path)
        actor = c.execute("SELECT actor FROM approval_audit_log WHERE ref='self-eval:999'").fetchone()[0]
        c.close()
        self.assertEqual(actor, "the-owner")

    def test_a_brain_without_provenance_keeps_the_sentinel_reading_the_whole_queue(self):
        """N0: no embedding_provenance (a brain before migration 033) -> the yield row reads the
        whole queue, as the engine keeps the template rule there; never UNMEASURABLE."""
        import organ_yield_sentinel as S
        c = sqlite3.connect(self.brain.path)
        c.execute("DROP TABLE IF EXISTS embedding_provenance")
        c.execute("INSERT INTO belief_scan_queue (memory_id) VALUES (1)")
        c.commit()
        spec = next(w for w in S.WATCH if w["organ"] == "belief_engine.propose")
        r = S._one_reading(c, spec, {}, False)
        self.assertNotIn("input UNMEASURABLE", r.detail)
        self.assertEqual(c.execute(spec["input_sql_no_provenance"]).fetchone()[0], 1)
        c.close()


class TestReview2ModeVerb(_Case):
    def _cli(self, *args, tty=False):
        from voidloom import cli
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), mock.patch.object(sys.stdin, "isatty", return_value=tty):
            rc = cli.dispatch(["approvals", "--json", *args])
        return rc, buf.getvalue()

    def test_with_no_terminal_changing_the_mode_needs_confirm(self):
        """N13: an agent acting alone stops; asked by the owner it passes --confirm, and the
        record says so."""
        rc, out = self._cli("--mode", "auto")
        self.assertNotEqual(rc, 0, out)
        self.assertFalse(AP.show()["recorded"])
        rc, out = self._cli("--mode", "auto", "--confirm")
        self.assertEqual(rc, 0, out)
        st = AP.show()
        self.assertEqual(st["mode"], "auto")
        self.assertIn("via no-terminal --confirm", st["set_by"])
        self.assertIn("was manual (nothing recorded)", out)

    def test_at_a_terminal_it_is_the_owner_s_own_act(self):
        rc, out = self._cli("--mode", "manual", tty=True)
        self.assertEqual(rc, 0, out)
        self.assertIn("via terminal", AP.show()["set_by"])


class TestReview2Installer(_AmbientHome):
    def setUp(self):
        super().setUp()
        if not sys.platform.startswith("linux"):
            self.skipTest("the scripted terminal is a Linux pty")

    def test_an_existing_brain_with_no_install_record_is_never_switched(self):
        """N3: no INSTALL_RECORD (installed before 2026-09-26, or a failed gate) is still an
        existing brain: a non-interactive run leaves its approvals alone."""
        env = self._env()
        self._brain()                                   # an existing DB, no INSTALL_RECORD
        rec = self.home / ".claude" / "memory" / "INSTALL_RECORD"
        if rec.exists():
            rec.unlink()
        r = subprocess.run(["bash", str(SH), "--tier0-only", "--force-cpu", "--thinker", "off", "--no-wire-hooks"],
                           env=env, cwd=str(self.tmp), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=TTY_TIMEOUT, start_new_session=True)
        st = json.loads(subprocess.run([sys.executable, str(_ROOT / "organs" / "approvals_policy.py"), "show",
                                        "--json"], env=env, capture_output=True, text=True).stdout)
        self.assertFalse(st["recorded"], (r.stdout[-1200:], r.stderr[-1200:]))
        self.assertNotIn("default for a new install", r.stdout)

    def test_the_upgrade_question_says_what_the_brain_does_today(self):
        """N14: before a blank Enter switches it, the question states today's state."""
        env = self._env()
        r = subprocess.run(["bash", str(SH), "--init-blank", "--tier0-only", "--force-cpu", "--thinker", "off",
                            "--no-wire-hooks", "--approvals", "manual"], env=env, cwd=str(self.tmp),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=TTY_TIMEOUT,
                           start_new_session=True)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        cfg = Path(json.loads(subprocess.run([sys.executable, str(_ROOT / "organs" / "approvals_policy.py"),
                                              "show", "--json"], env=env, capture_output=True,
                                             text=True).stdout)["path"])
        data = json.loads(cfg.read_text())
        data.pop("approvals")
        cfg.write_text(json.dumps(data))
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--tier0-only", "--force-cpu", "--thinker", "off"],
                                             env, [(Q_APPROVALS, "b")], str(self.tmp), timeout=TTY_TIMEOUT,
                                             ask_approvals=True)
        self.assertEqual((rc, left), (0, []), (tty[-1500:], err[-1500:]))
        self.assertIn("On this brain today: no choice is recorded", tty)

    def test_env_only_never_asks(self):
        """N15: --env-only lays no organ and records no choice, so it does not ask."""
        env = self._env()
        rc, tty, out, err, left = _drive_tty(["bash", str(SH), "--env-only", "--force-cpu"], env, [],
                                             str(self.tmp), timeout=300, ask_approvals=True)
        self.assertNotRegex(tty, Q_APPROVALS)


if __name__ == "__main__":
    unittest.main()
