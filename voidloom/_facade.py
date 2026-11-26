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

Which database a call answers for, and where its JSON line goes, are pinned to the
call (``organs/engine_pin.py``): nothing module-global is swapped, and one
process-wide lock runs engine calls one at a time, so concurrent callers over two
databases each get their own answer.

Heavy imports are confined to ``_call_engine`` (and below) — importing THIS
module is stdlib-cheap (it does not import claude_memory at module load).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sqlite3
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

    Asks `claude_memory._active_embed_model()`, which reads the install lock first
    and, on a brain with no lock, the env var, then `metadata.active_embedding_table`,
    then the nomic default, and maps it through EMBED_TIERS. Returns None rather than
    guessing when the engine is not importable or the lock refuses -- the caller
    degrades that to a weaker check instead of asserting.
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
    # The pin is not on this disk and this process may not fetch it: the capability is absent. The integrity refusals
    # (WEIGHTS_MISMATCH, LOCK_MALFORMED, ...) are not, and a brain the lock does not vouch for gets no keyword fallback.
    if '"error": "snapshot_absent"' in low or "snapshot_absent:" in low:
        return True
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


def _brain_is_locked(db_path: Optional[str]) -> bool:
    """A lock is a promise of semantic recall: on such a brain a store with no vector is a memory recall cannot reach, so
    an absent model is a refusal, not a keyword store. A brain with no lock made no such promise. Only the lock reader's
    own word that none was ever written (LOCK_MISSING without `was_locked`) reads as no promise: a lock that was written
    and is gone, one that is there and cannot be read, and a check that could not run all still hold the brain to it."""
    try:
        import embedder_lock  # type: ignore
        embedder_lock.resolve(db_path)
        return True
    except Exception as e:  # noqa: BLE001
        never = getattr(e, "code", None) == "LOCK_MISSING" and not (getattr(e, "detail", None) or {}).get("was_locked")
        return not never


# The engine's codes for an encoder that is absent rather than wrong. A recall answers by keyword, flagged; a store on a
# locked brain refuses, typed, and writes nothing.
_ENCODER_ABSENT = ("SNAPSHOT_ABSENT", "EMBEDDER_UNAVAILABLE")


def _keyword_store_refused(cmd_name: str, db_path: Optional[str]) -> bool:
    """Would a keyword-only store break this brain's promise? Read from the brain's declaration (its lock), never from
    the symptom, so a brain installed keyword-only keeps storing and a locked one refuses whatever failed to load."""
    return cmd_name == "store" and _brain_is_locked(db_path)


def _encoder_refusal(cmd_name: str, code: str, message: str) -> str:
    """The typed refusal a store on a locked brain answers when its encoder cannot run: one JSON line, as the engine's own
    door prints it. Nothing was written."""
    text = message if message.startswith(code) else f"{code}: {message}"
    return json.dumps({"status": "error", "error": code, "action": cmd_name,
                       "message": (f"{text}. This brain's install lock promises semantic recall, so a memory with no "
                                   "vector is refused rather than stored by keyword; nothing was written.")}) + "\n"


def _foreign_env_refusal() -> Optional[Dict[str, Any]]:
    """A store under an env directory that belongs to another install would land with no vector and say ok."""
    import sys

    brain_dir = str(_paths.organ_dir())
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    try:
        import voidloom_env  # type: ignore
        voidloom_env.env_path(voidloom_env.ENV_FILE_NAME)
    except ImportError:
        return None
    except ValueError as e:
        return {"status": "error", "error": getattr(e, "code", "ENV_DIR_FOREIGN"), "action": "store", "message": str(e)}
    return None


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
    engine + its organ siblings resolve. Heavy modules (torch/the embedder) are NOT
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
        # and both read the pinned database (engine_pin). So the
        # degrade is a substitution, not a second code path.
        try:
            import claude_memory_lite  # type: ignore
            setattr(claude_memory_lite, "_VOIDLOOM_TIER0", True)
            setattr(claude_memory_lite, "_VOIDLOOM_TIER1_ERROR", f"{type(e).__name__}: {e}")
            return claude_memory_lite
        except Exception:
            pass
        raise FacadeError(f"could not import claude_memory from {brain_dir!r}: {e}")


def _engine_pin():
    """organs/engine_pin.py: the process-wide lock around an engine call, and the per-call database and output."""
    import sys

    organs = str(_paths.organ_dir())
    if organs not in sys.path:
        sys.path.insert(0, organs)
    import engine_pin  # type: ignore
    return engine_pin


def _asks_for_marks(ns: argparse.Namespace) -> bool:
    return bool(getattr(ns, "outside", False)) or any(
        getattr(ns, k, None) is not None for k in ("research", "author", "session_id", "parent_ref", "source_ids"))


def _marks_unsupported(engine) -> Dict[str, Any]:
    return {"status": "error", "error": "OUTSIDE_REQUIRES_MARK", "code": "OUTSIDE_REQUIRES_MARK", "action": "store",
            "message": ("OUTSIDE_REQUIRES_MARK: this store asked for a mark and the engine that would answer "
                        f"({getattr(engine, '__name__', 'engine')}) cannot write one. Nothing was stored.")}


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

    One call at a time in this process (``engine_pin.LOCK``): the model code under an engine is not safe to run twice at
    once, so concurrent callers queue here. The database and the captured output are pinned to the call.
    """
    with _engine_pin().LOCK:
        return _call_engine_locked(cmd_name, ns, db_path)


def _call_engine_locked(cmd_name: str, ns: argparse.Namespace, db_path: Optional[str]) -> Dict[str, Any]:
    pinned = _engine_pin().pinned
    engine = _import_engine()
    if db_path is None:
        # Propagate voidloom's own resolution to the env-blind engine.
        db_path = _paths.resolve_db()
    if cmd_name == "store":
        foreign = _foreign_env_refusal()
        if foreign is not None:
            return foreign
    cmd_fn = getattr(engine, f"cmd_{cmd_name.replace('-', '_')}", None)
    if cmd_fn is None:
        return {"status": "error",
                "message": f"engine has no cmd_{cmd_name}"}
    if cmd_name == "store" and _asks_for_marks(ns) and not getattr(engine, "SUPPORTS_MARKS", False):
        return _marks_unsupported(engine)

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
    try:
        with pinned(db_path, buf):
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
            if _embedding_capability_error(_payload) and _tier1_unavailable(OSError(_payload)) \
                    and _keyword_store_refused(cmd_name, db_path):
                _said = _parse_last_json(_payload.strip())
                buf = io.StringIO(_encoder_refusal(cmd_name, "EMBEDDER_UNAVAILABLE", str(
                    (_said or {}).get("message") or _payload.strip()[:400])))
            elif _embedding_capability_error(_payload) and _tier1_unavailable(OSError(_payload)):
                lite = _import_tier0()
                lite_fn = getattr(lite, f"cmd_{cmd_name.replace('-', '_')}", None) if lite else None
                if lite_fn is not None:
                    setattr(lite, "_VOIDLOOM_TIER0", True)
                    setattr(lite, "_VOIDLOOM_TIER1_ERROR",
                            "embedding model unavailable (engine_error at call time)")
                    engine = lite
                    buf = io.StringIO()
                    raised_exit = None
                    try:
                        with pinned(db_path, buf):
                            lite_fn(ns)
                    except SystemExit as se2:
                        raised_exit = se2.code
    except (ImportError, OSError) as ie:
        if not _tier1_unavailable(ie):
            raise
        if _keyword_store_refused(cmd_name, db_path):
            return dict(_parse_last_json(_encoder_refusal(cmd_name, "EMBEDDER_UNAVAILABLE",
                                                          f"{type(ie).__name__}: {ie}").strip()) or {},
                        tier=1, engine="claude_memory")
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
        try:
            with pinned(db_path, buf):
                lite_fn(ns)
        except SystemExit as se:
            raised_exit = se.code
    except Exception as te:
        # The engine's TYPED refusals (`.code`) reach this in-process call as exceptions, never as the SystemExit
        # `main()` makes of them. SNAPSHOT_ABSENT means the capability is absent, so the front door degrades exactly
        # as it does for an ImportError, stamped. Every other typed code is an integrity refusal: returned as the
        # engine's own envelope, never masked by a keyword answer, and never `unhandled`.
        _code = getattr(te, "code", None)
        if not _code:
            raise                                       # not ours to rephrase
        if _code not in _ENCODER_ABSENT or _keyword_store_refused(cmd_name, db_path):
            payload = {"status": "error", "error": _code, "action": cmd_name, "message": str(te)}
            cov = getattr(te, "coverage", None)
            if cov is not None and hasattr(cov, "as_dict"):
                payload["coverage"] = cov.as_dict()
            for k, v in (getattr(te, "detail", None) or {}).items():
                payload.setdefault(k, v)
            if "coverage" not in payload and hasattr(engine, "_coverage_or_none"):
                with pinned(db_path):                   # what `main()` adds on the engine's own door
                    payload["coverage"] = engine._coverage_or_none()
            buf = io.StringIO(json.dumps(payload) + "\n")
            raised_exit = 1
        else:
            lite = _import_tier0()
            lite_fn = getattr(lite, f"cmd_{cmd_name.replace('-', '_')}", None) if lite else None
            if lite_fn is None:
                raise
            setattr(lite, "_VOIDLOOM_TIER0", True)
            setattr(lite, "_VOIDLOOM_TIER1_ERROR", f"{type(te).__name__}: {te}")
            engine = lite
            buf = io.StringIO()
            raised_exit = None
            try:
                with pinned(db_path, buf):
                    lite_fn(ns)
            except SystemExit as se3:
                raised_exit = se3.code

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
                _requested = isinstance(_err, str) and "tier 0 requested" in _err.lower()
                if _requested:
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
                    # the brain was installed keyword-only: what it declared, not a fault in it
                    **({"requested": True} if _requested else {}),
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
                      no_rerank: bool = False,
                      no_log: bool = False) -> argparse.Namespace:
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
        no_log=bool(no_log),
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
           no_rerank: bool = False,
           no_log: bool = False) -> Dict[str, Any]:
    """Facade -> engine cmd_recall (lazy heavy import happens here).

    ``no_log`` leaves no trace of this recall: no access row, no access count, no usage or met row. (No recall stores the
    words that were asked, with or without it.)

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
                           no_rerank=no_rerank, no_log=no_log)
    try:
        res = _call_engine("recall", ns, db_path=db_path)
    except sqlite3.Error as e:          # a brain this engine cannot read as it expects: an answer, never a raise
        return {"status": "error", "error": "BRAIN_UNREADABLE", "code": "BRAIN_UNREADABLE", "action": "recall",
                "degraded": True, "message": f"{type(e).__name__}: {e}. The brain could not be read as this release "
                "expects; run doctor, or open it once writable so its schema can be brought up to date."}
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


def _set_marks(ns: argparse.Namespace, outside: bool, research: Optional[str], author: Optional[str],
               session_id: Optional[str], parent_ref: Optional[str], source_ids: Any) -> None:
    """What the caller says about where the text came from, onto the namespace both engines read. Never inferred."""
    ns.outside, ns.research, ns.author = outside, research, author
    ns.session_id, ns.parent_ref, ns.source_ids = session_id, parent_ref, source_ids


def store(content: str, category: str, tags: str = "", importance: float = 0.5,
          db_path: Optional[str] = None, answers: Optional[str] = None, *, outside: bool = False,
          research: Optional[str] = None, author: Optional[str] = None, session_id: Optional[str] = None,
          parent_ref: Optional[str] = None, source_ids: Any = None) -> Dict[str, Any]:
    """Facade -> engine cmd_store. Category-enum validation is done in cli.py
    BEFORE this is called (so a bad category never loads the embedding model),
    but the engine validates again — defence in depth.

    ``outside`` marks text that is not the owner's own; ``research`` names the registered run that produced it (and
    implies outside); ``author`` is user, model or tool; ``source_ids`` are the memories it was written from, and it
    takes their marks."""
    ns = _store_namespace(content, category, tags, importance)
    ns.answers = answers            # the dream fix (R1-M6): `--answers <dream_id>` reaches the engine's settle path
    _set_marks(ns, outside, research, author, session_id, parent_ref, source_ids)
    return _call_engine("store", ns, db_path=db_path)


def store_full(content: str, category: str, *, tags: str = "",
               importance: float = 0.5, kind: str = "episodic",
               project: Optional[str] = None, context: Optional[str] = None,
               summary: Optional[str] = None, source_user: Optional[str] = None,
               auto_edges: bool = False,
               db_path: Optional[str] = None, answers: Optional[str] = None, outside: bool = False,
               research: Optional[str] = None, author: Optional[str] = None, session_id: Optional[str] = None,
               parent_ref: Optional[str] = None, source_ids: Any = None) -> Dict[str, Any]:
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
    _set_marks(ns, outside, research, author, session_id, parent_ref, source_ids)
    return _call_engine("store", ns, db_path=db_path)


def context(project: Optional[str] = None, limit: int = 15,
            db_path: Optional[str] = None) -> Dict[str, Any]:
    """Facade -> engine cmd_context (stdlib-cheap; no embedding model needed)."""
    ns = _context_namespace(project, limit)
    return _call_engine("context", ns, db_path=db_path)


def _marks_call(module: str, fn: str, db_path: Optional[str], *args: Any, **kw: Any) -> Dict[str, Any]:
    """Run one of the mark verbs over the resolved brain and return its result, a refusal included, as a dict."""
    import importlib
    with _engine_pin().LOCK:
        mod = importlib.import_module(module)
        db = db_path if db_path is not None else _paths.resolve_db()
        try:
            return getattr(mod, fn)(db, *args, **kw)
        except Exception as e:  # noqa: BLE001 -- only a typed refusal is an answer
            code = getattr(e, "code", None)
            if not isinstance(code, str):
                raise
            return {"status": "error", "error": code, "code": code, "message": str(e), **(getattr(e, "detail", None) or {})}


def research_register(config: Dict[str, Any], run_id: Optional[str] = None,
                      db_path: Optional[str] = None) -> Dict[str, Any]:
    """Register a research configuration, and a run of it. Insert-only, idempotent, starts nothing."""
    return _marks_call("marks", "register", db_path, config, run_id)


def forget(memory_id: Optional[int] = None, run_id: Optional[str] = None, *, dry_run: bool = False,
           include_mixed: bool = False, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Archive one memory or one research run, with a receipt. Reversible: `unarchive`."""
    return _marks_call("mark_admin", "forget", db_path, memory_id=memory_id, run_id=run_id, dry_run=dry_run,
                       include_mixed=include_mixed)


def unarchive(memory_id: Optional[int] = None, run_id: Optional[str] = None, *,
              db_path: Optional[str] = None) -> Dict[str, Any]:
    """Undo `forget` for one memory or one run, from its receipt."""
    return _marks_call("mark_admin", "unarchive", db_path, memory_id=memory_id, run_id=run_id)


def marks_backfill(*, dry_run: bool = False, yes_many: bool = False, undo: bool = False,
                   db_path: Optional[str] = None) -> Dict[str, Any]:
    """Mark what was already written from marked rows, or undo the last time that was done."""
    if undo:
        return _marks_call("mark_admin", "backfill_undo", db_path)
    return _marks_call("mark_admin", "backfill", db_path, dry_run=dry_run, yes_many=yes_many)


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
