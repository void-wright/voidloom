"""C3-b: the Tier-0 degrade must fire when the MODEL is absent, not only when the
STACK is unimportable.

Measured 2026-09-15 on a scratch HOME built by `install.sh --tier0-only --init-blank`:
`voidloom recall` exited 1 with `engine_error` offline, and silently downloaded
524 MB with network — on the install mode whose printed promise is
"Tier 0 (ALWAYS succeeds, stdlib-only)". `claude_memory_lite.py` was present and
unused. The cause: `claude_memory._load_model` wraps its load in
`except Exception -> _error(...)`, and `_error` PRINTS the error JSON and exits 1,
so the failure arrives as SystemExit and never reached the ImportError arm.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "organs"))   # the engine lives here
from voidloom import _facade  # noqa: E402



import pytest


@pytest.fixture(autouse=True)
def _restore_engine_tier_cache():
    """Save and restore `claude_memory._active_model_cache`.

    Crucible-3 seat D8: a test in THIS file cleared that module global inside a
    monkeypatch loop and never restored it, leaving the process cached on 'qwen'
    and deterministically failing two tests in test_voidloom.py with
    `db_uninitialized` against a brain holding 72 tables and 19,535 memories.
    That reopened the collection-order class C0 (4a528d4) closed nine hours
    earlier -- C0 scoped only the sys.modules sites, not module globals.
    An autouse fixture closes it for every test in this file, permanently.
    """
    import importlib
    cm = importlib.import_module("claude_memory")
    saved = dict(cm._active_model_cache)
    try:
        yield
    finally:
        cm._active_model_cache.clear()
        cm._active_model_cache.update(saved)


def test_embedding_capability_error_recognises_the_engines_own_words():
    load_fail = '{"status":"error","code":"engine_error",' \
                '"message":"Failed to load embedding model: We couldn\'t connect to ' \
                '\'https://huggingface.co\' to load the files"}'
    assert _facade._embedding_capability_error(load_fail) is True


def test_embedding_capability_error_does_NOT_swallow_a_brain_error():
    """The original comment's warning still binds: a fallback that swallows errors
    is worse than no fallback. A corrupt-brain engine_error must propagate."""
    brain_fail = '{"status":"error","code":"engine_error",' \
                 '"message":"database disk image is malformed"}'
    assert _facade._embedding_capability_error(brain_fail) is False
    migration_fail = '{"status":"error","code":"engine_error",' \
                     '"message":"no such table: memory_embeddings_v2"}'
    assert _facade._embedding_capability_error(migration_fail) is False


def test_embedding_capability_error_is_empty_safe():
    assert _facade._embedding_capability_error("") is False
    assert _facade._embedding_capability_error(None) is False


def test_tier1_unavailable_says_yes_when_declared_offline(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert _facade._tier1_unavailable(OSError("couldn't connect")) is True


def test_tier1_unavailable_still_says_yes_on_importerror():
    assert _facade._tier1_unavailable(ImportError("No module named 'sqlite_vec'")) is True


def test_tier1_unavailable_refuses_non_oserror():
    """A ValueError is not a capability signal and must not trigger a degrade."""
    assert _facade._tier1_unavailable(ValueError("bad argument")) is False


# ---------------------------------------------------------------------------
# C3-b-ii: the probe must ask about THIS BRAIN'S tier, not a hard-coded one.
# Falsified by Crucible-3 seat D6: the first probe asked "is nomic cached?"
# unconditionally, so on a qwen-tier brain (install.sh:152 — "the SHIPPED GPU
# default") with only nomic cached it answered "Tier 1 is available", refused the
# degrade, and let `recall` spend 74.5 s reaching for a model it could not fetch.
# ---------------------------------------------------------------------------

def test_active_tier_repo_follows_the_env_override(monkeypatch):
    import importlib
    cm = importlib.import_module("claude_memory")
    for tier, expected in (("nomic", "nomic-ai/nomic-embed-text-v1.5"),
                           ("jina",  "jinaai/jina-embeddings-v5-text-small"),
                           ("qwen",  "Qwen/Qwen3-Embedding-0.6B")):
        monkeypatch.setenv("WIZARD_EMBED_MODEL", tier)
        cm._active_model_cache.clear()
        assert _facade._active_tier_repo() == expected, f"tier {tier} resolved wrong"


def test_active_tier_repo_covers_every_declared_tier():
    """A probe that names ONE tier in a three-tier product is the C3-b defect
    one layer down. If EMBED_TIERS grows, this fails until the probe follows."""
    import importlib
    cm = importlib.import_module("claude_memory")
    for tier in cm.EMBED_TIERS:
        assert len(cm.EMBED_TIERS[tier]) >= 3, f"{tier} has no repo id"


def test_active_tier_repo_returns_None_rather_than_guessing(monkeypatch):
    """When the engine cannot be reached the probe must decline, not assert."""
    monkeypatch.setattr(_facade, "_import_engine_for_probe",
                        lambda: (_ for _ in ()).throw(ImportError("no engine")))
    assert _facade._active_tier_repo() is None


# ---------------------------------------------------------------------------
# THE TEST THAT BINDS THE DEGRADE ITSELF.
#
# Crucible-3 seat D8 replaced `_import_tier0()`'s body with `return None` --
# deleting the entire Tier-0 fallback -- and all nine tests in this file PASSED,
# because every one of them exercised a pure helper fed a hand-typed literal and
# none called the degrade path. A binder that survives the deletion of the thing
# it binds is not a binder; it is false confidence with a green tick. This test
# fails under that mutation.
# ---------------------------------------------------------------------------

def test_degrade_actually_routes_to_tier0_when_the_model_is_absent(monkeypatch, tmp_path):
    """The binder for the degrade PATH, not for its helpers.

    Crucible-3 seat D8 replaced `_import_tier0()`'s body with `return None` --
    deleting the entire Tier-0 fallback -- and all nine tests in this file passed,
    because every one exercised a pure helper fed a hand-typed literal. A binder
    that survives the deletion of the thing it binds is false confidence with a
    green tick.

    So this test deliberately does NOT patch `_import_tier0`: patching the very
    function whose deletion you are trying to detect makes the mutation invisible.
    (My first attempt did exactly that and passed under the mutation -- recorded
    here because it is the same defect one layer up.) Only the ENGINE is faked,
    to fail the way a missing model fails.
    """
    import json as _json, shutil, argparse

    blank = Path(__file__).resolve().parents[2] / "seed" / "claude_memory_blank.db"
    assert blank.exists(), (
        f"the shipped blank brain is missing at {blank} -- a SKIP here would make "
        "this binder bind nothing, which is the defect it exists to catch")
    brain = tmp_path / "brain.db"
    shutil.copy(blank, brain)

    class _FakeEngine:
        DB_PATH = brain
        @staticmethod
        def cmd_recall(ns):
            print(_json.dumps({"status": "error", "code": "engine_error",
                               "message": "Failed to load embedding model: "
                                          "We couldn't connect to 'https://huggingface.co'"}))
            raise SystemExit(1)

    monkeypatch.setattr(_facade, "_import_engine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr(_facade, "_tier1_unavailable", lambda exc: True)

    ns = argparse.Namespace(query="hello", top_k=3, json=True, explain=False,
                            category=None, project=None, db=None)
    out = _facade._call_engine("recall", ns, str(brain))

    # The stamp goes on the ENGINE'S TOP-LEVEL dict, beside `status` -- not inside
    # `data`. Asserting the wrong location is how a real defect gets a green tick.
    assert out.get("tier") == 0, f"the Tier-0 fallback did not run: {out}"
    assert out.get("engine") == "claude_memory_lite", f"wrong engine stamp: {out}"
    assert "degraded" in out, "degraded block missing -- an unstamped Tier-0 answer is the C3 defect"
