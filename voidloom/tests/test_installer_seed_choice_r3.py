# -*- coding: utf-8 -*-
"""Round 3 G1, item 2 (the installer's half): on an UPGRADE the seed pack's new entries are the OWNER's choice -- asked at a
terminal, or taken from --load-seed, and with neither REPORTED, never loaded silently -- and when the acceptance gate fails,
the DONE banner names EXACTLY which checks failed and what each means (memory #23616: the blanket "Nothing above is a claim
that it works", printed over the installer's own seed policy, sent the owner to re-run a finished install).

The shell pieces are EXTRACTED VERBATIM from install.sh (each function by its own braces; the two DONE blocks by their
column-0 `if`/`fi`), then run in bash -- under a scripted pty for the question -- against a brain built from the package's
own blank brain and seed pack. The full ambient upgrade is exercised wet in the round's gate (r3_g1/amb/amb_gate.sh).
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from voidloom.tests.test_tier1_ambient import _drive_tty

ROOT = Path(os.environ.get("VOIDLOOM_ROOT") or Path(__file__).resolve().parents[2]).resolve()
SH = ROOT / "install.sh"
SCRATCH = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
PACK = ROOT / "seed" / "seed_pack.json"
Q_SEED = r"Add them now\? \(y or n\):"


def _function(src: str, name: str) -> str:
    lines = src.splitlines()
    i = next(k for k, l in enumerate(lines) if re.match(r"^%s\(\) \{" % re.escape(name), l))
    j = next(k for k in range(i, len(lines)) if lines[k] == "}")
    return "\n".join(lines[i:j + 1])


def _block(src: str, first: str) -> str:
    lines = src.splitlines()
    i = next(k for k, l in enumerate(lines) if l == first)
    j = next(k for k in range(i + 1, len(lines)) if lines[k] == "fi")
    return "\n".join(lines[i:j + 1])


SRC = SH.read_text(encoding="utf-8")
SEED_FUNCS = "\n".join(_function(SRC, n) for n in ("ask_tty", "ask_say", "_ask_trim", "ask_line", "seed_plan_read",
                                                    "seed_check_yn", "seed_settle"))
GATE_READ = _function(SRC, "gate_read")
DONE_CHOICES = _block(SRC, 'if [ -n "$GATE_CHOICES" ]; then')
DONE_BANNER = _block(SRC, 'if [ "${ACCEPT_OK:-1}" -ne 1 ]; then')


@pytest.fixture
def w():
    SCRATCH.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="installer_seed_r3_", dir=str(SCRATCH)))
    db = root / "claude_memory.db"
    shutil.copy2(str(ROOT / "seed" / "claude_memory_blank.db"), str(db))
    env = dict(os.environ, WIZARD_LIBRARY_DB=str(db), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "schema_migrator.py"), "apply", "--db", str(db)],
                       capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr[-400:]
    yield root, db
    shutil.rmtree(str(root), ignore_errors=True)


def _lite(db, *args):
    env = dict(os.environ, WIZARD_LIBRARY_DB=str(db), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", str(ROOT / "organs" / "claude_memory_lite.py")] + list(args),
                          capture_output=True, text=True, env=env, timeout=300)


def _older_brain(db):
    """A brain holding all but the pack's LAST entry (one 'new' entry for the owner to choose)."""
    assert _lite(db, "load-seed", "--file", str(PACK)).returncode == 0
    last = [t for t in json.loads(PACK.read_text())["memories"][-1]["tags"].split(",") if t.startswith("anchor:seed_")][0]
    c = sqlite3.connect(str(db))
    rid = c.execute("SELECT memory_id FROM memory_tags WHERE tag = ?", (last,)).fetchone()[0]
    c.execute("DELETE FROM memory_tags WHERE memory_id = ?", (rid,))
    c.execute("DELETE FROM memories WHERE id = ?", (rid,))
    c.commit()
    c.close()


def _script(root, db, shape="ambient", load_seed=0, dry=0):
    s = root / "seed_settle.sh"
    s.write_text("#!/bin/bash\nset -uo pipefail\nsay() { printf '[install] %%s\\n' \"$*\"; }\n%s\n"
                 "SHAPE=%s; PKG_ROOT=%s; DB_PATH=%s; PY_SYS=%s; LOAD_SEED=%d; DRY_RUN=%d\n"
                 "SEED_LOAD=0; SEED_HOW=''; SEED_PLAN_NEW=0; SEED_PLAN_CHG=0; SEED_PLAN_VER=''\n"
                 "seed_settle\necho \"RESULT SEED_LOAD=$SEED_LOAD HOW=$SEED_HOW NEW=$SEED_PLAN_NEW CHG=$SEED_PLAN_CHG\"\n"
                 % (SEED_FUNCS, shape, ROOT, db, sys.executable, load_seed, dry), encoding="utf-8")
    return s


def _bytes(db):
    return Path(str(db)).read_bytes()


@pytest.mark.parametrize("answers,want,how", [
    ([(Q_SEED, "y")], "1", "answered y at the terminal"),
    ([(Q_SEED, "n")], "0", "answered n at the terminal"),
    ([(Q_SEED, "maybe"), (Q_SEED, "yes")], "1", "answered y at the terminal"),
])
def test_at_a_terminal_the_owner_is_asked_and_the_answer_is_taken(w, answers, want, how):
    root, db = w
    _older_brain(db)
    before = _bytes(db)
    rc, tty, out, err, left = _drive_tty(["bash", str(_script(root, db))], dict(os.environ), answers, str(root), timeout=120)
    assert (rc, left) == (0, []), (tty[-1200:], err[-600:])
    ver = json.loads(PACK.read_text(encoding="utf-8"))["seed_pack_version"]   # derived, never typed (1.6.0 broke a literal)
    assert "seed pack %s has 1 new and 0 changed entries this brain has not received" % ver in tty, tty[-1200:]
    assert "An upgrade adds none by itself" in tty
    assert "RESULT SEED_LOAD=%s HOW=%s NEW=1 CHG=0" % (want, how) in out, out
    if len(answers) > 1:
        assert "Answer y (add them) or n" in tty
    assert _bytes(db) == before, "the question itself wrote to the brain"


def test_with_no_terminal_and_no_flag_the_entries_are_reported_never_loaded(w):
    root, db = w
    _older_brain(db)
    before = _bytes(db)
    p = subprocess.run(["setsid", "bash", str(_script(root, db))], stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=120)
    assert p.returncode == 0, p.stderr[-600:]
    assert "RESULT SEED_LOAD=0 HOW=no terminal and no --load-seed NEW=1 CHG=0" in p.stdout, p.stdout
    assert "NOT added, reported as your choice" in p.stdout, p.stdout
    assert _bytes(db) == before


def test_the_flag_is_the_owners_answer_and_nothing_is_asked(w):
    root, db = w
    _older_brain(db)
    p = subprocess.run(["setsid", "bash", str(_script(root, db, load_seed=1))], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120)
    assert "RESULT SEED_LOAD=1 HOW=--load-seed NEW=1 CHG=0" in p.stdout, p.stdout


@pytest.mark.parametrize("shape,prepare", [("full", True), ("ambient", False)], ids=["a-fresh-shape", "nothing-to-add"])
def test_no_question_where_there_is_nothing_to_choose(w, shape, prepare):
    root, db = w
    if prepare:
        _older_brain(db)
    else:
        assert _lite(db, "load-seed", "--file", str(PACK)).returncode == 0      # the whole pack: nothing new
    p = subprocess.run(["setsid", "bash", str(_script(root, db, shape=shape))], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=120)
    assert "RESULT SEED_LOAD=0 HOW= " in p.stdout and "Seed pack:" not in p.stdout, p.stdout


def _gate_json(root, db, *flags):
    env = dict(os.environ, WIZARD_LIBRARY_DB=str(db), WIZARD_LIBRARY_SEED=str(PACK), PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, "-B", str(ROOT / "install" / "self_check.py"), "--json"] + list(flags),
                       capture_output=True, text=True, env=env, timeout=300)
    f = root / "sc.json"
    f.write_text(p.stdout, encoding="utf-8")
    return p.returncode, f


def _done(root, sc_json, accept_ok):
    s = root / "done.sh"
    s.write_text("#!/bin/bash\nset -uo pipefail\nsay() { printf '[install] %%s\\n' \"$*\"; }\n"
                 "warn() { printf '[install] WARNING: %%s\\n' \"$*\" >&2; }\nappend_install_log() { :; }\n"
                 "PY_SYS=%s; INSTALL_RECORD=/nonexistent/INSTALL_RECORD; ACCEPT_OK=%d\n%s\n"
                 "_sc_lines=\"$(gate_read %s)\"\n"
                 "GATE_SUMMARY=\"$(printf '%%s\\n' \"$_sc_lines\" | awk -F'\\t' '$1 == \"SUMMARY\" { print $2 \"/\" $3; exit }')\"\n"
                 "GATE_FAILS=\"$(printf '%%s\\n' \"$_sc_lines\" | awk -F'\\t' '$1 == \"FAIL\" || $1 == \"ERROR\"')\"\n"
                 "GATE_CHOICES=\"$(printf '%%s\\n' \"$_sc_lines\" | awk -F'\\t' '$1 == \"CHOICE\" { print $2 }')\"\n"
                 "%s\n%s\nexit 0\n" % (sys.executable, accept_ok, GATE_READ, sc_json, DONE_CHOICES, DONE_BANNER),
                 encoding="utf-8")
    return subprocess.run(["bash", str(s)], capture_output=True, text=True, timeout=120)


def test_a_failed_gate_names_each_failed_check_and_what_it_means(w):
    root, db = w                                   # a brain with no seed pack, judged as a FRESH install: real failures
    rc, f = _gate_json(root, db)
    assert rc == 1
    p = _done(root, f, 0)
    assert p.returncode == 5, (p.stdout, p.stderr)
    d = json.loads(f.read_text())
    failed = [c for c in d["checks"] if c["required"] and not c["ok"]]
    assert failed
    assert "ACCEPTANCE GATE FAILED — %d required check(s) did not pass" % len(failed) in p.stderr, p.stderr
    for c in failed:
        assert "  - %s: %s" % (c["name"], c["means"]) in p.stderr, (c["name"], p.stderr)
    assert "Nothing above is a claim" not in p.stdout + p.stderr


def test_an_upgrade_whose_only_gap_is_a_choice_passes_and_says_so_as_a_choice(w):
    root, db = w
    _older_brain(db)
    rc, f = _gate_json(root, db, "--seed-optional")
    assert rc == 0
    p = _done(root, f, 1)
    assert p.returncode == 0, p.stderr
    assert "Your choice (not a failure) -- the acceptance gate found:" in p.stdout, p.stdout
    assert "YOUR choice, not a failure" in p.stdout and "ACCEPTANCE GATE FAILED" not in p.stderr, (p.stdout, p.stderr)


def test_the_old_blanket_sentence_is_gone_from_the_installer():
    assert "Nothing above is a claim that it works" not in SRC
