# -*- coding: utf-8 -*-
"""Round 3 G2 item 3 (model-choice; ruler VL-MC-v1.2): the quarterly arcane manifest.

(a) rN_recheck RC-09. The timer fires on a quarter's FIRST day (`*-01,04,07,10-01 09:00`), but the organ named the
    CURRENT quarter: the scheduled run wrote "2026-Q4" from hours of data, and the quarter that had just ended never
    got its timer-written manifest. A run in a quarter's first QUARTER_GRACE_DAYS -- the timer's own day, a
    Persistent=true catch-up after the machine slept through it, an owner's re-run after a failed fire -- now names AND
    covers the quarter just ended (its date range, its counts). Later in the quarter a run is a draft of the quarter
    so far, as the owner's mid-quarter runs were. --year/--quarter override either way.
(b) The council's voice files. The package lays <claude home>/skills/council-{seer,skeptic,architect,dreamer,
    synthesizer}/SKILL.md (install/install_council.sh); the organ looked only for the reference host's council-orin,
    -vael, -dessa, -kael-em and the author's persona, so on a stranger's install every voice spoke without its file. The
    package's names resolve first, the host's names as the fallback.

RED on the N bytes (voidloom f8f0c30 = artifacts_r2); GREEN on Round 3's. No model: the seam is stubbed.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import arcane_manifest as am

UTC = timezone.utc


def _clock(monkeypatch, *ymdh):
    """Set the organ's clock. The Round 3 organ reads it through _now(); the N bytes read datetime.now inside
    _quarter_label, so a stand-in datetime class is patched too (it is what makes these rows RED there, not error)."""
    fixed = datetime(*ymdh, tzinfo=UTC)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)
    monkeypatch.setattr(am, "datetime", _DT)
    if hasattr(am, "_now"):
        monkeypatch.setattr(am, "_now", lambda: fixed)
    return fixed


def _brain(tmp_path):
    db = tmp_path / "claude_memory.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, summary TEXT,
            category TEXT NOT NULL, importance REAL DEFAULT 0.5, source_project TEXT, source_context TEXT,
            created_at TEXT, updated_at TEXT, last_accessed TEXT, access_count INTEGER DEFAULT 0,
            is_archived INTEGER DEFAULT 0, version INTEGER DEFAULT 1, kind TEXT DEFAULT 'episodic');
        CREATE TABLE memory_tags (id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL, tag TEXT NOT NULL,
            UNIQUE(memory_id, tag));
        CREATE TABLE council_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger_type TEXT, topic TEXT,
            topic_category TEXT, rounds_completed INTEGER DEFAULT 0, transcript TEXT, synthesis TEXT, drew_input TEXT,
            decision_memory_id INTEGER, decision_weight_score REAL, participants TEXT, created_at TEXT,
            completed_at TEXT, status TEXT DEFAULT 'in_progress');
    """)
    # 6 memories inside 2026-Q3 (incl. its last second), 2 on Oct 1 (Q4's first hours), 1 in Q2
    for ts in ("2026-07-01 00:00:00", "2026-07-15 10:00:00", "2026-08-20 10:00:00", "2026-09-07 10:00:00",
               "2026-09-30 12:00:00", "2026-09-30 23:59:59", "2026-10-01 00:30:00", "2026-10-01 09:00:00",
               "2026-06-12 10:00:00"):
        c.execute("INSERT INTO memories (content, summary, category, importance, created_at) VALUES (?,?,?,?,?)",
                  ("memory written at %s" % ts, "m %s" % ts, "insight", 0.7, ts))
    c.commit()
    c.close()
    return str(db)


# ---------------------------------------------------------------- (a) the quarter a run names and covers
@pytest.mark.parametrize("when,label,start,end", [
    ((2026, 10, 1, 13, 0), "2026-Q3", "2026-07-01 00:00:00", "2026-10-01 00:00:00"),    # the timer's own day
    ((2026, 10, 9, 8, 0), "2026-Q3", "2026-07-01 00:00:00", "2026-10-01 00:00:00"),     # a catch-up a week later
    ((2027, 1, 1, 14, 0), "2026-Q4", "2026-10-01 00:00:00", "2027-01-01 00:00:00"),     # across the year
    ((2026, 4, 1, 13, 0), "2026-Q1", "2026-01-01 00:00:00", "2026-04-01 00:00:00"),
    ((2026, 7, 1, 13, 0), "2026-Q2", "2026-04-01 00:00:00", "2026-07-01 00:00:00"),
], ids=["oct-1-timer", "oct-9-catch-up", "jan-1-year-rollover", "apr-1", "jul-1"])
def test_a_run_in_a_quarters_first_days_names_and_covers_the_quarter_that_ended(tmp_path, monkeypatch, when, label,
                                                                               start, end):
    _clock(monkeypatch, *when)
    d = am.gather_quarter_data(_brain(tmp_path))["data"]
    assert (d["quarter"], d["date_range"]["start"], d["date_range"]["end"]) == (label, start, end), d


def test_the_quarter_just_ended_is_counted_whole_and_todays_hours_are_not(tmp_path, monkeypatch):
    _clock(monkeypatch, 2026, 10, 1, 13, 0)
    d = am.gather_quarter_data(_brain(tmp_path))["data"]
    assert d["total_memories_this_quarter"] == 6, d          # Jul 1 .. Sep 30 23:59:59; NOT the 2 written on Oct 1


def test_q4_covers_its_last_second(tmp_path, monkeypatch):
    """The old Q4 bound ended at 12-31 23:59:59 under a half-open `<`: that second was dropped."""
    assert am._quarter_date_range(2026, 4) == ("2026-10-01 00:00:00", "2027-01-01 00:00:00")


@pytest.mark.parametrize("when,label", [((2026, 11, 15, 10, 0), "2026-Q4"), ((2026, 9, 7, 10, 0), "2026-Q3"),
                                        ((2026, 6, 12, 10, 0), "2026-Q2")],
                         ids=["mid-quarter-draft", "owners-sep-7-run", "owners-jun-12-run"])
def test_a_run_later_in_the_quarter_is_a_draft_of_the_quarter_so_far(tmp_path, monkeypatch, when, label):
    _clock(monkeypatch, *when)
    assert am.gather_quarter_data(_brain(tmp_path))["data"]["quarter"] == label


def test_year_and_quarter_named_by_the_caller_win(tmp_path, monkeypatch):
    _clock(monkeypatch, 2026, 10, 1, 13, 0)
    d = am.gather_quarter_data(_brain(tmp_path), year=2026, quarter_number=4)["data"]
    assert d["quarter"] == "2026-Q4" and d["total_memories_this_quarter"] == 2, d


def test_the_scheduled_run_stores_the_manifest_under_the_quarter_that_ended(tmp_path, monkeypatch):
    """End to end, the unit's own entry point (run_full_manifest_pipeline) with the seam stubbed: the stored row,
    its label, year, quarter number and data snapshot all name 2026-Q3 when the timer fires on Oct 1."""
    _clock(monkeypatch, 2026, 10, 1, 13, 0)
    db = _brain(tmp_path)
    th = am._thinker
    text = "We closed the quarter with the dream organ storing its dreams again, and the storm taught us to wait."
    monkeypatch.setattr(th, "think", lambda prompt, **kw: th.ThinkResult(text * 2, "stub", "m", 0.1, "text"))
    r = am.run_full_manifest_pipeline(db, timeout_seconds=5)
    assert r["status"] == "ok" and r["quarter"] == "2026-Q3", r
    row = sqlite3.connect(db).execute("SELECT quarter, year, quarter_number, data_snapshot FROM arcane_manifests").fetchone()
    snap = json.loads(row[3])
    assert row[:3] == ("2026-Q3", 2026, 3) and snap["date_range"]["end"] == "2026-10-01 00:00:00", row[:3]


# ---------------------------------------------------------------- (b) the voice files, by the package's names
LENSES = {"orin": ("council-seer", "council-orin"), "vael": ("council-skeptic", "council-vael"),
          "dessa": ("council-architect", "council-dessa"), "kael-em": ("council-dreamer", "council-kael-em")}


def _skills(root, names):
    for n in names:
        (root / "skills" / n).mkdir(parents=True)
        (root / "skills" / n / "SKILL.md").write_text("VOICE FILE %s" % n)


def _voices_heard(tmp_path, monkeypatch, claude_home, persona=None):
    monkeypatch.setenv("WIZARD_CLAUDE_HOME", str(claude_home))
    monkeypatch.delenv("VOIDLOOM_SKILLS_DIR", raising=False)
    # The synthesizer seat is the brain's DECLARED persona when there is one (lane L6, 2026-10-15): pin the
    # declaration to this test's own file, so a persona.json beside the suite never decides what is heard.
    decl = tmp_path / "persona.json"
    monkeypatch.setenv("VOIDLOOM_PERSONA_FILE", str(decl))
    monkeypatch.delenv("VOIDLOOM_PERSONA", raising=False)
    if persona:
        decl.write_text(json.dumps({"default": persona, "personas": {persona: {"blurb": "x"}}}))
    monkeypatch.chdir(tmp_path)                       # no .claude/skills under the working directory
    # the brain-relative candidate (organs/../../skills) must not reach a real ~/.claude/skills when the suite runs
    # inside an installed brain: the module's own path is moved under tmp_path for the call
    _clock(monkeypatch, 2026, 10, 1, 13, 0)
    db = _brain(tmp_path)
    assert am.ensure_manifest_table(db)["status"] == "ok"         # the migration is read via the module's own path
    monkeypatch.setattr(am, "__file__", str(tmp_path / "brain" / "memory" / "organs" / "arcane_manifest.py"))
    th = am._thinker
    heard = []

    def think(prompt, **kw):
        heard.append(kw.get("system"))
        return th.ThinkResult("A section long enough to be kept by the organ's own acceptance rule, about the quarter.",
                              "stub", "m", 0.1, "text")
    monkeypatch.setattr(th, "think", think)
    am.run_full_manifest_pipeline(db, timeout_seconds=5)
    return heard


def test_the_packages_council_folders_are_the_voices(tmp_path, monkeypatch):
    home = tmp_path / "claude_home"
    _skills(home, [p for p, _h in LENSES.values()] + ["council-synthesizer"])
    heard = _voices_heard(tmp_path, monkeypatch, home)
    assert heard == ["VOICE FILE council-seer", "VOICE FILE council-skeptic", "VOICE FILE council-architect",
                     "VOICE FILE council-dreamer", "VOICE FILE council-synthesizer"], heard


def test_the_reference_hosts_folders_are_the_fallback(tmp_path, monkeypatch):
    """The lenses fall back to the host's own folder names. The synthesis has no hard-coded host folder any more: on a
    host whose brain declares a persona, that persona's skill folder IS the synthesizer's voice (the reference host
    declares one, so its synthesis is unchanged)."""
    home = tmp_path / "claude_home"
    _skills(home, [h for _p, h in LENSES.values()] + ["iris-vale"])
    heard = _voices_heard(tmp_path, monkeypatch, home, persona="Iris Vale")
    assert heard == ["VOICE FILE council-orin", "VOICE FILE council-vael", "VOICE FILE council-dessa",
                     "VOICE FILE council-kael-em", "VOICE FILE iris-vale"], heard


def test_an_undeclared_brain_never_borrows_a_persona_folder(tmp_path, monkeypatch):
    """No persona declared: a persona-named folder lying in skills/ is not the synthesizer; the package's is."""
    home = tmp_path / "claude_home"
    _skills(home, [p for p, _h in LENSES.values()] + ["council-synthesizer", "iris-vale"])
    heard = _voices_heard(tmp_path, monkeypatch, home)
    assert heard[-1] == "VOICE FILE council-synthesizer", heard


def test_the_packages_name_wins_when_both_are_laid(tmp_path, monkeypatch):
    home = tmp_path / "claude_home"
    _skills(home, [n for pair in LENSES.values() for n in pair] + ["council-synthesizer", "iris-vale"])
    heard = _voices_heard(tmp_path, monkeypatch, home)
    assert all(h and ("council-orin" not in h and "council-vael" not in h and "iris-vale" not in h) for h in heard), heard


def test_no_voice_file_is_said_not_silent(tmp_path, monkeypatch, caplog):
    home = tmp_path / "claude_home"
    (home / "skills").mkdir(parents=True)
    with caplog.at_level("WARNING"):
        heard = _voices_heard(tmp_path, monkeypatch, home)
    assert heard == [None] * 5, heard
    said = [r.getMessage() for r in caplog.records if "voice file" in r.getMessage()]
    assert len(said) == 5 and "council-seer / council-orin" in said[0], said
