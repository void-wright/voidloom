# -*- coding: utf-8 -*-
"""The r1 reflection's fix pass for the gemini backend (F1-F7, iterations/r1_reflection.md) -- each test RED on
integrate/gemini f504aec and GREEN after.

  F1 the persistent home's evidence walk is bounded by TIME, not by an entry count (a growing agy history must never
     make a re-proof read 'unmeasured'); agy's own per-conversation stores are pruned (older than keep_days) under the
     gemini lock before a call starts, never the sign-in file; measured.t8 carries the RECURSIVE growth.
  F2 nothing a user reads promises a per-answer model check agy cannot give (its reply names no model).
  F3 TMPDIR is the brain's own <home>/tmp for every agy call (agy wrote a feature-flag cache to the shared /tmp).
  F4 bubblewrap is a listed prerequisite, and the installer checks it BEFORE the guide's Part E.
  F5 an old scripted `--thinker d --thinker-endpoint ...` refusal carries the d-letter note.
  F6 each program's "adds" names what install/write_set.json declares its vendor's installer writes.
  F7 the facts line names the program (Antigravity (agy)), not the backend id.
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from voidloom.tests import _thinker_fakes as F
from voidloom.tests import _thinker_gemini_fake as G
import thinker

ROOT = Path(thinker.__file__).resolve().parent.parent
# The Gemini backend refuses to run without a working bubblewrap sandbox (thinker._bwrap_ready, decided by
# execution). Cases that exercise a REAL call need one; elsewhere they skip, loudly, with the reason.
_BWRAP, _BWRAP_WHY = thinker._bwrap_ready()
needs_bwrap = pytest.mark.skipif(not _BWRAP, reason="bubblewrap cannot make a sandbox here: %s" % _BWRAP_WHY)
SCRATCH = Path.home() / ".crucible_scratch"
GUIDE = ROOT / "docs" / "OVERNIGHT_THINKER_SETUP.md"


@pytest.fixture
def w(monkeypatch):
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="thinker_gemini_fix_", dir=str(SCRATCH)))
    yield F.world(monkeypatch, root)
    shutil.rmtree(str(root), ignore_errors=True)


def _entry():
    return thinker.load_catalog()["backends"]["gemini"]


# ------------------------------------------------------------------------------------------------------------- F1
def test_f1_a_persistent_home_bigger_than_any_count_is_still_searched_completely(tmp_path, monkeypatch):
    call = tmp_path / "call"
    for d in ("cwd", "home"):
        (call / d).mkdir(parents=True)
    big = tmp_path / "persist"
    big.mkdir()
    for i in range(300):
        (big / ("f%04d" % i)).write_text("")
    (big / "probe-xyz").write_text("")
    # an entry count, however large, is a number a growing store must eventually cross: none may decide it
    monkeypatch.setattr(thinker, "_EVIDENCE_WALK_CAP_PERSISTENT", 10, raising=False)
    ev = thinker._sandbox_evidence(call, "probe-xyz", (big,))
    assert ev["home_complete"] is True and ev["home_hits"] == [str(big / "probe-xyz")], ev


def test_f1_the_bound_is_wall_clock_time(tmp_path, monkeypatch):
    call = tmp_path / "call"
    for d in ("cwd", "home"):
        (call / d).mkdir(parents=True)
    big = tmp_path / "persist"
    (big / "a" / "b").mkdir(parents=True)
    monkeypatch.setattr(thinker, "_EVIDENCE_WALK_SECONDS_PERSISTENT", -1.0, raising=False)
    ev = thinker._sandbox_evidence(call, "probe-xyz", (big,))
    assert ev["home_complete"] is False, "a walk past its seconds reads incomplete (never a silent pass)"


def _populated_home(w, days_old=10):
    ch = Path(w.thinker_home) / "gemini-home"
    base = ch / ".gemini" / "antigravity-cli"
    tok = G.save_brain_sign_in(w, "the-brain-sign-in")
    old_t = time.time() - days_old * 86400
    made = {"old": [], "new": []}
    for kind, t in (("old", old_t), ("new", None)):
        cid = "%s-0000-conv" % kind
        files = [base / "conversations" / (cid + ".db"), base / "implicit" / ("%s-impl.pb" % kind),
                 base / "annotations" / ("%s.pbtxt" % kind), base / "presence" / ("%s.lock" % kind),
                 ch / "tmp" / ("%s-cache.json" % kind)]
        for f in files:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x")
        bdir = base / "brain" / cid
        (bdir / "sub").mkdir(parents=True)
        (bdir / "sub" / "note.md").write_text("x")
        for p in files + [bdir]:
            if t:
                os.utime(str(p), (t, t))
        made[kind] = files + [bdir]
    con = sqlite3.connect(str(base / "conversation_summaries.db"))
    con.execute("CREATE TABLE conversation_summaries (conversation_id text PRIMARY KEY, title text)")
    con.executemany("INSERT INTO conversation_summaries VALUES (?, ?)", [("old-0000-conv", "o"), ("new-0000-conv", "n")])
    con.commit()
    con.close()
    os.utime(str(tok), (old_t, old_t))        # an OLD sign-in file is never a candidate
    return ch, base, tok, made


def test_f1_prune_removes_old_history_and_its_index_rows_and_never_the_sign_in(w):
    ch, base, tok, made = _populated_home(w)
    res = thinker.prune_history(_entry())
    assert not res["errors"], res
    assert all(not p.exists() for p in made["old"]), [str(p) for p in made["old"] if p.exists()]
    assert all(p.exists() for p in made["new"])
    assert tok.is_file() and tok.read_text() == "the-brain-sign-in"
    rows = [r[0] for r in sqlite3.connect(str(base / "conversation_summaries.db"))
            .execute("SELECT conversation_id FROM conversation_summaries")]
    assert rows == ["new-0000-conv"] and res["index_rows"] == 1
    again = thinker.prune_history(_entry())
    assert "within the last" in again.get("skipped", ""), "at most once per every_hours"


def test_f1_a_store_that_holds_the_sign_in_is_refused(w):
    ch, base, tok, made = _populated_home(w)
    e = json.loads(json.dumps(_entry()))
    e["history"]["stores"] = [".gemini/antigravity-cli"]
    res = thinker.prune_history(e, force=True)
    assert tok.is_file() and any("holds the sign-in" in x for x in res["errors"]), res


@needs_bwrap
def test_f1_prune_runs_under_the_lock_before_the_call_when_on(w, monkeypatch):
    G.make_fake_agy(w.bin, good_token="the-brain-sign-in", log=str(w.log))
    ch, base, tok, made = _populated_home(w)
    F.record(thinker, "gemini")
    cat = thinker.load_catalog()
    cat["backends"]["gemini"]["history"]["prune"] = True
    monkeypatch.setattr(thinker, "load_catalog", lambda path=None: cat)
    (ch / ".thinker-pruned").unlink(missing_ok=True)
    thinker.think("Say hello.", purpose="sleep_loop", timeout_s=60)
    assert not made["old"][0].exists() and made["new"][0].exists()
    assert (ch / ".thinker-pruned").is_file()


def test_f1_pruning_ships_on_with_its_measurement():
    h = _entry()["history"]
    assert h["prune"] is True and h["keep_days"] == 7 and "passed" in h["_doc"] and "the sign-in kept" in h["_doc"]


def test_f1_t8_states_the_recursive_growth():
    t8 = _entry()["measured"]["t8"]
    assert "~20 filesystem entries" in t8 and "450 KB" in t8 and "573 entries" in t8
    assert "1 entry per call" not in t8 and "years of headroom" not in t8


# ------------------------------------------------------------------------------------------------------------- F2
def test_f2_no_user_facing_text_promises_a_per_answer_model_check():
    g = GUIDE.read_text(encoding="utf-8")
    assert "the answer is thrown away" not in g
    assert "`agy models`" in g and "refuses a made-up model name" in g and "does not say which model answered" in g
    src = Path(thinker.__file__).read_text(encoding="utf-8")
    assert "test question answered by %s" not in src, "the login line must name the PINNED model, not 'answered by'"
    assert "asked of the pinned %s" in src


# ------------------------------------------------------------------------------------------------------------- F3
@needs_bwrap
def test_f3_every_agy_call_gets_the_brains_own_tmpdir(w):
    G.make_fake_agy(w.bin, log=str(w.log))
    G.save_brain_sign_in(w)
    F.record(thinker, "gemini")
    thinker.think("Say hello.", purpose="sleep_loop", timeout_s=60)
    gh = str(Path(w.thinker_home) / "gemini-home")
    calls = [r for r in F.read_log(w.log) if "argv" in r]
    p_calls = [c for c in calls if "-p" in c["argv"]]
    assert p_calls and all(c["env"].get("TMPDIR") == gh + "/tmp" for c in p_calls), [c["env"].get("TMPDIR")
                                                                                     for c in p_calls]
    assert os.path.isdir(gh + "/tmp")
    listing = [c for c in calls if c["argv"][:1] == ["models"]]
    assert all(c["env"].get("TMPDIR") == gh + "/tmp" for c in listing)
    ver = [c for c in calls if c["argv"][:1] == ["--version"]]
    assert ver and all(c["env"].get("TMPDIR") == c["env"]["HOME"] for c in ver), "the version question: its own throwaway"


# ------------------------------------------------------------------------------------------------------------- F4
def test_f4_bubblewrap_is_a_listed_prerequisite():
    g = GUIDE.read_text(encoding="utf-8")
    part_e = g[g.index("Part E: Gemini (option d)"):]
    need = part_e[part_e.index("**What you need first:**"):part_e.index("### E1.")]
    assert "bubblewrap" in need and "sudo apt install bubblewrap" in need
    for f in ("README.md", "docs/ONBOARDING_GUIDE.md", "seed/seed_pack.json"):
        assert "bubblewrap" in (ROOT / f).read_text(encoding="utf-8"), f


def test_f4_the_installer_checks_bwrap_before_the_program_offer_and_the_part():
    sh = (ROOT / "install.sh").read_text(encoding="utf-8")
    body = sh[sh.index("thinker_program_settle() {"):sh.index("thinker_ask() {")]
    i = body.index('bwrap --dev-bind / / -- /bin/true')
    assert i < body.index('"$THINKER_GET_PY" check'), "checked before anything else about the program"
    assert "cause: no-sandbox" in body and "sudo apt install bubblewrap" in body
    cause = thinker.NO_SANDBOX_CAUSE
    assert cause.replace("`", "\\`") in body or cause in body, "the same sentence as thinker.py NO_SANDBOX_CAUSE"


# ------------------------------------------------------------------------------------------------------------- F5
def test_f5_an_old_scripted_d_other_refusal_carries_the_letter_note(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
    p = subprocess.run(["bash", str(ROOT / "install.sh"), "--thinker", "d", "--thinker-endpoint",
                        "http://127.0.0.1:11434"], cwd=str(tmp_path), env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 2, (p.returncode, p.stderr[-600:])
    assert "the letter d now means d) Gemini" in p.stderr and "--thinker local" in p.stderr, p.stderr[-600:]
    assert not (tmp_path / ".claude").exists()


# ------------------------------------------------------------------------------------------------------------- F6
VENDOR = {"claude": "Anthropic", "codex": "npm", "kimi": "Moonshot|Kimi Code", "gemini": "Google|agy install"}


def _folder(path):
    p = path.replace("${HOME}", "~").replace("${LOCAL_BIN}", "~/.local/bin").replace("${CACHE_HOME}", "~/.cache")
    p = re.sub(r"/\*\*$", "", p)
    if re.search(r"/(\.bashrc|\.bash_profile|\.profile|\.zshrc|\.zprofile|config\.fish)$", p):
        return "startup file"
    if p.startswith("~/.local/bin/"):
        return "~/.local/bin"
    return p


def test_f6_each_programs_adds_line_names_what_its_installer_is_declared_to_write():
    words = thinker.load_catalog()["question"]["program_words"]
    ws = json.loads((ROOT / "install" / "write_set.json").read_text(encoding="utf-8"))
    writes = [x for st in ws["steps"] for x in st.get("writes", [])]
    for bid, pat in VENDOR.items():
        mine = [x for x in writes if re.search(pat, x.get("why", ""))]
        assert mine, bid
        adds = words[bid]["adds"]
        missing = sorted(set(_folder(x["path"]) for x in mine if _folder(x["path"]) not in adds))
        assert not missing, (bid, adds, missing)
        if any(_folder(x["path"]) == "startup file" for x in mine):
            assert "possibly a PATH line" in adds, (bid, adds)


# ------------------------------------------------------------------------------------------------------------- F7
def test_f7_the_facts_line_names_the_program(w):
    facts = thinker.question_facts()
    d = [f for fs in facts["facts"].values() for f in fs if "Antigravity" in f or "gemini is not installed" in f]
    assert d and all("gemini is not installed" not in f for f in d), d
    assert any(f == "Antigravity (agy) is not installed on this machine" for f in d), d
