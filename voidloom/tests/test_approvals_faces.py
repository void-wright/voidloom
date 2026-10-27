# -*- coding: utf-8 -*-
"""The ANSI face of the approvals TUI, driven through its `_input` seam.

VAEL, Council #12 (2026-09-15): nothing in the test tree referenced run_tui,
_approvals_faces or the `_input` seam — the face's four behavioural claims
(loops home after a decision; quits only on q; boots empty and still waits for
q; the verdict is derived from the WORK, never the envelope's label) rested on
one interactive transcript.  These tests are those four claims, executed.

The face is driven by scripted keys; `is_tty()` is not consulted because
`_run_ansi` is the unit under test (run_tui's TTY refusal has its own test).
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from typing import Any, Dict, List

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(os.path.dirname(HERE))
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from voidloom import _approvals_faces as faces  # noqa: E402


def _rec(ref: str, n_units: int = 2, select_by: str = "title") -> Dict[str, Any]:
    return {"id": ref, "ref": ref, "title": f"item {ref}", "source": "self-eval",
            "summary": "s", "preview": "p", "trust_tier": "T2",
            "units": [{"title": f"prop {i}", "id": f"{ref}-{i}", "kind": "k",
                       "select_by": select_by} for i in range(1, n_units + 1)]}


class Script:
    """Feeds keys in order; a run that asks for more keys than scripted FAILS,
    which is how 'the loop returns home' is asserted (it must ask again)."""
    def __init__(self, keys: List[str]):
        self.keys = list(keys)
        self.prompts: List[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.keys:
            raise AssertionError(f"face asked for input beyond the script; prompts so far: {self.prompts}")
        return self.keys.pop(0)


def _drive(records_fn, enact, keys, snooze=None):
    s = Script(keys)
    buf = io.StringIO()
    with redirect_stdout(buf):
        res = faces._run_ansi(structured=records_fn, enact=enact,
                              snooze=snooze or (lambda h: {"status": "ok"}),
                              force_mode="none", _input=s)
    return res, s, buf.getvalue()


def test_empty_queue_boots_and_only_q_leaves():
    res, s, out = _drive(lambda: [], lambda **kw: {"status": "ok"}, ["", "x", "q"])
    assert res["status"] == "ok" and res["code"] == "tui_empty"
    assert len(s.prompts) == 3, "an empty shell must keep asking until q"
    assert "approvals" in out


def test_decision_loops_home_and_remainder_is_reread():
    state = {"items": [_rec("self-eval:1"), _rec("self-eval:2")]}

    def enact(ref, *, action, **kw):
        state["items"] = [i for i in state["items"] if i["ref"] != ref]
        return {"status": "ok", "enacted": [1], "failed": [], "blocked": []}

    # pick item 1 -> approve -> which? all -> comment blank -> confirm y  ; then home again -> q
    res, s, out = _drive(lambda: list(state["items"]), enact,
                         ["1", "a", "all", "", "y", "q"])
    assert res["status"] == "ok" and res["code"] == "tui_quit"
    # the home screen rendered twice (the loop came back) and the second render shows 1 item
    assert out.count("approvals (TUI)") >= 2
    assert "1 remain" in out or "remaining" in out or "self-eval:2" in out


def test_failed_enact_is_not_reported_ok():
    items = [_rec("self-eval:3")]

    def enact(ref, *, action, **kw):
        return {"status": "ok", "enacted": [], "failed": [3], "blocked": []}

    res, s, out = _drive(lambda: list(items), enact, ["1", "a", "all", "", "y", "q"])
    assert res["status"] == "error", "an enact whose WORK failed must not close as ok"
    assert any("fail" in line.lower() for line in out.splitlines()), out


def test_selection_by_id_for_belief_units():
    items = [_rec("belief:b1", select_by="id")]
    seen: Dict[str, Any] = {}

    def enact(ref, *, action, **kw):
        seen.update(kw)
        return {"status": "ok", "enacted": [1], "failed": [], "blocked": []}

    _drive(lambda: list(items), enact, ["1", "a", "1", "", "y", "q"])
    assert seen.get("ids") == ["belief:b1-1"] and not seen.get("titles")


def test_run_tui_refuses_without_a_tty(monkeypatch):
    monkeypatch.setattr(faces, "is_tty", lambda: False)
    res = faces.run_tui(list_pending=lambda: [], structured=lambda: [],
                        enact=lambda **kw: {}, snooze=lambda h: {})
    assert res["status"] == "refused" and res["code"] == "no_tty"


def test_default_face_is_ansi_even_if_textual_importable(monkeypatch):
    monkeypatch.setattr(faces, "is_tty", lambda: True)
    monkeypatch.setattr(faces, "textual_available", lambda: True)
    called = {"textual": 0}

    def fake_textual(**kw):
        called["textual"] += 1
        return {"status": "ok"}
    monkeypatch.setattr(faces, "_run_textual", fake_textual)
    s = Script(["q"])
    with redirect_stdout(io.StringIO()):
        res = faces.run_tui(list_pending=lambda: [], structured=lambda: [],
                            enact=lambda **kw: {}, snooze=lambda h: {}, _input=s)
    assert called["textual"] == 0 and res["code"] == "tui_empty"


def test_cli_tui_path_reaches_run_tui_with_face(monkeypatch):
    """The CLI's --tui dispatch must reach faces.run_tui (caught 2026-09-15: a
    NameError in the dispatch made `voidloom approvals --tui` an unhandled error
    while every face test passed — the seam between cli.py and the face had no
    test).  Runs the real verb function with a stubbed backend and a stubbed
    run_tui; asserts the face flag is threaded through.

    THE PREMISE, repaired (reference-host round, R9). This passed ``db=None``, so the
    envelope's admission (``_count_trust``) resolved ``<tree>/claude_memory.db`` -- present
    on an installed brain, ABSENT in a checkout -- and refused with
    ``approvals_count_unknown`` AFTER the stubbed face had been reached (measured: the
    stub ran, the refusal came from the count, not from a TTY guard). The test now
    hands the verb a brain it owns, built from the package (``packaged_brain``), so the
    same assertion holds in a checkout and on an install. The assertion is unchanged."""
    import tempfile
    import types
    from pathlib import Path
    from voidloom import cli as vcli
    from voidloom.tests._owned_brain import packaged_brain
    scratch = Path(os.environ.get("VOIDLOOM_SCRATCH") or (Path.home() / ".crucible_scratch"))
    scratch.mkdir(parents=True, exist_ok=True)
    box = Path(tempfile.mkdtemp(prefix="tui_", dir=str(scratch)))
    import shutil
    seen = {}

    def fake_run_tui(**kw):
        seen.update(kw)
        return {"status": "ok", "code": "tui_empty"}
    monkeypatch.setattr(faces, "run_tui", fake_run_tui)
    backend = types.SimpleNamespace(
        list_pending=lambda db=None: [], structured_record=lambda db=None: [],
        set_snooze=lambda h, db=None: {"status": "ok"},
        approve=lambda *a, **k: {}, reject=lambda *a, **k: {})
    try:
        # the brain is built INSIDE the try (iteration 2, item 15): a migrator failure must not leak the box
        db = str(packaged_brain(box / "claude_memory.db"))
        res = vcli._verb_tui(backend, db=db, face="ansi")
    finally:
        shutil.rmtree(str(box), ignore_errors=True)
    assert res.get("status") == "ok"
    assert seen.get("_force_backend") == "ansi"
    assert callable(seen.get("structured")) and callable(seen.get("enact"))
