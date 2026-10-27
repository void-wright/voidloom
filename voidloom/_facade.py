"""_facade.py — the thin facade over the canonical engine ``claude_memory.py``.

This is the load-bearing translation layer. The engine's ``cmd_*`` functions are
the canonical entry points, but they were written for a CLI: they EMIT a single
JSON line via ``_output()`` (``print(json.dumps(...))``) and signal failure via
``_error()`` (which prints ``{"status":"error",...}`` then ``sys.exit(1)``). They
do not *return* a value.

Rather than re-implement any engine logic (which would drift the instant the
engine changes — and it changes a lot: column-dynamic INSERTs, kind taxonomy,
encoding-surprise, actor-stamping), the facade:

  1. Lazily ``import claude_memory`` (heavy modules inside it load only when a
     verb that needs embeddings actually runs — recall/store).
  2. Builds the exact argparse ``Namespace`` the target ``cmd_*`` expects.
  3. Calls the ``cmd_*`` function while capturing stdout.
  4. Parses the engine's last JSON line back into a dict, catching the
     ``SystemExit(1)`` that ``_error`` raises and turning it into a clean result.
  5. Hands the engine's ``{status, action, ...}`` dict back to ``cli.py``, which
     wraps it into the Voidloom ``{status, code, data}`` envelope.

The temp-DB override (for write-path tests) is done by swapping the engine's
module-global ``DB_PATH`` for the duration of one call — ``_get_conn`` reads
``DB_PATH`` live, so this cleanly redirects the engine at a temp copy without
touching the live brain.

Heavy imports are confined to ``_call_engine`` (and below) — importing THIS
module is stdlib-cheap (it does not import claude_memory at module load).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import _paths

# The default brain DB is resolved RELATIVE-SELF in the single package resolver
# (``_paths.default_db``): this package (``voidloom/``) ships inside the brain
# dir, so the DB sits one level up. The env knobs (WIZARD_LIBRARY_DB /
# CLAUDE_MEMORY_DB) always win over the fallback — see ``_paths.resolve_db``.


class FacadeError(Exception):
    """Raised when the engine could not be loaded at all (import failure)."""


def _active_tier_repo():
    """The HuggingFace repo id the engine would load for THIS brain, or None.

    Mirrors `claude_memory._active_embed_model()`'s resolution (env var first,
    then `metadata.active_embedding_table`, then the nomic default) and maps it
    through EMBED_TIERS. Returns None rather than guessing when the engine is not
    importable -- the caller degrades that to a weaker check instead of asserting.
    """
    try:
        engine = _import_engine_for_probe()
        tier = engine._active_embed_model()
        return engine.EMBED_TIERS[tier][2]
    except Exception:
        return None


def _import_engine_for_probe():
    import importlib
    return importlib.import_module("claude_memory")


def _embedding_capability_error(payload: str) -> bool:
    """True iff the engine's own printed error says the EMBEDDING model failed.

    C3-b. The engine reports this failure as data, not as an exception, so the
    honest discriminator is the engine's own words -- not an exception type we
    would be guessing at from outside. Kept deliberately narrow: a brain-level
    engine_error (corrupt DB, bad migration) does not match and still propagates.
    """
    if not payload:
        return False
    low = payload.lower()
    if "embedding model" not in low and "sentence_transformers" not in low:
        return False
    return any(k in low for k in ("failed to load", "couldn't connect", "could not connect",
                                  "no module named", "offline", "not found"))


def _tier1_unavailable(exc) -> bool:
    """True iff `exc` means THE TIER-1 EMBEDDING CAPABILITY IS ABSENT.

    C3-b -- WHY THIS EXISTS, MEASURED 2026-09-15 ON A COLD `--tier0-only` INSTALL.

    The original degrade triggered on ``ImportError`` alone, reasoning that "an
    ImportError raised by a running engine command means exactly one thing: this
    interpreter does not carry the Tier-1 stack." That reasoning is right and its
    DOMAIN IS TOO NARROW. There is a second, and on a `--tier0-only` install the
    NORMAL, way for Tier 1 to be absent: the python packages import fine (they
    live in the base env) and THE MODEL ITSELF is not present -- no cache, no
    network. `sentence_transformers.SentenceTransformer(...)` then raises
    ``OSError``, not ``ImportError``, and the degrade never fired.

    Measured on a scratch HOME built by `install.sh --tier0-only --init-blank`:
    with network, the first `voidloom recall` silently downloaded 524 MB while
    the installer had printed "skipping Tier 1"; with `HF_HUB_OFFLINE=1`, it
    exited 1 with `engine_error` -- on the install mode whose whole promise is
    "Tier 0 (ALWAYS succeeds, stdlib-only)". `claude_memory_lite.py` was present
    and unused, exactly as before C3.

    The original comment's warning still binds: "a fallback that swallows errors
    is worse than no fallback, because it answers confidently over a broken
    brain." So this does NOT widen the catch to bare ``OSError``. It uses the
    exception only as a TRIGGER TO ASK THE WORLD, and degrades only if the
    capability is genuinely gone -- a disk error while reading the brain leaves
    Tier 1 importable and loadable, so the probe says "available" and the error
    propagates untouched.
    """
    if isinstance(exc, ImportError):
        return True
    if not isinstance(exc, OSError):
        return False
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return True          # the stack itself is gone
    import os
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        if os.environ.get(var, "") not in ("", "0"):
            return True      # offline by declaration: the model cannot arrive
    # C3-b-ii -- ASK ABOUT THE MODEL *THIS BRAIN* NEEDS, NOT A HARD-CODED ONE.
    #
    # The first version of this probe asked "is nomic cached?" unconditionally.
    # Falsified by Crucible-3 seat D6 on a supported configuration: the product
    # has THREE tiers (EMBED_TIERS: nomic-768 / jina-1024 / qwen-1024) and
    # install.sh:152 calls qwen "the SHIPPED GPU default". On a qwen-tier brain
    # with only nomic cached, the probe answered "Tier 1 is available", the
    # degrade was refused, and `recall` spent 74.5 s reaching for a model it
    # could not fetch before exiting 1 -- with claude_memory_lite.py present and
    # unused, which is verbatim the sentence the C3-b comment above wrote about
    # the incarnation it replaced. A probe that names one tier in a three-tier
    # product is the same defect one layer down.
    try:
        from huggingface_hub import scan_cache_dir
        names = {r.repo_id.lower() for r in scan_cache_dir().repos}
    except Exception:
        return True          # cannot even ask -> treat the capability as absent
    want = _active_tier_repo()
    if want is None:
        # cannot resolve the tier -> fall back to "any known tier cached",
        # which is weaker but never claims a capability we did not check.
        known = ("nomic", "jina", "qwen")
        return not any(k in n for n in names for k in known)
    return want.lower() not in names


def _import_tier0():
    """The engine whose whole purpose is that it cannot fail: stdlib FTS5 only.

    Returns the module, or None if even this is unreachable -- in which case the
    caller re-raises the real error rather than inventing a softer one.
    """
    import sys

    brain_dir = str(_paths.organ_dir())
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    try:
        import claude_memory_lite  # type: ignore
        return claude_memory_lite
    except Exception:
        return None


_TIER0_ONLY_CACHE: Optional[bool] = None


def _tier0_only_requested() -> bool:
    """Did the OPERATOR ask for Tier 0 only -- at install time or right now?

    CRUCIBLE-3 D7-F2 / D8-F2 / U03-1, measured by three independent seats.
    ``--tier0-only`` was an INSTALL-TIME flag and nothing carried it into the
    runtime, while the runtime chose its tier by TRYING THE IMPORT. So on a host
    whose ambient interpreter happens to have torch + sentence_transformers, the
    first documented command after a ``--tier0-only`` install downloaded
    **1.7 GB** and executed remote Python from the hub:

        h2  /usr/bin/python3, no torch        0.132 s,  0 bytes,  mode fts5-keyword
        h1  anaconda + torch + ST            141.6 s,  1.7 GB,   then 16.0 s warm

    Same package, same flags, same brain, same verb -- the only difference was
    what the ambient interpreter could import. The installer had printed
    "Tier 1 ... not completed" four minutes earlier and its DONE block told the
    user to run exactly that command.

    A flag that scopes the installer and not the product is not a choice, it is
    a suggestion. Resolution order, most immediate first:
      1. ``VOIDLOOM_TIER0_ONLY`` in the environment ("0"/"false"/"" turns it OFF,
         so a user who later installs Tier 1 can opt back in without a re-install)
      2. ``metadata.tier0_only`` in the brain, written by ``install.sh``
      3. otherwise: no, behave exactly as before.

    Cached per process: this is consulted on every engine import and the answer
    cannot change mid-run.
    """
    global _TIER0_ONLY_CACHE
    if _TIER0_ONLY_CACHE is not None:
        return _TIER0_ONLY_CACHE
    env = os.environ.get("VOIDLOOM_TIER0_ONLY")
    if env is not None:
        _TIER0_ONLY_CACHE = env.strip().lower() not in ("", "0", "false", "no", "off")
        return _TIER0_ONLY_CACHE
    _TIER0_ONLY_CACHE = False
    try:
        import sqlite3
        db = _paths.resolve_db()
        if db and os.path.exists(db):
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT value FROM metadata WHERE key='tier0_only'").fetchone()
            finally:
                conn.close()
            if row and str(row[0]).strip().lower() not in ("", "0", "false", "no", "off"):
                _TIER0_ONLY_CACHE = True
    except Exception:
        pass                       # never let this decide by raising
    return _TIER0_ONLY_CACHE


def _import_engine():
    """Lazily import the canonical engine. Adds the brain dir to sys.path so the
    engine + its organ siblings resolve. Heavy modules (torch/jina) are NOT
    pulled by the import itself — only by actually running recall/store.

    The brain dir is resolved through the single package resolver
    (``_paths.brain_dir``) so the facade and the engine agree on *which* brain,
    and the dir added to sys.path is derived from the same resolution as the DB
    path propagated to the engine.
    """
    import sys

    brain_dir = str(_paths.organ_dir())
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    if _tier0_only_requested():
        # The operator asked for Tier 0. Honour it BEFORE touching the Tier-1
        # import, so no model is resolved, fetched or executed. This is the
        # whole of D7-F2: the choice now binds the runtime, not just the installer.
        lite = _import_tier0()
        if lite is not None:
            setattr(lite, "_VOIDLOOM_TIER0", True)
            setattr(lite, "_VOIDLOOM_TIER1_ERROR",
                    "tier 0 requested by the operator (--tier0-only / "
                    "VOIDLOOM_TIER0_ONLY / metadata.tier0_only)")
            return lite
        # Tier 0 unavailable is a real failure and must not be masked by
        # silently upgrading to the tier the operator declined.
    try:
        import claude_memory  # type: ignore
        return claude_memory
    except Exception as e:  # pragma: no cover - exercised via doctor's degrade
        # C3 -- THE TIER-0 PATH, which the product promised and never had.
        #
        # install.sh:26-27 says "Tier 0 (ALWAYS succeeds, stdlib-only) ...
        # Keyword recall works on ANY python3". Measured at the parent on
        # /usr/bin/python3 against a complete brain: `voidloom recall` returned
        # {"status":"error","code":"unhandled","message":"No module named
        # 'sqlite_vec'"} while `voidloom doctor` on the SAME interpreter
        # returned healthy: true. `claude_memory_lite.py` -- the engine whose
        # entire purpose is that it cannot fail -- had NO importer anywhere on
        # the runtime path; its only code-resident consumer was the Node hook
        # this same commit retires, which is why both halves land together.
        #
        # The two engines are call-compatible where it matters: both expose
        # cmd_recall / cmd_store taking a Namespace and printing one JSON line,
        # and both read a module-global DB_PATH that `_engine_db` swaps. So the
        # degrade is a substitution, not a second code path.
        try:
            import claude_memory_lite  # type: ignore
            setattr(claude_memory_lite, "_VOIDLOOM_TIER0", True)
            setattr(claude_memory_lite, "_VOIDLOOM_TIER1_ERROR", f"{type(e).__name__}: {e}")
            return claude_memory_lite
        except Exception:
            pass
        raise FacadeError(f"could not import claude_memory from {brain_dir!r}: {e}")


@contextlib.contextmanager
def _engine_db(engine, db_path: Optional[str]):
    """Temporarily point the engine at ``db_path`` (a temp copy) for one call.

    ``claude_memory._get_conn`` reads the module-global ``DB_PATH`` at call time,
    so swapping it here redirects every read/write of this call. Always restored,
    even on exception. When ``db_path`` is None the live default is used as-is.
    """
    if db_path is None:
        yield
        return
    original = engine.DB_PATH
    try:
        engine.DB_PATH = Path(db_path)
        yield
    finally:
        engine.DB_PATH = original


def _call_engine(cmd_name: str, ns: argparse.Namespace,
                 db_path: Optional[str] = None) -> Dict[str, Any]:
    """Run an engine ``cmd_<name>`` with the given Namespace, capturing its JSON.

    Returns the parsed engine result dict (always containing at least
    ``status``). An engine ``_error`` (which prints + ``sys.exit(1)``) is caught:
    the printed ``{"status":"error",...}`` line is parsed and returned, so the
    caller gets a clean dict, never an exit.

    Single-source-of-truth on which DB: the engine's own module-global
    ``DB_PATH`` is relative-self and ENV-BLIND — it does not consult
    ``WIZARD_LIBRARY_DB`` / ``CLAUDE_MEMORY_DB``. When ``db_path`` is None we
    therefore resolve it HERE (via the single ``_paths.resolve_db`` chain) and
    point the engine at exactly what voidloom resolved, so a set
    ``WIZARD_LIBRARY_DB`` can never make voidloom and the engine open different
    DBs. With NO env set this resolves to the same relative-self path the engine
    would have used on its own — a true no-op (byte-identical to passing None
    through). An explicit caller-supplied ``db_path`` (e.g. a temp-DB test copy)
    is honoured verbatim and is NOT overridden.
    """
    engine = _import_engine()
    if db_path is None:
        # Propagate voidloom's own resolution to the env-blind engine.
        db_path = _paths.resolve_db()
    cmd_fn = getattr(engine, f"cmd_{cmd_name.replace('-', '_')}", None)
    if cmd_fn is None:
        return {"status": "error",
                "message": f"engine has no cmd_{cmd_name}"}

    # H3 stdout/stderr split (deliberate, do-not-regress): we capture ONLY
    # stdout — the engine's single JSON line — into ``buf``. STDERR is left
    # UNTOUCHED: it is never redirected here, and the facade itself emits nothing
    # to it on the success path. The engine's model-load INFO/WARNING (and the
    # SentenceTransformers version warning) are routed off default stderr by the
    # engine-core logger (G2/G6); this facade must not re-introduce that noise by
    # capturing-then-replaying stderr or by adding its own. So a successful
    # recall/store stays silent on stderr, and ``voidloom recall --json | jq``
    # keeps working because the clean JSON is the only thing on stdout.
    buf = io.StringIO()
    raised_exit = None
    with _engine_db(engine, db_path):
        try:
            with contextlib.redirect_stdout(buf):
                cmd_fn(ns)
        except SystemExit as se:
            # _error() does sys.exit(1) AFTER printing the error JSON. A clean
            # success path never exits. Capture the code; the printed line holds
            # the real message.
            raised_exit = se.code
            # C3-b -- THE TIER-1 MODEL IS ABSENT, AND THE ENGINE SWALLOWED IT.
            #
            # `claude_memory._load_model` wraps its load in `except Exception ->
            # _error(...)`, and `_error` prints an engine_error JSON and exits 1.
            # So a missing MODEL never reaches the exception arms below: it
            # arrives here, as a SystemExit, and the degrade never fired. That is
            # why `--tier0-only` shipped a `recall` that exits 1 offline while
            # `claude_memory_lite.py` sat present and unused.
            #
            # Read the ENGINE'S OWN VERDICT rather than guessing at exception
            # types -- the payload is already in `buf`. Degrade only when the
            # engine says the embedding capability failed AND the probe agrees it
            # is genuinely absent; a brain-level engine_error still propagates.
            if raised_exit and not getattr(engine, "_VOIDLOOM_TIER0", False):
                _payload = buf.getvalue()
                if _embedding_capability_error(_payload) and _tier1_unavailable(OSError(_payload)):
                    lite = _import_tier0()
                    lite_fn = getattr(lite, f"cmd_{cmd_name.replace('-', '_')}", None) if lite else None
                    if lite_fn is not None:
                        setattr(lite, "_VOIDLOOM_TIER0", True)
                        setattr(lite, "_VOIDLOOM_TIER1_ERROR",
                                "embedding model unavailable (engine_error at call time)")
                        engine = lite
                        buf = io.StringIO()
                        raised_exit = None
                        with _engine_db(lite, db_path):
                            try:
                                with contextlib.redirect_stdout(buf):
                                    lite_fn(ns)
                            except SystemExit as se2:
                                raised_exit = se2.code
        except (ImportError, OSError) as ie:
            if not _tier1_unavailable(ie):
                raise
            # C3 -- THE TIER-1 STACK IS MISSING, AT CALL TIME.
            #
            # The first version of this degrade sat in `_import_engine`, which
            # was the wrong place: `import claude_memory` SUCCEEDS on a
            # stdlib-only interpreter. The failure is at RUNTIME --
            # claude_memory.py:733 `import sqlite_vec` inside `_get_conn()` --
            # so the degrade has to wrap the CALL, not the import. Traced, not
            # assumed; the assumption cost one wrong edit.
            #
            # ImportError raised by a running engine command means exactly one
            # thing: this interpreter does not carry the Tier-1 stack. Any other
            # exception is a real failure and must NOT be masked -- a fallback
            # that swallows errors is worse than no fallback, because it answers
            # confidently over a broken brain.
            lite = _import_tier0()
            if lite is None:
                raise
            lite_fn = getattr(lite, f"cmd_{cmd_name.replace('-', '_')}", None)
            if lite_fn is None:
                raise
            setattr(lite, "_VOIDLOOM_TIER0", True)
            setattr(lite, "_VOIDLOOM_TIER1_ERROR", f"{type(ie).__name__}: {ie}")
            engine = lite
            buf = io.StringIO()
            raised_exit = None
            with _engine_db(lite, db_path):
                try:
                    with contextlib.redirect_stdout(buf):
                        lite_fn(ns)
                except SystemExit as se:
                    raised_exit = se.code

    out = buf.getvalue().strip()
    parsed = _parse_last_json(out)
    if parsed is not None:
        # C3: a DEGRADED result must say so. A Tier-0 answer is a keyword answer
        # over the same brain -- correct, and narrower than the semantic one the
        # caller asked for. Returning it unstamped would be the class this cycle
        # exists to end: a claim the evidence does not support.
        if isinstance(parsed, dict):
            degraded = bool(getattr(engine, "_VOIDLOOM_TIER0", False))
            # CRUCIBLE-3 D04-F7: the tier/engine stamp was ONE-SIDED -- written only
            # on the degraded path, absent on the Tier-1 path -- so a consumer that
            # branches on which engine answered could not tell "Tier 1" from "the
            # producer did not say". An absent key is not a value. Both paths stamp
            # now, so the key's presence stops carrying information its own absence
            # was being used to convey.
            parsed.setdefault("tier", 0 if degraded else 1)
            parsed.setdefault("engine",
                              "claude_memory_lite" if degraded else "claude_memory")
            if degraded:
                # CRUCIBLE-3 D6-F10: the reason was picked by SUBSTRING-MATCHING the
                # engine's own error text -- `"model unavailable" in str(err)` -- so
                # any error whose wording did not contain that phrase was reported as
                # "the Tier-1 engine is not importable on this interpreter" while
                # `tier1_error`, in the SAME OBJECT, said something else. The reason
                # and the evidence contradicted each other at arm's length.
                #
                # It is now DERIVED from the exception's own type, which is the fact
                # the degrade already branched on: `_tier1_unavailable()` admits
                # ImportError and OSError, and those mean different things. Where the
                # type does not settle it we say so rather than guessing a phrase.
                _err = getattr(engine, "_VOIDLOOM_TIER1_ERROR", None)
                _kind = type(_err).__name__ if isinstance(_err, BaseException) else None
                # DECLINED is not UNAVAILABLE. When the operator asked for Tier 0,
                # nothing failed -- saying "unavailable at call time" would report a
                # fault where there was a choice, which is the same defect class
                # this cycle removed from the degrade reason in the first place.
                if isinstance(_err, str) and "tier 0 requested" in _err.lower():
                    _reason = ("Tier 0 was REQUESTED by the operator; Tier 1 was not "
                               "attempted and no model was resolved or fetched")
                elif _kind == "ImportError" or _kind == "ModuleNotFoundError":
                    _reason = ("the Tier-1 stack is not importable on this "
                               "interpreter")
                elif _kind == "OSError":
                    _reason = ("the Tier-1 embedding model could not be loaded on "
                               "this host")
                else:
                    _reason = ("Tier 1 was unavailable at call time; see "
                               "`tier1_error` for what the engine reported")
                parsed.setdefault("degraded", {
                    "reason": _reason,
                    "tier1_error_type": _kind,
                    "tier1_error": (str(_err) if _err is not None else None),
                    "means": ("keyword (FTS5) recall over the same brain; no "
                              "semantic ranking"),
                })
        return parsed

    # No parseable JSON. If the engine exited non-zero, surface that; else the
    # engine produced unexpected (or empty) output.
    if raised_exit not in (None, 0):
        return {"status": "error",
                "message": f"engine cmd_{cmd_name} exited {raised_exit} "
                           f"with no JSON; raw output: {out[:400]!r}"}
    return {"status": "error",
            "message": f"engine cmd_{cmd_name} produced no JSON; "
                       f"raw output: {out[:400]!r}"}


def _parse_last_json(text: str) -> Optional[Dict[str, Any]]:
    """The engine emits exactly one JSON object as its final stdout line. Parse
    the last JSON-looking line. Returns None if nothing parses."""
    if not text:
        return None
    # Fast path: the whole buffer is one JSON object.
    try:
        v = json.loads(text)
        if isinstance(v, dict):
            return v
    except (ValueError, TypeError):
        pass
    # Otherwise scan from the last non-empty line backwards for a JSON object.
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                v = json.loads(s)
                if isinstance(v, dict):
                    return v
            except (ValueError, TypeError):
                continue
    return None


# ---------------------------------------------------------------------------
# Namespace builders — each mirrors exactly the attributes the target cmd_*
# reads via getattr/attr access. Keeping these explicit (rather than reusing the
# engine's argparse) means the facade exposes ONLY the v0.1 surface and supplies
# safe defaults for every engine flag the cmd_* touches.
# ---------------------------------------------------------------------------


def _recall_namespace(query: str, top_k: int,
                      explain: bool = False,
                      no_cross_tier: bool = False,
                      leads: Optional[int] = None,
                      explore: bool = False,
                      no_rerank: bool = False) -> argparse.Namespace:
    # cmd_recall + _resolve_recall_mode read these. We pin the default hybrid
    # pipeline and leave every ablation/kill-switch OFF (engine defaults).
    # ``explain`` mirrors the engine's own ``--explain`` (dest="explain",
    # store_true, default False): cmd_recall reads it via
    # ``getattr(args, "explain", False)`` to run its render-only L5-N explainer.
    # OFF by default => the recall path is byte-identical to before.
    # ``no_cross_tier`` (H12) threads the engine's own ``--no-cross-tier`` arg:
    # cmd_recall reads ``args.no_cross_tier`` and skips the EDDIE cross-tier
    # enrichment when True. Default False keeps the namespace byte-identical to
    # before; the explicit-OFF request now reaches the engine (belt-and-suspenders
    # alongside C1's default-OFF gate in the engine core).
    return argparse.Namespace(
        query=query,
        top_k=top_k,
        threshold=None,
        category=None,
        project=None,
        kind=None,
        hybrid=True,
        vector_only=False,
        bm25_only=False,
        # handoff round 1 (L2): the caller's per-recall reranker OFF (engine `--no-rerank`). Whether a reranker
        # may run at all is the engine's reranker gate (accelerator AND >= 15.0 GiB); this can only turn it off.
        no_rerank=bool(no_rerank),
        multi_hop=0,
        no_log_usage=False,
        no_lens=False,
        persona=None,
        no_cross_tier=bool(no_cross_tier),
        no_boost=False,
        no_ppr=False,
        no_diversity=False,
        no_temporal_rerank=False,
        explain=bool(explain),
        # The dream fix (A3): the side-channel knobs. None = the registry's quota; 0 = every channel OFF.
        leads=leads,
        explore=bool(explore),
    )


def _store_namespace(content: str, category: str, tags: str,
                     importance: float) -> argparse.Namespace:
    # cmd_store reads these (kind defaults to 'episodic'; auto_edges OFF).
    return argparse.Namespace(
        content=content,
        category=category,
        tags=tags or "",
        importance=importance,
        project=None,
        context=None,
        summary=None,
        kind="episodic",
        auto_edges=False,
        source_user=None,
    )


def _store_full_namespace(
    content: str, category: str, *, tags: str, importance: float,
    kind: str, project: Optional[str], context: Optional[str],
    summary: Optional[str], source_user: Optional[str],
    auto_edges: bool,
) -> argparse.Namespace:
    """The full ``cmd_store`` Namespace — every field a memory dict may carry.

    Used by ``store_full`` (the batch-store path). Mirrors EXACTLY the attributes
    ``cmd_store`` reads, so a batch caller can drive the engine's richer store
    path (kind taxonomy, ``auto_edges`` graph-weave, ``source_user`` actor-stamp,
    ``summary``/``project``/``context``) — none of which the narrow single-verb
    ``_store_namespace`` exposes. Defaults match the engine's own ``cmd_store``
    defaults so an item that supplies only content+category behaves identically
    to a single ``store``.
    """
    return argparse.Namespace(
        content=content,
        category=category,
        tags=tags or "",
        importance=importance,
        project=project,
        context=context,
        summary=summary,
        kind=kind or "episodic",
        auto_edges=bool(auto_edges),
        source_user=source_user,
    )


def _context_namespace(project: Optional[str], limit: int) -> argparse.Namespace:
    return argparse.Namespace(project=project, limit=limit)


# ---------------------------------------------------------------------------
# Public facade calls. Each returns the engine result dict (with `status`).
# cli.py converts these into the Voidloom envelope.
# ---------------------------------------------------------------------------


def recall(query: str, top_k: int = 5, explain: bool = False,
           no_cross_tier: bool = False,
           db_path: Optional[str] = None,
           leads: Optional[int] = None,
           explore: bool = False,
           no_rerank: bool = False) -> Dict[str, Any]:
    """Facade -> engine cmd_recall (lazy heavy import happens here).

    ``no_rerank`` (handoff round 1) skips the rerank stage for this recall (mode ``no-rerank``). It can only turn the
    reranker OFF: the engine's gate keeps it off on CPU-only machines and below 15.0 GiB RAM whatever this says.

    ``explain`` is the pass-through for the engine's L5-N-beta Explainable Recall
    surface. It threads onto the Namespace exactly like ``top_k`` and the other
    recall flags do — ``cmd_recall`` reads ``args.explain`` and, when True, runs
    its render-only "why-surfaced" explainer (a per-result one-liner to STDERR;
    the engine's stdout JSON is byte-identical either way). OFF by default =>
    a no-explain recall is unchanged.

    ``no_cross_tier`` (H12) is the pass-through for the engine's own
    ``--no-cross-tier`` arg: it threads onto the Namespace exactly like ``top_k``
    /``explain`` do, and ``cmd_recall`` reads ``args.no_cross_tier`` to skip the
    EDDIE cross-tier enrichment when True. Default False keeps the call
    byte-identical to before; with C1's engine-core default-OFF gate this is
    belt-and-suspenders, but a ``voidloom recall --no-cross-tier`` request now
    reaches the engine instead of being silently dropped.

    Because the engine prints the explanation to STDERR (which ``_call_engine``
    does not capture) and the voidloom envelope is JSON-first for agents, when
    ``explain`` is set we ALSO fold a clean, ANSI-free structured ``explain``
    list into the engine result dict — built from the SAME per-result sub-scores
    the engine already stamped (via the engine's own ``recall_explain`` organ,
    render-only, never recomputed). This is purely additive: with ``explain``
    off, nothing is added and the dict is exactly what it was before."""
    ns = _recall_namespace(query, top_k, explain=explain,
                           no_cross_tier=no_cross_tier, leads=leads, explore=explore,
                           no_rerank=no_rerank)
    res = _call_engine("recall", ns, db_path=db_path)
    if explain:
        _attach_structured_explain(res)
    return res


def _attach_structured_explain(res: Dict[str, Any]) -> None:
    """Fold a structured per-result explanation into an OK recall result dict.

    Uses the engine's own ``recall_explain`` organ as the single source of truth
    for the explanation shape (the same module ``cmd_recall`` renders to stderr).
    PURE READ of the sub-scores already on each result row — no DB, no model, no
    recompute. Fail-graceful and additive: any problem (organ absent, bad shape)
    leaves ``res`` untouched, so ``--explain`` can never break a recall. Mutates
    ``res`` in place, adding ``res["explain"]`` only when explanations were built.
    """
    try:
        if not isinstance(res, dict) or res.get("status") != "ok":
            return
        results = res.get("results")
        if not isinstance(results, list) or not results:
            return
        # The explainer is a sibling organ of the engine; it resolves on the same
        # brain-dir sys.path the engine import already established. NOT a new
        # engine entry point — a render-only consumer of data already in `res`.
        try:
            from . import _paths  # already imported at module top; re-import safe
            import sys as _sys
            brain_dir = str(_paths.organ_dir())
            if brain_dir not in _sys.path:
                _sys.path.insert(0, brain_dir)
            import recall_explain  # type: ignore
        except Exception:
            return  # organ unavailable -> silently skip (the flag no-ops here)

        explanations: List[Dict[str, Any]] = []
        for row in results:
            if not isinstance(row, dict):
                continue
            try:
                obj = recall_explain.build_explanation(row)
                # 'none' mode => zero ANSI escapes, machine-clean line for --json.
                line = recall_explain.render_explanation(obj, mode="none")
                explanations.append({
                    "id": obj.get("id"),
                    "line": line,
                    "factors": obj.get("factors", []),
                    "present_keys": obj.get("present_keys", []),
                    "absent_keys": obj.get("absent_keys", []),
                })
            except Exception:
                continue  # never let one bad row abort the explain fold
        if explanations:
            res["explain"] = explanations
    except Exception:
        # Defensive: the explain fold is a render-only nicety; never propagate.
        return


def store(content: str, category: str, tags: str = "", importance: float = 0.5,
          db_path: Optional[str] = None, answers: Optional[str] = None) -> Dict[str, Any]:
    """Facade -> engine cmd_store. Category-enum validation is done in cli.py
    BEFORE this is called (so a bad category never loads the embedding model),
    but the engine validates again — defence in depth."""
    ns = _store_namespace(content, category, tags, importance)
    ns.answers = answers            # the dream fix (R1-M6): `--answers <dream_id>` reaches the engine's settle path
    return _call_engine("store", ns, db_path=db_path)


def store_full(content: str, category: str, *, tags: str = "",
               importance: float = 0.5, kind: str = "episodic",
               project: Optional[str] = None, context: Optional[str] = None,
               summary: Optional[str] = None, source_user: Optional[str] = None,
               auto_edges: bool = False,
               db_path: Optional[str] = None, answers: Optional[str] = None) -> Dict[str, Any]:
    """Facade -> engine cmd_store with the FULL field set (the batch-store path).

    Same engine entry point as ``store``, but exposes every memory-dict field a
    batch item may carry (kind / project / context / summary / source_user /
    auto_edges). The single ``store`` verb keeps its narrow signature unchanged;
    this is the additive surface ``_batch.store_batch`` drives. Category-enum
    validation is the caller's responsibility (the engine validates again —
    defence in depth). All heavy work happens through the same ``_call_engine``,
    so a batch of N items loads the embedding model ONCE per process."""
    ns = _store_full_namespace(
        content, category, tags=tags, importance=importance, kind=kind,
        project=project, context=context, summary=summary,
        source_user=source_user, auto_edges=auto_edges)
    ns.answers = answers            # R1-M6: batch-store items may settle dreams too
    return _call_engine("store", ns, db_path=db_path)


def context(project: Optional[str] = None, limit: int = 15,
            db_path: Optional[str] = None) -> Dict[str, Any]:
    """Facade -> engine cmd_context (stdlib-cheap; no embedding model needed)."""
    ns = _context_namespace(project, limit)
    return _call_engine("context", ns, db_path=db_path)


def engine_categories() -> List[str]:
    """The engine's canonical category enum, read live so the facade can never
    drift from it. Falls back to the documented v0.1 list if the engine cannot
    be imported (e.g. doctor on a half-installed host)."""
    try:
        engine = _import_engine()
        cats = getattr(engine, "CATEGORIES", None)
        if isinstance(cats, (list, tuple)) and cats:
            return list(cats)
    except Exception:
        pass
    return [
        "user_preference", "technical_decision", "project_knowledge",
        "error_pattern", "skill_lesson", "workflow_pattern",
        "people_context", "tool_optimization", "domain_knowledge",
        "campaign_state", "correction", "insight",
    ]


def engine_importable() -> Dict[str, Any]:
    """Best-effort: can the engine be imported, and where from? For doctor.
    Never raises."""
    try:
        engine = _import_engine()
        return {"importable": True,
                "module_file": getattr(engine, "__file__", None),
                "categories": len(getattr(engine, "CATEGORIES", []))}
    except Exception as e:
        return {"importable": False, "error": str(e)}
