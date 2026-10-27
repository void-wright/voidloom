# -*- coding: utf-8 -*-
"""M8 -- <brain>/THINKER_RECORD, and M9 -- `voidloom thinker set | status`, M14's thinker line and weekly check.

M8 (DESSA C-3): a SEPARATE record -- INSTALL_RECORD is rewritten whole by install.sh on every run, install.ps1 and
the stead write none -- key=value with round 3's machinery: prove before record, read back after, and a torn or
hand-edited record REFUSED on read (record_sha256 over every byte before it). Written ONLY by organs/thinker.py;
INSTALL_RECORD keeps a derived pointer.
M9 (R-Q3): `set` proves the backend in the timers' environment and only then records; a FAILED set leaves the
previous record BYTE-IDENTICAL; `--source voidstead` records who asked (the stead never runs install.sh). The
systemd-run half (the user manager with the units' env file) runs in the cold gate on a scratch brain; here the
probe runs `--probe-env direct`, which the record states.
M14: one health line for doctor / the digest / token_doctor ("unreachable N of 7 nights"), a weekly credential check
that alerts on the FIRST auth failure, and the Claude token's one-year expiry reminder ~30 days ahead.
"""
import ast
import datetime as dt
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
import thinker

SCRATCH = Path.home() / ".crucible_scratch"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_m89_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _claude(w, **b):
    fake = F.make_fake(w.bin, "claude", log=str(w.log), **b)
    F.save_cred(w, "claude")
    return fake


# ------------------------------------------------------------------ M8
def test_a_record_cannot_be_written_without_a_passing_probe(w):
    cand = {"schema": "1", "backend": "claude", "transport": "cli", "binary": "/x/claude"}
    with pytest.raises(thinker.RecordError):
        thinker._commit_record(cand, None)
    with pytest.raises(thinker.RecordError):
        thinker._commit_record(cand, thinker._Proven(False, cand, {}))
    other = dict(cand, binary="/y/claude")
    with pytest.raises(thinker.RecordError):
        thinker._commit_record(cand, thinker._Proven(True, other, {}))
    assert not thinker.record_path().exists()


def test_a_torn_or_edited_record_is_refused_on_read(w):
    _claude(w)
    F.record(thinker, "claude")
    rp = thinker.record_path()
    good = rp.read_bytes()
    assert thinker.parse_record(good)["backend"] == "claude"
    for name, bad in (("torn", good[:-20]), ("no-sum", good[:good.rindex(b"record_sha256")]),
                      ("edited", good.replace(b"backend=claude", b"backend=codex"))):
        rp.write_bytes(bad)
        with pytest.raises(thinker.RecordError):
            thinker.read_record()
        with pytest.raises(thinker.ThinkUnavailable) as ei:
            thinker.think("x", purpose="sleep_loop", timeout_s=10)
        assert ei.value.reason == "unconfigured", name
        assert "RECORD REFUSED" in thinker.health_line()
    rp.write_bytes(good)
    assert thinker.read_record()["backend"] == "claude"


def test_no_record_means_nobody_answered_and_organs_take_the_loud_path(w):
    import whisper_network
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=10)
    assert ei.value.reason == "unconfigured" and "voidloom thinker set" in ei.value.detail
    obs = [{"observation_type": "insight", "intensity": 0.7, "observation": "w"}]
    assert whisper_network._synthesize_llm(obs, {"insight": obs}, "a", "b") is None
    assert thinker.read_log()[-1]["note"] == "no-record"
    assert thinker.health_line().startswith("thinker: not chosen yet")


def test_only_the_seam_writes_the_record():
    """No packaged python other than organs/thinker.py names THINKER_RECORD except through the seam's reader, and no
    shell line in the installers writes it (a redirect, mv, cp, tee, sed -i, rm onto it)."""
    offenders = []
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("voidloom/tests/", ".git/")) or rel == "organs/thinker.py":
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        if "THINKER_RECORD" not in src:
            continue
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and (
                    n.value.strip() == "THINKER_RECORD" or n.value.rstrip().endswith(("/THINKER_RECORD",
                                                                                    "\\THINKER_RECORD"))):
                offenders.append("%s:%d names THINKER_RECORD" % (rel, n.lineno))
    for p in [ROOT / "install.sh", ROOT / "install" / "install_crons.sh"]:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "THINKER_RECORD" in line and not line.lstrip().startswith("#") and \
                    re.search(r"(>\s*\S*THINKER_RECORD|\b(mv|cp|tee|rm)\b.*THINKER_RECORD|sed\s+-i)", line):
                offenders.append("%s:%d writes THINKER_RECORD" % (p.name, i))
    assert offenders == [], offenders


def test_install_record_carries_a_derived_pointer():
    src = (ROOT / "install.sh").read_text(encoding="utf-8")
    body = src[src.index("write_install_record() {"):src.index("mv -f \"$_tmp\" \"$INSTALL_RECORD\"")]
    assert 'rec["thinker_record"]' in body and "_thk.read_record" in body and 'rec["thinker_backend"]' in body


# ------------------------------------------------------------------ M9
def test_a_failed_set_leaves_the_previous_record_byte_identical(w):
    fake = _claude(w)
    F.record(thinker, "claude")
    rp = thinker.record_path()
    before = rp.read_bytes()
    # a dead backend
    F.set_behaviour(fake, mode="fail", said="API Error: Connection refused")
    env = thinker.run(["set", "claude", "--probe-env", "direct", "--answered-by", "test"])
    assert env["_exit_code"] == 1 and env["code"] == "thinker_probe_failed" and env["data"]["record_unchanged"]
    assert rp.read_bytes() == before
    # an unauthenticated one
    F.set_behaviour(fake, mode="fail", said="Invalid API key (fake)")
    assert thinker.run(["set", "claude", "--probe-env", "direct"])["_exit_code"] == 1
    assert rp.read_bytes() == before
    # a backend whose tool block breaks
    F.set_behaviour(fake, mode="write")
    assert thinker.run(["set", "claude", "--probe-env", "direct"])["_exit_code"] == 1
    assert rp.read_bytes() == before
    # a backend that is not installed / has no credential
    assert thinker.run(["set", "kimi", "--probe-env", "direct"])["_exit_code"] == 3
    F.make_fake(w.bin, "kimi", log=str(w.log))
    env = thinker.run(["set", "kimi", "--probe-env", "direct", "--no-login"])
    assert env["_exit_code"] == 3 and "voidloom thinker login kimi" in env["_human"]
    assert rp.read_bytes() == before


def test_set_off_is_an_answer(w):
    _claude(w)
    F.record(thinker, "claude")
    env = thinker.run(["set", "off", "--answered-by", "owner"])
    assert env["_exit_code"] == 0
    rec = thinker.read_record()
    assert rec["backend"] == "off" and rec["previous_backend"] == "claude" and rec["answered_by"] == "owner"
    with pytest.raises(thinker.ThinkUnavailable) as ei:
        thinker.think("x", purpose="sleep_loop", timeout_s=10)
    assert ei.value.backend_id == "off" and "not now" in ei.value.detail
    assert thinker.health_line().startswith("thinker: off")
    assert thinker.nights()["unreachable"] == 0, "an owner's 'not now' is never an unreachable night"


def test_the_stead_sets_through_the_same_verb(w):
    _claude(w)
    env = thinker.run(["set", "claude", "--probe-env", "direct", "--source", "voidstead", "--pin", "abc123"])
    assert env["_exit_code"] == 0, env
    rec = thinker.read_record()
    assert (rec["source"], rec["pin"], rec["answered_by"]) == ("voidstead", "abc123", "voidstead")
    assert rec["probe_env"].startswith("direct") and rec["probe_nonce_ok"] == "true"
    assert rec["probe_tool_block"] == "held" and os.path.isabs(rec["binary"])


def test_without_systemd_run_the_timers_env_probe_refuses(w, monkeypatch):
    _claude(w)
    real = shutil.which
    monkeypatch.setattr(thinker.shutil, "which", lambda n, *a, **k: None if n == "systemd-run" else real(n, *a, **k))
    env = thinker.run(["set", "claude"])
    assert env["_exit_code"] == 1 and "systemd-run is not on PATH" in env["_human"]
    assert not thinker.record_path().exists()


def test_status_shows_backend_credential_age_and_expiry(w):
    _claude(w)
    saved = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=340)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (w.thinker_home / "claude.env").write_text("# saved_at=%s by test\nCLAUDE_CODE_OAUTH_TOKEN=%s\n"
                                               % (saved, "t" * 40))
    F.record(thinker, "claude")
    rep = thinker.status_report()
    assert rep["backend"] == "claude" and rep["proven_at"]
    c = rep["credential"]
    assert c["fingerprint"] == thinker._fp("t" * 40) and c["age_days"] == 340 and c["legacy"] is False
    assert c["next_expiry"] == (dt.datetime.strptime(saved, "%Y-%m-%dT%H:%M:%SZ")
                                + dt.timedelta(days=365)).strftime("%Y-%m-%d")
    line = thinker.health_line(rep)
    assert "unreachable 0 of 7 nights" in line and "expires" in line
    env = thinker.run(["status"])
    assert env["_exit_code"] == 0 and env["_human"].startswith("thinker: claude")


# ------------------------------------------------------------------ M14
def test_unreachable_n_of_7_nights(w):
    rows = []
    # noon of the LOCAL today, written in UTC as THINKER_LOG writes it: nights() counts LOCAL calendar dates. (Round 1
    # iteration 2 found this test clock-dependent: with UTC noon it failed whenever the UTC date was already
    # tomorrow's -- 00:00-04:00 UTC on an EDT host, the hours the landing arms ran; it failed on the iteration-1 bytes too.)
    today = dt.datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0).astimezone(dt.timezone.utc)
    for d, outcome, reason in ((0, "ok", ""), (1, "unavailable", "auth"), (1, "unavailable", "auth"),
                               (2, "unavailable", "unreachable"), (2, "ok", ""), (3, "timeout", "timeout"),
                               (4, "unavailable", "unconfigured"), (9, "unavailable", "auth")):
        rows.append({"at": (today - dt.timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ"), "purpose": "sleep_loop",
                     "backend": "claude", "outcome": outcome, "reason": reason})
    n = thinker.nights(rows)
    assert n["unreachable"] == 2 and n["of"] == 7, n          # night 1 (all auth) and night 3 (timeout)
    assert n["nights_with_calls"] == 4


def test_weekly_check_alerts_on_the_first_auth_failure_and_the_expiry(w):
    fake = _claude(w)
    soon = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=350)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (w.thinker_home / "claude.env").write_text("# saved_at=%s\nCLAUDE_CODE_OAUTH_TOKEN=%s\n" % (soon, "t" * 40))
    F.record(thinker, "claude")
    r = thinker.weekly_check(timeout_s=30)
    assert r["probe"]["passed"] and any("REMINDER" in a and "expires" in a for a in r["alerts"]), r
    F.set_behaviour(fake, mode="fail", said="Invalid API key (fake)")
    r = thinker.weekly_check(timeout_s=30)
    assert any(a.startswith("ALERT") for a in r["alerts"]), r
    r = thinker.weekly_check(timeout_s=30)
    assert not any(a.startswith("ALERT") for a in r["alerts"]), "only the FIRST auth failure alerts"
    assert [x["outcome"] for x in thinker.read_log() if x.get("purpose") == "thinker_check"] == ["ok", "auth", "auth"]


def test_doctor_and_digest_carry_the_thinker_line(w, monkeypatch):
    """EXECUTED: the weekly digest of the thinker's OWN brain runs the weekly check (one probe, logged) and stores
    the thinker line in the digest; a digest of any other database (a test copy) never probes; `voidloom doctor`
    reports the same line."""
    import sqlite3
    import subprocess
    import sys
    import whisper_network
    from voidloom.tests._owned_brain import packaged_brain
    monkeypatch.setattr(whisper_network, "_encode_for_embedding", lambda text, conn=None: None)  # no model, no GPU
    _claude(w)
    F.record(thinker, "claude")
    db = packaged_brain(w.brain / "claude_memory.db")
    c = sqlite3.connect(str(db))
    whisper_network.capture_whisper(c, observation="an observation for the digest", observation_type="insight",
                                    intensity=0.6)
    c.commit()
    c.close()
    n_calls = len([r for r in F.read_log(w.log) if not r.get("end")])
    r = whisper_network.generate_weekly_digest(str(db), mode="heuristic")
    assert r["status"] == "ok" and r["thinker"]["checked"] is True, r
    assert r["thinker"]["line"].startswith("thinker: claude")
    assert len([x for x in F.read_log(w.log) if not x.get("end")]) == n_calls + 1, "the weekly check made ONE call"
    c = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    content = c.execute("SELECT content FROM memories WHERE id=?", (r["digest_memory_id"],)).fetchone()[0]
    c.close()
    assert "Overnight thinker: thinker: claude" in content
    other = w.root / "copy" / "claude_memory.db"
    other.parent.mkdir()
    packaged_brain(other)
    r2 = whisper_network.generate_weekly_digest(str(other), mode="heuristic")
    assert r2["thinker"]["checked"] is False, "a digest of another database probed the owner's thinker"
    # doctor, through the CLI, with the organs laid in the brain the way an install lays them
    (w.brain / "organs").symlink_to(ROOT / "organs")
    env = {k: os.environ[k] for k in ("HOME", "PATH", "WIZARD_LIBRARY_DB", "TMPDIR") if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    p = subprocess.run([sys.executable, "-B", "-m", "voidloom", "doctor", "--json"], capture_output=True, text=True,
                       env=env, timeout=300, cwd=str(w.root))
    import json as _j
    doc = _j.loads(p.stdout[p.stdout.index("{"):])
    assert doc["data"]["thinker"]["line"].startswith("thinker: claude"), doc["data"].get("thinker")
    assert doc["data"]["thinker"]["nights"]["of"] == 7
