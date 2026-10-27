# -*- coding: utf-8 -*-
"""2026-10-26 diagnostics: `voidloom doctor` read the Claude Code hook DEAD while
it fired in the very session asking.

The two "missed" sessions were the brain's own overnight thinker: headless
`claude -p` calls, whose transcripts carry ``"entrypoint": "sdk-cli"``. An
interactive session carries ``"cli"``. A headless session with no fire is not a
person the hook failed to reach, so it is not judged; it is reported apart.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import voidloom_heartbeat as hb  # noqa: E402

NOW = datetime.now(timezone.utc)


def ago(minutes: int) -> str:
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _transcript(path: Path, entrypoint: str, ts: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "queue-operation", "timestamp": ts},
             {"type": "user", "entrypoint": entrypoint, "timestamp": ts,
              "message": {"role": "user", "content": "hello"}}]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def _beats(*session_ids: str) -> dict:
    fires = [{"session_id": s, "ts": ago(1), "source": "startup"} for s in session_ids]
    return {"path": "/hb", "present": True, "readable": True, "fire_count": len(fires),
            "first_ts": ago(300), "last_ts": ago(1), "unparseable_lines": 0, "fires": fires}


def test_listing_reads_the_entrypoint_from_the_transcript_head(tmp_path):
    proj = tmp_path / "projects"
    _transcript(proj / "-home-x" / "aaaa.jsonl", "cli", ago(30))
    _transcript(proj / "-home-x--claude-memory" / "bbbb.jsonl", "sdk-cli", ago(20))
    (proj / "-home-x" / "cccc.jsonl").write_text('{"type":"summary"}\n')   # no entrypoint at all
    out = hb.list_platform_sessions("claude-code", env={"CLAUDE_CONFIG_DIR": str(tmp_path)})
    by_id = {s["id"]: s for s in out["sessions"]}
    assert by_id["aaaa"]["entrypoint"] == "cli" and by_id["aaaa"]["headless"] is False
    assert by_id["bbbb"]["entrypoint"] == "sdk-cli" and by_id["bbbb"]["headless"] is True
    assert by_id["cccc"]["entrypoint"] is None and by_id["cccc"]["headless"] is False


def _sessions(*rows):
    return {"root": "/x", "root_present": True, "count": len(rows),
            "sessions": [{"id": i, "started_at": ago(m), "headless": h} for i, m, h in rows]}


def test_an_unfired_headless_session_does_not_make_the_hook_dead():
    s = _sessions(("you-1", 50, False), ("thinker", 40, True), ("you-2", 10, False))
    r = hb.join_state(registered=True, heartbeats=_beats("you-1", "you-2"), sessions=s,
                      platform="claude-code", installed_at=ago(300))
    assert r["state"] == "LIVE", r["reason"]
    assert r["headless_sessions_not_judged"] == 1
    assert "thinker" not in r["sessions_in_window"]


def test_an_unfired_interactive_session_still_reads_dead():
    s = _sessions(("you-1", 50, False), ("you-missed", 40, False), ("thinker", 30, True))
    r = hb.join_state(registered=True, heartbeats=_beats("you-1"), sessions=s,
                      platform="claude-code", installed_at=ago(300))
    assert r["state"] == "DEAD"
    assert r["missed_sessions"] == ["you-missed"]


def test_fired_headless_sessions_cannot_push_a_persons_miss_out_of_the_window():
    """Council (VAEL's planted case): one interactive miss, then ten headless sessions that
    DID fire. If fired headless sessions joined the window they would fill the last 10 and
    the verdict would read LIVE over a person the hook never reached."""
    rows = [("you-missed", 100, False)] + [(f"thinker-{i}", 90 - i, True) for i in range(10)]
    s = _sessions(*rows)
    r = hb.join_state(registered=True, heartbeats=_beats(*[f"thinker-{i}" for i in range(10)], "you-old"),
                      sessions=_sessions(("you-old", 200, False), *rows),
                      platform="claude-code", installed_at=ago(300))
    assert r["state"] == "DEAD", r["reason"]
    assert r["missed_sessions"] == ["you-missed"]
    assert r["headless_sessions_not_judged"] == 10 and r["headless_sessions_fired"] == 10
    assert not any(x.startswith("thinker") for x in r["sessions_in_window"])
    # a headless session's fire joins the session the platform recorded: never "unjoined"
    assert r["fire_count_unjoined"] == 0 and "never recorded" not in r.get("note", "")


def test_only_the_exact_claude_p_entrypoint_is_headless(tmp_path):
    """Council (ORIN): the Claude Code Desktop app's entrypoint was never measured on this
    machine. Whatever it writes must stay JUDGED, or the doctor would stop watching the
    very users who never see a terminal banner. Only `sdk-cli` (claude -p) is headless."""
    proj = tmp_path / "projects" / "-home-x"
    for sid, ep in (("d1", "claude-desktop"), ("d2", "sdk-ts"), ("d3", "sdk-py"), ("d4", "claude-vscode")):
        _transcript(proj / f"{sid}.jsonl", ep, ago(10))
    out = hb.list_platform_sessions("claude-code", env={"CLAUDE_CONFIG_DIR": str(tmp_path)})
    assert all(s["headless"] is False for s in out["sessions"]), out["sessions"]


def test_headless_fires_alone_never_make_the_hook_live():
    """Review F1: with only headless fires and no install receipt, the window held no
    session a person opened and the verdict read LIVE over '0 of 0'."""
    s = _sessions(*[(f"you-{i}", 200 - i, False) for i in range(5)],
                  *[(f"thinker-{i}", 100 - i, True) for i in range(5)])
    r = hb.join_state(registered=True, heartbeats=_beats(*[f"thinker-{i}" for i in range(5)]), sessions=s,
                      platform="claude-code", installed_at=None)
    assert r["state"] != "LIVE", r["reason"]
    assert "no session a person opened has ever fired" in r.get("note", "")


def test_a_head_line_longer_than_the_read_still_yields_its_entrypoint(tmp_path):
    """Review F8: a claude -p prompt over 64 KB truncates the head line; the entrypoint is
    still near its start and must not be lost (a false DEAD otherwise)."""
    p = tmp_path / "projects" / "-home-x" / "big.jsonl"
    p.parent.mkdir(parents=True)
    rec = {"type": "user", "entrypoint": "sdk-cli", "timestamp": ago(5),
           "message": {"role": "user", "content": "x" * 90000}}
    p.write_text(json.dumps(rec) + "\n")
    out = hb.list_platform_sessions("claude-code", env={"CLAUDE_CONFIG_DIR": str(tmp_path)})
    assert out["sessions"][0]["headless"] is True


def test_the_window_says_whose_sessions_it_counted():
    """Review F5: the domain string must name what was measured."""
    s = _sessions(("you-1", 50, False), ("thinker", 40, True))
    r = hb.join_state(registered=True, heartbeats=_beats("you-1"), sessions=s,
                      platform="claude-code", installed_at=ago(300))
    assert "a person opened" in r["window"]["domain"] and "a person opened" in r["reason"]


def test_hooks_status_carries_the_headless_counts(monkeypatch):
    """Review 1 F6 / review 2 N10: `voidloom hooks status` shows the exclusion too."""
    import voidloom_hooks_status as hs
    monkeypatch.setattr(hb, "read_heartbeats", lambda p: _beats("you-1"))
    monkeypatch.setattr(hb, "list_platform_sessions",
                        lambda p: _sessions(("you-1", 50, False), ("thinker", 40, True)))
    row = hs._fired_rung("claude-code", True)
    assert row["headless_sessions_not_judged"] == 1 and row["headless_sessions_fired"] == 0
