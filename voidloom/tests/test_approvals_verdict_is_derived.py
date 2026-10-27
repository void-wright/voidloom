# -*- coding: utf-8 -*-
"""The approvals verdict is DERIVED from the work, never read from the status field.

CRUCIBLE-3 D04-F1 (LIES). `voidloom approvals --approve belief:1 --ids 999999
--confirm --json` returned status "ok", code "approval_enacted", EXIT 0 — directly
above `"enacted": []` and `"failed": [{"error": "proposal not found"}]`. The human
render was worse, because it is the line an operator reads:
`[ok] voidloom approvals (approval_enacted)`. Blast radius: every non-TTY enact
(`--json`, scripts, any `&& next-step`) plus the persisted approval_audit_log.

The rule, already in this brain from the 2026-09-08 measurement of the same shape
one layer in: *a status field is not the work, it is a claim ABOUT the work made by
its author. When a result carries both a status and a list of outcomes, the list is
the evidence and the status is testimony. Read the list.*

These cases drive the SHIPPED `_wrap_backend_result` directly, which is the single
funnel all three enact verbs pass through, so a regression cannot hide behind a
fixture that happens not to fail.
"""
from __future__ import annotations

import unittest

from voidloom.cli import _wrap_backend_result


def _w(payload):
    return _wrap_backend_result(dict(payload), verb="approvals",
                                ok_code="approval_enacted")


class TestApprovalsVerdictIsDerived(unittest.TestCase):

    def test_all_items_failed_is_not_ok(self):
        """The reported defect, exactly."""
        r = _w({"status": "ok", "action": "approved_and_enacted", "enacted": [],
                "failed": [{"id": 999999, "error": "proposal not found"}], "skipped": []})
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["code"], "approvals_enact_incomplete", r)

    def test_partial_enact_is_not_ok(self):
        """One success beside one failure is not a success."""
        r = _w({"status": "ok", "enacted": [{"id": 1}],
                "failed": [{"id": 2, "error": "x"}], "skipped": []})
        self.assertEqual(r["status"], "error", r)

    def test_blocked_counts_as_not_ok(self):
        r = _w({"status": "ok", "enacted": [], "failed": [],
                "blocked": [{"id": 3}], "skipped": []})
        self.assertEqual(r["status"], "error", r)

    def test_enacting_nothing_at_all_is_not_ok(self):
        """No success, no failure, no skip: there is no work this verdict is about."""
        r = _w({"status": "ok", "enacted": [], "failed": [], "skipped": []})
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["code"], "approvals_enacted_nothing", r)

    def test_self_eval_nested_under_enact_is_read(self):
        """Self-eval reports its work nested; belief and governance report it flat."""
        r = _w({"status": "ok", "enact": {"enacted": [], "blocked": [],
                                          "failed": [{"title": "a"}]}})
        self.assertEqual(r["status"], "error", r)

    # --- the other direction: legitimate successes must STILL be ok ---------

    def test_genuine_success_is_ok(self):
        r = _w({"status": "ok", "action": "approved_and_enacted",
                "enacted": [{"id": 1}], "failed": [], "skipped": []})
        self.assertEqual(r["status"], "ok", r)

    def test_skipped_only_is_ok(self):
        """'Already applied' is an outcome, not a failure."""
        r = _w({"status": "ok", "enacted": [], "failed": [],
                "skipped": [{"id": 4, "why": "already applied"}]})
        self.assertEqual(r["status"], "ok", r)

    def test_a_result_with_no_work_list_is_left_alone(self):
        """THE ESCAPE HATCH, and it is deliberately narrow: a listing verb has no
        work list to read, and inventing a failure from its silence would be the
        same error in the opposite direction."""
        r = _w({"status": "ok", "unified_open_count": 4})
        self.assertEqual(r["status"], "ok", r)

    def test_backend_error_still_surfaces_as_error(self):
        r = _w({"status": "error", "message": "boom"})
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["code"], "approvals_error", r)

    def test_the_counts_reach_the_operator(self):
        """A refusal that does not say what it observed is a different defect."""
        r = _w({"status": "ok", "enacted": [], "failed": [{"id": 9}], "skipped": []})
        msg = (r.get("data") or {}).get("message") or ""
        self.assertIn("0 enacted", msg, r)
        self.assertIn("1 failed", msg, r)


if __name__ == "__main__":
    unittest.main()
