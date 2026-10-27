# -*- coding: utf-8 -*-
"""Round 2, lane R2c, finding l2: one quarantine output printed two pair counts under one name.

MEASURED (r1 reflection, belief_cli quarantine --apply --json on a copy): "totals": {"pairs": 3527, ...} beside
"open_pairs": 3701 -- the second also counted the 174 pairs held only by a pending proposal. RED on 6bb45ee.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import belief_engine as BE  # noqa: E402
from voidloom.tests.test_belief_quarantine_r1 import brain, schema_db  # noqa: E402,F401  (fixtures)

def test_l2_the_apply_and_the_report_agree_on_what_a_pair_is(brain):
    """Round 2, l2: an apply's totals.pairs and the report's pair count, in one output, must agree or be labelled.
    MEASURED on a copy: totals.pairs 3,527 beside open_pairs 3,701 (the 174 proposal-only pairs, unlabelled)."""
    path, _ids, _pend = brain
    c = sqlite3.connect(path)
    out = BE.quarantine_template_pairs(c, apply=True)
    c.commit()
    rep = BE.quarantine_report(c)
    assert rep.get("open_edge_pairs") == out["totals"]["pairs"], (rep.get("open_edge_pairs"), out["totals"])
    assert rep["open_pairs_all_kinds"] == rep["open_edge_pairs"] + rep["open_pairs_without_an_edge"]
    assert "open_pairs" not in rep, "an unlabelled pair count beside totals.pairs is the defect"
