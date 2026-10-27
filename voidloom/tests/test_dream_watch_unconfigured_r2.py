# -*- coding: utf-8 -*-
"""Round 2, R2c m4 -- for the integrator, with dream_ledger_unconfigured.patch: `voidloom doctor`'s dream line
(dream_ledger.dream_watch, the function doctor and the sentinel share) reads UNCONFIGURED, not STARVED, on a brain
whose owner answered the thinker question "e) not now". RED without the patch; GREEN with it."""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "organs") not in sys.path:
    sys.path.insert(0, str(ROOT / "organs"))

import dream_ledger  # noqa: E402
import schema_migrator  # noqa: E402
import thinker  # noqa: E402
from voidloom.tests import _thinker_fakes as F  # noqa: E402


@pytest.fixture()
def brain(monkeypatch, tmp_path):
    w = F.world(monkeypatch, tmp_path / "w")
    path = w.brain / "claude_memory.db"
    assert schema_migrator.apply(str(path), ROOT / "migrations")["complete"]
    return path


def _watch(path):
    c = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    try:
        return dream_ledger.dream_watch(c, 2.0)
    finally:
        c.close()


def test_not_now_reads_unconfigured_where_doctor_looks(brain):
    assert thinker.run(["set", "off", "--answered-by", "owner"])["_exit_code"] == 0
    w = _watch(brain)
    assert w["verdict"] == "UNCONFIGURED" and "not now" in w["detail"], w


def test_guard_no_answer_stays_starved(brain):
    assert _watch(brain)["verdict"] == "STARVED"
