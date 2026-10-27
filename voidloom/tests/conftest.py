# -*- coding: utf-8 -*-
"""THE declared way to run this suite, and the state isolation it needs.

    cd ~/.claude/memory && python3 -m pytest voidloom/tests/ -q

That command is the deliverable of this file as much as the fixtures are.

CRUCIBLE-3 seat D8-F6: there was no `conftest.py`, no `pytest.ini`, no
`pyproject.toml`, no CI definition, and zero occurrences of the word `pytest` in
`README.md`. "Run the tests" was not a command, it was a memory — and three seats
in one run invented three invocations and published three different counts. The
package's own slow-gate harness names this defect and fixes it for the gates
(`voidloom/tests/harness/run_all.py`: *"the harnesses existed, were good, and were
invoked by a hand… so 'run the gates' is a command rather than a memory"*). The
fast surface never got the same treatment.

D8-F6 also named the specific thing this file's absence had already cost:

  > *"The absent conftest.py is not a style nit here — it is the specific missing
  > part that would have contained F4."*

F4 was a test that cleared `claude_memory._active_model_cache` — a module global —
inside a monkeypatch loop and never restored it, leaving the process cached on the
last tier and deterministically failing two tests in ANOTHER FILE with
`db_uninitialized` against a brain holding 72 tables and 19,535 memories. The
suite told the operator their data was gone and told them to run `init`, which is
the one action a user with a healthy brain should least like to take.

So the reset lives here, autouse and suite-wide, rather than in the one file that
happened to break. A guard scoped to the file where the bug was found only guards
against that bug.

ENVIRONMENT, all optional and all reported by the header below:
  WIZARD_LIBRARY_DB / CLAUDE_MEMORY_DB   point the host-bound classes at a brain
  VOIDLOOM_TESTS_QUIET=1                 suppress the header
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]          # ~/.claude/memory
for _p in (str(_ROOT), str(_ROOT / "organs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# STATE ISOLATION
# ---------------------------------------------------------------------------
# Module globals in the ENGINE are shared by every test in a process. pytest's
# own monkeypatch restores env vars and attributes; it knows nothing about a bare
# `{}` at module scope. Each entry below is a global a test has already been
# caught mutating, or one whose mutation would have the same blast radius.
_ENGINE_GLOBALS = ("_active_model_cache",)


@pytest.fixture(autouse=True)
def _restore_engine_globals():
    """Snapshot and restore the engine's shared caches around EVERY test.

    Not a `clear()`: a clear would throw away a cache a previous test populated
    legitimately and make the suite order-dependent in the other direction. Save,
    yield, restore — so a test may mutate freely and the next one starts where
    this one did.
    """
    try:
        import claude_memory as cm  # type: ignore
    except Exception:
        yield                    # no engine on this host; nothing to isolate
        return
    saved = {}
    for name in _ENGINE_GLOBALS:
        obj = getattr(cm, name, None)
        if isinstance(obj, dict):
            saved[name] = dict(obj)
    try:
        yield
    finally:
        for name, before in saved.items():
            obj = getattr(cm, name, None)
            if isinstance(obj, dict):
                obj.clear()
                obj.update(before)


# ---------------------------------------------------------------------------
# NO CHILD OF THIS SUITE CAN REACH THE TERMINAL IT WAS RUN FROM (model-choice round 1)
# ---------------------------------------------------------------------------
# The installer asks its questions (the overnight thinker's a-e, the GPU pair) on /dev/tty whenever it has one -- THE
# ASK HELPER's rule, deliberately: an installing agent in a pty is still asked. So a human who ran this suite FROM A
# TERMINAL was asked by every installer test that did not pass --thinker (measured under a pty: the question printed
# on the operator's terminal and the test waited for an answer), while every measured arm ran without a controlling
# terminal and never saw it. A child started in a NEW SESSION has no controlling terminal: opening /dev/tty fails and
# the installer takes its no-terminal path, which is what these tests assert. So every subprocess this suite starts is
# started that way -- unless the caller chose its own session handling (preexec_fn: the scripted-terminal drivers,
# which setsid() and take a pty ON PURPOSE; start_new_session / process_group given explicitly). One place, so a new
# test cannot forget it; `pytest --noconftest` is the red.
import subprocess as _subprocess

_POPEN_INIT = _subprocess.Popen.__init__


def _popen_init_detached(self, *args, **kwargs):
    if os.name == "posix" and not any(k in kwargs for k in ("start_new_session", "preexec_fn", "process_group")):
        kwargs["start_new_session"] = True
    return _POPEN_INIT(self, *args, **kwargs)


if getattr(_subprocess.Popen.__init__, "__name__", "") != "_popen_init_detached":
    _subprocess.Popen.__init__ = _popen_init_detached


# ---------------------------------------------------------------------------
# THE HEADER — so a published count is explicable
# ---------------------------------------------------------------------------
def pytest_report_header(config):
    """Say which brain the host-bound classes resolved, and whether they will run.

    D8-F7: several classes are gated on a live, POPULATED brain. A skip and a pass
    look identical in pytest's summary line, which is how "101 passed" became a
    sentence about one host rather than about the product. Printing the resolved
    path and the gate's answer makes the number reproducible or explicably not.
    """
    if os.environ.get("VOIDLOOM_TESTS_QUIET", "").strip() in ("1", "true", "yes"):
        return None
    try:
        from voidloom import _paths as _p
        db = str(_p.resolve_db())
    except Exception:
        db = (os.environ.get("WIZARD_LIBRARY_DB")
              or os.environ.get("CLAUDE_MEMORY_DB")
              or str(_ROOT / "claude_memory.db"))
    usable, why = False, "absent"
    try:
        import sqlite3
        if Path(db).exists():
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                n = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            finally:
                c.close()
            usable, why = n > 0, (f"{n} memories" if n else "present but empty")
    except Exception as e:
        why = f"unreadable: {type(e).__name__}"
    return [
        f"voidloom: brain {db}",
        f"voidloom: host-bound classes {'RUN' if usable else 'SKIP'} ({why}) — "
        f"point WIZARD_LIBRARY_DB at a populated brain to run them",
    ]


# ---------------------------------------------------------------------------
# A SELECTION THAT SILENTLY MISSES THE TESTS IT WAS MEANT TO RUN
# ---------------------------------------------------------------------------
# tier1-upgrade loop, iteration 3: a red-at-base check ran `-k cron`, the new test's name did not contain "cron",
# pytest selected only the OLD test, passed, and "green on the old code" was reported for a test that never ran.
# pytest fails a run that selects NOTHING (rc 5); a run that selects SOME tests but not the ones it was for is silent.
#
# Opt-in, for gate scripts: VOIDLOOM_REQUIRE_TESTS="needle1,needle2" -- each needle is a substring of a test's node id
# (a class name, a test name, a file::Class). After selection (-k / -m / node ids), any needle that matches NO selected
# test fails the run before a single test executes, naming the needle. Unset: no effect.
def pytest_collection_finish(session):
    want = [w.strip() for w in os.environ.get("VOIDLOOM_REQUIRE_TESTS", "").split(",") if w.strip()]
    if not want:
        return
    ids = [item.nodeid for item in session.items]
    missing = [w for w in want if not any(w in i for i in ids)]
    if missing:
        raise pytest.UsageError(
            "VOIDLOOM_REQUIRE_TESTS: no selected test matches %s (selected %d: %s) -- the filter missed the tests "
            "this run was meant to execute" % (missing, len(ids), ", ".join(i.split("::")[-1] for i in ids[:8])))
