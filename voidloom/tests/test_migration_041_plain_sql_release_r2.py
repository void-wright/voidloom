# -*- coding: utf-8 -*-
"""Round 2, lane R2c, finding m3: migration 041's header documents the plain-SQL release of the belief quarantine
for a brain whose code was reverted (no belief_cli). It restored the EDGES only: the quarantined proposals stayed
'rejected' (so the reverted code's _pair_already_decided never re-proposed them) and the gaps stayed acknowledged.

MEASURED (r1 reflection, on a quarantined copy): after the header SQL, edges_valid_until equalled the pre-image, but
proposals_status 3dbc5eed != bcac072d and gaps_ack 562694d7 != 45281aea.

These tests execute THE HEADER'S OWN TEXT (the indented SQL lines of its rollback paragraph), so the documentation
cannot drift from what it claims. RED on 6bb45ee, GREEN on lane/R2c, unless a docstring says guard.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import belief_engine as BE  # noqa: E402
from voidloom.tests.test_belief_quarantine_r1 import brain, schema_db  # noqa: E402,F401  (fixtures)

MIGRATION = ROOT / "migrations" / "migration_041_belief_quarantine.sql"


def header_release_sql() -> str:
    """The SQL lines (indented five or more spaces after '--') between the header's 'plain SQL after a revert'
    sentence and its 'Removing the table itself' sentence."""
    lines = MIGRATION.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if "plain SQL after a revert" in l)
    end = next(i for i, l in enumerate(lines) if i > start and "Removing the table itself" in l)
    sql = [l[2:] for l in lines[start:end] if re.match(r"^-- {5,}\S", l)]
    assert sql, "the header carries no plain-SQL release"
    return "\n".join(sql)


def _fingerprint(c):
    return {
        "edges": c.execute("SELECT id, valid_until FROM memory_edges ORDER BY id").fetchall(),
        "proposals": c.execute("SELECT id, status, decided_at, error_log FROM edge_proposals ORDER BY id").fetchall(),
        "gaps": c.execute("SELECT id, acknowledged, acknowledged_at, acknowledged_reason FROM gap_inventory "
                          "ORDER BY id").fetchall(),
    }


def _quarantine(c):
    out = BE.quarantine_template_pairs(c, apply=True)
    c.commit()
    kinds = dict(c.execute("SELECT object_kind, COUNT(*) FROM belief_quarantine GROUP BY 1").fetchall())
    assert kinds.get("edge") and kinds.get("proposal") and kinds.get("gap"), (kinds, out)
    return kinds


def _release_by_header(c):
    c.executescript("BEGIN;\n" + header_release_sql() + "\nCOMMIT;")


def test_the_header_sql_restores_edges_proposals_and_gaps(brain):
    path, _ids, _pend = brain
    c = sqlite3.connect(path)
    before = _fingerprint(c)
    _quarantine(c)
    assert _fingerprint(c) != before
    _release_by_header(c)
    after = _fingerprint(c)
    for k in before:
        assert after[k] == before[k], k
    assert c.execute("SELECT COUNT(*) FROM belief_quarantine WHERE released_at IS NULL").fetchone()[0] == 0


def test_the_header_sql_does_what_the_code_release_does(brain, tmp_path):
    path, _ids, _pend = brain
    twin = tmp_path / "twin.db"
    c = sqlite3.connect(path)
    _quarantine(c)
    c.execute("VACUUM INTO ?", (str(twin),))
    t = sqlite3.connect(twin)
    BE.release_quarantine(t, release_all=True, note="code")
    _release_by_header(c)
    assert _fingerprint(c) == _fingerprint(t)


def test_a_changed_object_is_left_alone_and_stays_open(brain):
    """A proposal decided again after the quarantine is a conflict: not overwritten, its row stays open."""
    path, _ids, pend = brain
    c = sqlite3.connect(path)
    _quarantine(c)
    pid = next(iter(pend.values()))
    c.execute("UPDATE edge_proposals SET status = 'enacted', decided_at = '2026-10-16 00:00:00' WHERE id = ?", (pid,))
    c.commit()
    _release_by_header(c)
    assert c.execute("SELECT status FROM edge_proposals WHERE id = ?", (pid,)).fetchone()[0] == "enacted"
    open_rows = c.execute("SELECT object_kind, object_id FROM belief_quarantine WHERE released_at IS NULL").fetchall()
    assert open_rows == [("proposal", pid)], open_rows

