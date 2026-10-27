# -*- coding: utf-8 -*-
"""2026-10-26 diagnostics: the belief engine's false class is defined by WHO
WROTE a memory, not by a list of organ templates.

Measured on the owner's brain (read-only): of 3,993 contradictions ever
proposed, none joined two memories a person stored. After round 1's template
quarantine, 292 pairs (584 edges) were still live -- "Sleep Loop Reflection",
"APPROVED ENHANCEMENT" and weekly-digest notes, families the list never named --
and the precision canary read HEALTHY over all of them because it shared the
filter's list. 30 of them were approved by hand on 2026-10-26.

No model is loaded: the NLI is a double, neighbours are a double, and torch is
never imported.
"""
from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORGANS = ROOT / "organs"
MIGRATIONS = ROOT / "migrations"
if str(ORGANS) not in sys.path:
    sys.path.insert(0, str(ORGANS))

import belief_engine as BE  # noqa: E402
import organ_yield_sentinel as S  # noqa: E402
import schema_migrator  # noqa: E402

REFLECTION = "Sleep Loop Reflection (2026-09-2{n}): {k} episodic memories accumulated. Dominant themes: t{n}."


@pytest.fixture(scope="module")
def schema_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("schema") / "base.db"
    out = schema_migrator.apply(str(path), MIGRATIONS)
    assert out["complete"], out["files_with_errors"]
    return path


def _mem(conn, content):
    return conn.execute(
        "INSERT INTO memories (content, summary, category, importance, source_project, kind, created_at, "
        "updated_at, last_accessed) VALUES (?, ?, 'insight', 0.5, 'p', 'episodic', datetime('now'), "
        "datetime('now'), datetime('now'))", (content, content[:40])).lastrowid


def _stamp(conn, mid, organ):
    conn.execute(
        "INSERT INTO embedding_provenance (memory_id, vec_table, model_id, model_ref, dim, content_hash, "
        "embedded_at, organ) VALUES (?, 'memory_embeddings_v2', 'test/model', 'unresolved', 1024, ?, "
        "datetime('now'), ?)", (mid, "0" * 64, organ))


def _live_pair(conn, a, b, by='{"authored_by": "belief_engine"}'):
    for s, t in ((a, b), (b, a)):
        conn.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, metadata, "
                     "extraction_method) VALUES (?, ?, 'contradicts', 0.95, ?, 'manual')", (s, t, by))


@pytest.fixture()
def brain(schema_db, tmp_path):
    path = tmp_path / "brain.db"
    shutil.copy(schema_db, path)
    c = sqlite3.connect(path)
    ids = {
        "refl1": _mem(c, REFLECTION.format(n=1, k=87)),          # unlisted organ family, unstamped
        "refl2": _mem(c, REFLECTION.format(n=4, k=190)),
        "org1": _mem(c, "Weekly digest: the week leaned on retries."),   # organ-stamped
        "org2": _mem(c, "Weekly digest: the week leaned on caching."),
        "own1": _mem(c, "The assay runs at 37 C."),               # a person wrote these
        "own2": _mem(c, "The assay runs at 4 C."),
        "own3": _mem(c, "Deploys go out on Tuesdays."),
    }
    _stamp(c, ids["org1"], "whisper_network.generate_weekly_digest")
    _stamp(c, ids["org2"], "whisper_network.generate_weekly_digest")
    _stamp(c, ids["own1"], "claude_memory.store")
    _stamp(c, ids["own2"], "claude_memory.update")   # both authored writers are covered (review F9)
    _stamp(c, ids["own3"], "claude_memory.store")
    _live_pair(c, ids["refl1"], ids["refl2"])      # false: no end authored
    _live_pair(c, ids["org1"], ids["org2"])        # false: organ-written
    _live_pair(c, ids["own3"], ids["org1"])        # false: one end organ-written
    _live_pair(c, ids["own1"], ids["own2"])        # a real pair: both authored
    c.execute("INSERT INTO edge_proposals (proposal_kind, source_id, target_id, predicate, nli_label, "
              "nli_score, cosine, status, report_batch) VALUES ('add_contradicts', ?, ?, 'contradicts', "
              "'contradiction', 0.99, 0.6, 'pending', 'b1')", (ids["refl1"], ids["org2"]))
    c.commit()
    c.close()
    return path, ids


class _NLIScreams:
    calls = 0

    def predict(self, pairs, apply_softmax=True):
        type(self).calls += len(pairs)
        return [[0.99, 0.005, 0.005] for _ in pairs]


def _edges_hash(conn) -> str:
    rows = conn.execute("SELECT id, source_id, target_id, predicate, valid_until FROM memory_edges "
                        "ORDER BY id").fetchall()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def _live(conn):
    return {(s, t) for s, t in conn.execute(
        "SELECT source_id, target_id FROM memory_edges WHERE predicate='contradicts' AND valid_until IS NULL")}


def test_the_class_is_every_pair_without_two_authored_ends(brain):
    path, ids = brain
    c = sqlite3.connect(path)
    a = BE.authored_ids(c)
    assert a == {ids["own1"], ids["own2"], ids["own3"]}
    fam = lambda x, y: BE.false_family(None, None, ids[x], ids[y], a)  # noqa: E731
    assert fam("refl1", "refl2") == BE.NON_AUTHORED_FAMILY
    assert fam("org1", "org2") == BE.NON_AUTHORED_FAMILY
    assert fam("own3", "org1") == BE.NON_AUTHORED_FAMILY
    assert fam("own1", "own2") is None
    c.close()


def test_scan_pair_never_asks_nli_about_a_non_authored_pair(brain):
    path, ids = brain
    c = sqlite3.connect(path)
    _NLIScreams.calls = 0
    out = BE.scan_pair(c, ids["refl1"], ids["refl2"], nli_model=_NLIScreams(), cosine_gate=0.5,
                       precomputed_cosine=0.9)
    assert out["decision"] == BE.NON_AUTHORED_PAIR_DECISION and _NLIScreams.calls == 0
    own = BE.scan_pair(c, ids["own1"], ids["own2"], nli_model=_NLIScreams(), cosine_gate=0.5,
                       precomputed_cosine=0.9)
    assert own["decision"] == "contradicts" and _NLIScreams.calls > 0
    c.close()


def test_nightly_scan_skips_and_counts_non_authored_pairs(brain, monkeypatch):
    path, ids = brain
    c = sqlite3.connect(path)
    c.execute("DELETE FROM memory_edges")
    c.execute("DELETE FROM edge_proposals")
    nb = {ids["refl1"]: [(ids["refl2"], 0.9)], ids["org1"]: [(ids["own3"], 0.9)],
          ids["own1"]: [(ids["own2"], 0.9)]}
    for m in nb:
        c.execute("INSERT INTO belief_scan_queue (memory_id) VALUES (?)", (m,))
    c.commit()
    monkeypatch.setattr(BE, "_top_k_neighbors", lambda conn, mid, k, gate: nb.get(mid, []))
    _NLIScreams.calls = 0
    out = BE.nightly_scan(c, nli_model=_NLIScreams(), report_batch="t")
    rows = c.execute("SELECT source_id, target_id FROM edge_proposals").fetchall()
    c.close()
    assert out["non_authored_pairs_skipped"] == 2 and out["authorship_known"] is True
    assert rows == [(ids["own1"], ids["own2"])]
    assert _NLIScreams.calls == 2          # only the authored pair, both directions


def test_quarantine_retires_the_class_round_trips_exactly_and_respects_a_release(brain):
    path, ids = brain
    c = sqlite3.connect(path)
    pre = _edges_hash(c)
    dry = BE.quarantine_template_pairs(c)
    assert dry["totals"]["edges"] == 6 and dry["totals"]["proposals"] == 1
    assert _edges_hash(c) == pre                                    # a dry run writes nothing
    BE.quarantine_template_pairs(c, apply=True)
    assert _live(c) == {(ids["own1"], ids["own2"]), (ids["own2"], ids["own1"])}
    assert c.execute("SELECT status FROM edge_proposals WHERE status='pending'").fetchall() == []
    assert {r[0] for r in c.execute("SELECT DISTINCT family FROM belief_quarantine")} == {BE.NON_AUTHORED_FAMILY}
    assert {r[0] for r in c.execute("SELECT DISTINCT reason FROM belief_quarantine")} == {BE.NON_AUTHORED_REASON}
    again = BE.quarantine_template_pairs(c, apply=True)               # idempotent
    assert again["totals"] == {"pairs": 0, "edges": 0, "proposals": 0, "gaps": 0}
    BE.release_quarantine(c, family=BE.NON_AUTHORED_FAMILY, note="rehearsal")
    c.commit()
    assert _edges_hash(c) == pre                                    # released = the pre-image, exactly
    after = BE.quarantine_template_pairs(c, apply=True)               # a released pair stays released
    assert after["totals"]["edges"] == 0
    c.close()


def test_the_precision_canary_sees_the_class_the_template_list_missed(brain):
    path, ids = brain
    c = sqlite3.connect(path)
    spec = S.PRECISION_WATCH[0]
    before = S._precision_reading(c, spec, {}, False)
    assert before.verdict == "BROKEN", before.detail
    BE.quarantine_template_pairs(c, apply=True)
    after = S._precision_reading(c, spec, {}, False)
    assert after.verdict == "HEALTHY", after.detail
    c.close()


def test_a_brain_that_cannot_say_who_wrote_anything_keeps_the_template_rule(brain):
    path, ids = brain
    c = sqlite3.connect(path)
    c.execute("DROP TABLE embedding_provenance")
    c.commit()
    assert BE.authored_ids(c) is None
    dry = BE.quarantine_template_pairs(c)
    assert dry["totals"]["edges"] == 0          # nothing here is a listed template; nothing is retired
    _NLIScreams.calls = 0
    out = BE.scan_pair(c, ids["refl1"], ids["refl2"], nli_model=_NLIScreams(), cosine_gate=0.5,
                       precomputed_cosine=0.9)
    assert out["decision"] == "contradicts"
    c.close()


def test_the_release_command_can_undo_the_new_family_from_both_entry_points(brain):
    """The rollback must be reachable, not only written: `--release-family` offered only the
    three template families, so the CLI could not undo a not_authored quarantine."""
    import ast
    import json as _json
    import os
    import subprocess
    path, ids = brain
    c = sqlite3.connect(path)
    pre = _edges_hash(c)
    BE.quarantine_template_pairs(c, apply=True)
    c.close()
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": "", "PYTORCH_NVML_BASED_CUDA_CHECK": "1"}
    r = subprocess.run([sys.executable, str(ORGANS / "belief_cli.py"), "quarantine", "--release-family",
                        BE.NON_AUTHORED_FAMILY, "--db", str(path), "--json"],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert _json.loads(r.stdout)["action"] == "release"
    c = sqlite3.connect(path)
    assert _edges_hash(c) == pre
    c.close()
    # the engine's own `belief` subcommand carries a second parser; it must offer the same family
    tree = ast.parse((ORGANS / "claude_memory.py").read_text(encoding="utf-8"))
    offered = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == "--release-family":
            for kw in node.keywords:
                if kw.arg == "choices":
                    offered = ast.literal_eval(kw.value)
    assert offered is not None and BE.NON_AUTHORED_FAMILY in offered
    assert set(f for f, _ in BE.TEMPLATE_FAMILIES) <= set(offered)


def test_the_canary_sees_a_pending_proposal_alone(brain):
    """Review F4: the pending-proposal branch of the canary had no test of its own."""
    path, ids = brain
    c = sqlite3.connect(path)
    c.execute("DELETE FROM memory_edges")
    c.commit()
    r = S._precision_reading(c, S.PRECISION_WATCH[0], {}, False)
    assert r.verdict == "BROKEN" and r.output_rows == 1, r.detail
    c.close()


def test_a_released_link_is_reported_as_kept_not_as_awaiting_a_scan(brain):
    """Review F3: after a person releases the quarantine, the scan never re-takes those
    links, so the canary must not stay BROKEN promising that it will."""
    path, ids = brain
    c = sqlite3.connect(path)
    c.execute("DELETE FROM edge_proposals")
    BE.quarantine_template_pairs(c, apply=True)
    BE.release_quarantine(c, family=BE.NON_AUTHORED_FAMILY, note="kept")
    c.commit()
    r = S._precision_reading(c, S.PRECISION_WATCH[0], {}, False)
    assert r.verdict == "HEALTHY", r.detail
    assert "released by a person and are kept by choice" in r.detail
    c.close()


def test_a_link_this_engine_did_not_draw_is_never_retired_for_authorship(brain):
    """Review F13: the authorship rule retires only the engine's own links."""
    path, ids = brain
    c = sqlite3.connect(path)
    c.execute("DELETE FROM memory_edges")
    c.execute("DELETE FROM edge_proposals")
    _live_pair(c, ids["own3"], ids["refl1"], by='{"authored_by": "person"}')
    c.commit()
    assert BE.quarantine_template_pairs(c)["totals"]["edges"] == 0
    c.close()


def test_the_yield_row_reads_starved_not_broken_when_only_organ_rows_queue(brain):
    """Review F2: the engine proposes nothing by design, while organ rows keep queuing.
    The yield row must count only authored queue rows as input, or it reads BROKEN
    ("worked, then stopped") about a design choice."""
    path, ids = brain
    c = sqlite3.connect(path)
    for m in (ids["refl1"], ids["org1"], ids["org2"]):
        c.execute("INSERT INTO belief_scan_queue (memory_id) VALUES (?)", (m,))
    c.commit()
    spec = next(w for w in S.WATCH if w["organ"] == "belief_engine.propose")
    assert c.execute(spec["input_sql"]).fetchone()[0] == 0
    c.execute("INSERT INTO belief_scan_queue (memory_id) VALUES (?)", (ids["own1"],))
    c.commit()
    assert c.execute(spec["input_sql"]).fetchone()[0] == 1
    c.close()


def test_the_remediation_pass_counts_the_false_class_it_never_judges(brain):
    """Review F10: scan_pair's two false-class decisions matched no branch in the
    remediation pass, so those pairs went uncounted and its tally stopped summing."""
    path, ids = brain
    c = sqlite3.connect(path)
    c.execute("DELETE FROM memory_edges")
    c.execute("DELETE FROM edge_proposals")
    for s, t in ((ids["refl1"], ids["refl2"]), (ids["refl2"], ids["refl1"])):
        c.execute("INSERT INTO memory_edges (source_id, target_id, predicate, confidence, metadata, "
                  "extraction_method) VALUES (?, ?, 'contradicts', 0.3, '{}', 'heuristic')", (s, t))
    c.commit()
    out = BE.remediate_heuristic_contradicts(c, dry_run=True, nli_model=_NLIScreams(), cosine_gate=0.5)
    assert out.get("tally", out).get("skipped_false_class") == 1, out
    c.close()
