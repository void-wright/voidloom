"""_bootstrap.py — STDLIB-ONLY host detection (install != onboard).

``voidloom bootstrap`` answers: *what kind of machine is this, and is the brain
already laid down here?* — WITHOUT importing a single heavy dependency. It is the
first thing a fresh install runs, and it must succeed on a bare host that has no
torch, no models, and maybe not even the DB yet.

Hard rules (plan #6042):
  * **Cannot import torch / an embedder / claude_memory.** Everything here is Python
    stdlib + best-effort subprocess to ``nvidia-smi``. (We deliberately do NOT
    ``import claude_memory`` — even though importing it happens to be torch-free
    today, presence-checking the brain by *path inspection* guarantees the
    bootstrap path can never regress into loading a model.)
  * **Never ``lspci``.** It is absent / fails under WSL2. GPU presence is probed
    via ``nvidia-smi --query-gpu`` only, and its absence is reported as
    "no CUDA GPU visible", never as an error.
  * **Deterministic + fail-graceful.** Every probe degrades to a safe default;
    the function always returns a dict and never raises.

Tier semantics (matches the two-tier embedding doctrine, engine #4258):
  * ``gpu``   — a CUDA GPU is visible AND torch is importable would be possible.
                Bootstrap reports the GPU facts; it does not *load* torch.
  * ``cpu``   — no CUDA GPU visible (laptop / teammate handoff tier).
  Bootstrap reports the *hardware* tier (gpu-capable vs cpu-only). Whether the
  GPU models are actually pulled is a separate fact (``models_present``).
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import _paths

# The default brain location is resolved RELATIVE-SELF in the single package
# resolver (``_paths.default_db``): this package (``voidloom/``) ships INSIDE the
# brain dir, so the brain DB sits beside the package. Bootstrap only *inspects*
# these paths; it never imports the engine, so no model can load on this path.
# The env knobs (WIZARD_LIBRARY_DB / CLAUDE_MEMORY_DB) always win over the
# fallback — see ``_paths.resolve_db``.


def detect_os() -> Dict[str, Any]:
    """OS / kernel facts, including WSL2 detection via /proc/version."""
    system = platform.system()
    release = platform.release()
    machine = platform.machine()
    is_wsl2 = False
    wsl_signature = None
    # WSL2 is detected by the 'microsoft' + 'WSL2' marker in the kernel string.
    # /proc/version is the reliable signal (platform.release() also carries it on
    # WSL2, but /proc/version is canonical and present on every WSL2 kernel).
    try:
        proc_version = Path("/proc/version").read_text(
            encoding="utf-8", errors="replace")
        low = proc_version.lower()
        if "microsoft" in low:
            wsl_signature = proc_version.strip()[:200]
            is_wsl2 = "wsl2" in low
    except Exception:
        pass
    return {
        "system": system,
        "release": release,
        "machine": machine,
        "is_wsl2": is_wsl2,
        "wsl_signature": wsl_signature,
    }


def detect_gpu() -> Dict[str, Any]:
    """Best-effort CUDA GPU probe via ``nvidia-smi --query-gpu`` ONLY.

    NEVER calls lspci (fails in WSL2). Absence of nvidia-smi or any error is
    reported as ``available: False`` with a reason — never an exception.
    """
    smi = shutil.which("nvidia-smi")
    if not smi:
        return {"available": False, "reason": "nvidia-smi not found on PATH",
                "gpus": []}
    try:
        proc = subprocess.run(
            [smi, "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8,
        )
    except Exception as e:  # subprocess failure, timeout, permission, etc.
        return {"available": False, "reason": f"nvidia-smi failed: {e}",
                "gpus": []}
    if proc.returncode != 0:
        reason = (proc.stderr or proc.stdout or "non-zero exit").strip()[:200]
        return {"available": False,
                "reason": f"nvidia-smi exit {proc.returncode}: {reason}",
                "gpus": []}
    gpus: List[Dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        name = parts[0] if len(parts) > 0 else "unknown"
        mem = None
        if len(parts) > 1:
            try:
                mem = int(float(parts[1]))
            except ValueError:
                mem = None
        driver = parts[2] if len(parts) > 2 else None
        gpus.append({"name": name, "memory_total_mb": mem,
                     "driver_version": driver})
    return {"available": bool(gpus),
            "reason": "ok" if gpus else "nvidia-smi returned no GPUs",
            "gpus": gpus}


def detect_brain(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Presence of the brain DB + a heuristic on whether GPU models are cached.

    Pure path inspection — does NOT import claude_memory, does NOT open the DB
    with sqlite (open is cheap but unnecessary for a presence check and could
    trip extension-load paths). ``models_present`` is a best-effort check of the
    HuggingFace cache for the embedding models the engine names.

    Path resolution honours the SINGLE env-first resolver (M1): when no explicit
    ``db_path`` is given we resolve via ``_paths.resolve_db()`` — the same chain
    the facade / guard / CLI use (``WIZARD_LIBRARY_DB`` > ``CLAUDE_MEMORY_DB`` >
    relative-self). The previous default derived only the *directory* from env
    (via the engine's env knobs) then re-appended a HARDCODED ``claude_memory.db``
    basename, so a
    ``CLAUDE_MEMORY_DB`` pointing at a differently-named DB (e.g. a sandbox
    ``voidloom_sandbox.db``) reported the wrong path and a false ``db_present``.
    Resolving the full env path (incl. its real basename) makes ``detect_brain``
    agree with every other verb even when callers still pass ``None``.
    """
    db = (Path(db_path).resolve() if db_path
          else Path(_paths.resolve_db()).resolve())
    db_present = False
    db_size_mb = None
    try:
        if db.exists() and db.is_file():
            db_present = True
            db_size_mb = round(db.stat().st_size / (1024 * 1024), 1)
    except Exception:
        db_present = False

    engine_present = False
    try:
        engine_present = (db.parent / "claude_memory.py").is_file()
    except Exception:
        engine_present = False

    models = _detect_models(db)
    return {
        "db_path": str(db),
        "db_present": db_present,
        "db_size_mb": db_size_mb,
        "engine_present": engine_present,
        "models_present": models["present"],
        "models_detail": models,
    }


def _lock_reader():
    """organs/embedder_lock.py, the package's one reader of the lock and the cache: stdlib-only, loaded from its file
    so this module still imports nothing heavy and leaves sys.path alone. None with no organs laid."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_vl_embedder_lock", _paths.organ_dir() / "embedder_lock.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _hf_cache_roots() -> List[Path]:
    """The one cache the models load from. Listing other roots here would report models present that the loader
    cannot see. With no organs laid, the library's default."""
    mod = _lock_reader()
    if mod is not None:
        try:
            return [mod.hf_hub_cache()]
        except Exception:
            pass
    return [Path.home() / ".cache" / "huggingface" / "hub"]


def _engine_model_dirs() -> tuple:
    """``({tier: hf_cache_dirname}, source)`` read from the engine's EMBED_TIERS."""
    tiers, source = _engine_tiers()
    return {tier: dirname for tier, (_table, dirname) in tiers.items()}, source


def _engine_tiers() -> tuple:
    """``({tier: (vec_table, hf_cache_dirname)}, source)`` read from the engine's EMBED_TIERS.

    BY AST, NEVER BY IMPORT. This module is the stdlib-only host detector -- the
    one thing that must run on a bare machine -- so importing ``claude_memory``
    to learn three strings would pull torch into ``voidloom bootstrap``.

    The engine writes ``EMBED_TIERS = {"nomic": (table, dim, NOMIC_MODEL_NAME), ...}``
    where the repo id is a module constant, so the constants are resolved first
    and then substituted. Anything unparseable yields the literal table with
    ``source`` saying so: a fallback that pretends to be a derivation is the
    defect this replaces.
    """
    def _dirname(repo: str) -> str:
        return "models--" + repo.replace("/", "--")

    fallback = ({"nomic": ("memory_embeddings", _dirname("nomic-ai/nomic-embed-text-v1.5")),
                 "jina": ("memory_embeddings_v2", _dirname("jinaai/jina-embeddings-v5-text-small")),
                 "qwen": ("memory_embeddings_v3", _dirname("Qwen/Qwen3-Embedding-0.6B"))},
                "FALLBACK LITERAL (engine EMBED_TIERS unreadable) — this census "
                "may be narrower than the engine's tier table")
    try:
        import ast
        src = (_paths.organ_dir() / "claude_memory.py").read_text(
            encoding="utf-8", errors="replace")
        tree = ast.parse(src)
        consts: Dict[str, str] = {}
        tiers = None
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                consts[tgt.id] = node.value.value
            elif tgt.id == "EMBED_TIERS" and isinstance(node.value, ast.Dict):
                tiers = node.value
        if tiers is None:
            return fallback
        out: Dict[str, tuple] = {}
        for k, v in zip(tiers.keys, tiers.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            if not isinstance(v, (ast.Tuple, ast.List)) or len(v.elts) < 3:
                continue
            repo_node = v.elts[2]
            repo = None
            if isinstance(repo_node, ast.Constant) and isinstance(repo_node.value, str):
                repo = repo_node.value
            elif isinstance(repo_node, ast.Name):
                repo = consts.get(repo_node.id)
            table = v.elts[0].value if isinstance(v.elts[0], ast.Constant) else None
            if repo:
                out[k.value] = (table, _dirname(repo))
        if not out:
            return fallback
        return out, "derived from organs/claude_memory.py EMBED_TIERS (AST)"
    except Exception:
        return fallback


def _brain_tier(db: Path, tiers: Dict[str, tuple]) -> tuple:
    """``(tier, how)`` for a brain with no lock, by the read the engine itself makes for one:
    `metadata.active_embedding_table` when that table holds a vector (a variable never moves such a brain), else
    WIZARD_EMBED_MODEL, else the recorded table, else nomic. ``tier`` is None when the brain cannot be read."""
    env = os.environ.get("WIZARD_EMBED_MODEL", "").strip().lower()
    holds = False
    try:
        import sqlite3
        conn = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
            try:
                holds = bool(row) and conn.execute(f"SELECT 1 FROM {row[0]}_rowids LIMIT 1").fetchone() is not None
            except sqlite3.Error:
                holds = False
        finally:
            conn.close()
    except Exception:
        return (env, "WIZARD_EMBED_MODEL") if env in tiers else (None, "the brain could not be read")
    if env in tiers and not holds:
        return env, "WIZARD_EMBED_MODEL"
    for tier, (table, _dirname) in tiers.items():
        if row and row[0] == table:
            return tier, "metadata.active_embedding_table"
    return "nomic", "the engine's default for a brain with no recorded table"


def _engine_jina_pin() -> Optional[tuple]:
    """``(model_id, revision, files)`` the engine loads jina at, BY AST: importing the module pins a card."""
    try:
        import ast
        tree = ast.parse((_paths.organ_dir() / "embedding_upgrade.py").read_text(encoding="utf-8", errors="replace"))
        got = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
               if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
               and n.targets[0].id in ("JINA_MODEL_NAME", "JINA_REVISION", "JINA_SNAPSHOT_FILES")}
        return got["JINA_MODEL_NAME"], got["JINA_REVISION"], got["JINA_SNAPSHOT_FILES"]
    except Exception:
        return None


def _loadable_at_pin(tier: Optional[str], tiers: Dict[str, tuple]) -> Optional[bool]:
    """Can a brain with no lock load `tier`: is the snapshot the loader reads there, at the tier's pin, whole? A folder of
    that name is what an interrupted download leaves. None when the pin cannot be read here, which claims nothing."""
    mod = _lock_reader()
    if mod is None or tier not in tiers:
        return None
    dirname = tiers[tier][1]
    try:
        pins = None
        for entry in mod.catalogue_tiers().values():
            if "models--" + entry["model_id"].replace("/", "--") == dirname:
                pins = [(entry["model_id"], entry["revision"], entry.get("files"))]
                if isinstance(entry.get("code"), dict):
                    pins.append((entry["code"]["model_id"], entry["code"]["revision"], entry["code"].get("files")))
        if pins is None:
            jina = _engine_jina_pin()
            pins = [jina] if jina and "models--" + jina[0].replace("/", "--") == dirname else None
        if pins is None:
            return None
        return all(mod.snapshot_complete(mod.snapshot_dir_for(m, r), f) for m, r, f in pins)
    except Exception:
        return None


def _brain_model(out: Dict[str, Any], db: Optional[Path], tiers: Dict[str, tuple]) -> None:
    """Narrow `present` from "some model is cached" to "the model THIS brain loads is cached". A brain with a lock
    answers for every snapshot the lock names, in the one cache the loader reads, its remote code included; a lock that
    cannot be read answers no. A brain with no lock answers for its tier's model, whichever tier that is, shipped or
    not. With no brain on disk there is nothing to narrow to, and the census stands, labelled."""
    if db is None:
        return
    db = Path(db)
    lock_path = db.resolve().parent / "embedder.lock.json"      # symlinks followed, as the lock's own reader does
    if not lock_path.exists():
        if not db.is_file():
            return
        mod = _lock_reader()
        lost = mod.lost_lock(lock_path.parent, db) if mod is not None else None
        if lost is not None:
            # the brain's own record says a lock was written: it is a locked brain whose lock is gone, never one that
            # had none, and no model can be vouched for until the lock is back
            out.update({"lock": str(lock_path), "lock_snapshots": {}, "present": False, "lock_refusal": "LOCK_MISSING",
                        "was_locked": True, "lock_tier": lost.get("tier"),
                        "present_semantics": "the lock's: it was written and is gone, so no model can be vouched for"})
            return
        tier, how = _brain_tier(db, tiers)
        out["brain_tier"] = tier
        loadable = _loadable_at_pin(tier, tiers)
        out["present"] = bool(out["by_model"].get(tier)) if loadable is None else loadable
        import importlib.util
        if tier == "jina" and importlib.util.find_spec("peft") is None:
            out["present"] = False
            out["missing_modules"] = ["peft"]       # its remote code imports peft at load: bytes on disk are not a model
        out["present_semantics"] = (f"this brain's: the {tier} model, its tier by {how} (the brain has no lock)"
                                    if tier else f"this brain's: unknown, {how}")
        return
    mod = _lock_reader()
    try:
        if mod is None:
            raise ValueError("organs/embedder_lock.py is not importable")
        lock = mod.read_lock(lock_path, brain=db)
        pins = [(lock["model_id"], lock["revision"], lock.get("files"))]
        if isinstance(lock.get("code"), dict):
            pins.append((lock["code"]["model_id"], lock["code"]["revision"], lock["code"].get("files")))
        snaps = {f"{mid}@{rev[:12]}": mod.snapshot_complete(mod.snapshot_dir_for(mid, rev), files)
                 for mid, rev, files in pins}
        partial = [f"{mid}@{rev[:12]}" for mid, rev, files in pins if mod.snapshot_dir_for(mid, rev) is not None
                   and not mod.snapshot_complete(mod.snapshot_dir_for(mid, rev), files)]
    except Exception as e:
        out.update({"lock": str(lock_path), "lock_snapshots": {}, "present": False,
                    "lock_refusal": getattr(e, "code", type(e).__name__),
                    "present_semantics": "the lock's: it cannot be read, so no model can be vouched for"})
        return
    out.update({"lock": str(lock_path), "lock_snapshots": snaps, "present": all(snaps.values()),
                "present_semantics": "the lock's: every snapshot the install lock names (model + remote code) "
                                     "is under the one cache the loader reads"})
    if partial:
        out.update({"lock_incomplete": partial, "lock_incomplete_fix": mod.fetch_command(db)})


def _detect_models(db: Optional[Path] = None) -> Dict[str, Any]:
    """Heuristic: are the embedding models present in the HF cache? Path-only; we never import or load them. The HF hub
    cache encodes a repo id ``org/name`` as a directory ``models--org--name``.

    `present` answers for `db`'s own model (`_brain_model`). The census of every tier the engine names stays beside it
    as `by_model` and `any_engine_tier_present`, because a model cached for some other tier does nothing for this one.
    """
    # CRUCIBLE-3 D1-F2: this was a HAND-TYPED dict of TWO, against an engine that
    # names THREE -- and the one it omitted, Qwen3-Embedding-0.6B, is the one the
    # engine calls "the shipped GPU default" (jina is cc-by-nc-4.0 and CANNOT
    # SHIP). So a correct, current host carrying exactly the shipped model got
    # `models_present: false`, and `true` could be earned entirely by a model that
    # cannot ship. The docstring above says "the models the ENGINE NAMES"; the
    # census named its own.
    #
    # The sharpest part of the finding, and the reason this is not a mitigation:
    # `by_model` and `cache_roots_checked` are already in the envelope, so this is
    # the one place in the package where a rate ships with its denominator
    # attached -- AND THE DENOMINATOR WAS THE WRONG ONE. A published domain that
    # does not match the domain the field's name promises is a more convincing
    # error, not a smaller one.
    #
    # Derived from the engine's own EMBED_TIERS now. By AST, never by import:
    # this module is the STDLIB-ONLY host detector and importing the engine would
    # pull torch into `voidloom bootstrap`, which is the one verb that must run on
    # a bare host. The derivation source is published beside the result, and a
    # parse that yields nothing falls back to the literal AND SAYS SO rather than
    # silently censusing two again.
    tiers, _derivation = _engine_tiers()
    wanted = {tier: dirname for tier, (_table, dirname) in tiers.items()}
    found: Dict[str, bool] = {k: False for k in wanted}
    for root in _hf_cache_roots():
        try:
            if not root.is_dir():
                continue
            for key, dirname in wanted.items():
                if (root / dirname).is_dir():
                    found[key] = True
        except Exception:
            continue
    any_present = any(found.values())
    out: Dict[str, Any] = {"present": any_present, "by_model": found,
                           # An ANY over the set, until a brain narrows it; stating the semantics costs one key.
                           "present_semantics": "any: true when AT LEAST ONE named model is cached",
                           "any_engine_tier_present": any_present,
                           "tiers_censused": sorted(found),
                           "census_derived_from": _derivation,
                           "cache_roots_checked": [str(r) for r in _hf_cache_roots()]}
    _brain_model(out, db, tiers)
    return out


def bootstrap(db_path: Optional[str] = None) -> Dict[str, Any]:
    """The full stdlib-only host report. Deterministic; never raises.

    Returns a dict with: os, gpu, brain, and a derived ``tier`` +
    ``onboarded`` summary. ``tier`` is the *hardware* tier; ``onboarded`` says
    whether this host is ready to run heavy verbs (brain + models present).
    """
    os_info = detect_os()
    gpu_info = detect_gpu()
    brain_info = detect_brain(db_path)

    tier = "gpu" if gpu_info.get("available") else "cpu"
    # 'onboarded' = the brain DB is here AND (on a gpu host) the models are
    # cached. On a cpu host the lighter nomic tier may still need a pull, so we
    # report models_present honestly and let the operator decide.
    onboarded = bool(brain_info.get("db_present")
                     and brain_info.get("models_present"))

    return {
        "tier": tier,
        "onboarded": onboarded,
        "os": os_info,
        "gpu": gpu_info,
        "brain": brain_info,
        "notes": _tier_notes(tier, brain_info, gpu_info),
    }


def _tier_notes(tier: str, brain: Dict[str, Any],
                gpu: Dict[str, Any]) -> List[str]:
    notes: List[str] = []
    if tier == "gpu":
        n = len(gpu.get("gpus", []))
        notes.append(f"CUDA GPU tier: {n} GPU(s) visible — the Qwen3-Embedding-0.6B "
                     "(1024-dim) embedding tier is available.")
    else:
        notes.append("CPU-only tier: no CUDA GPU visible — the lighter Nomic "
                     "(768-dim) tier is the path here.")
    if not brain.get("db_present"):
        notes.append("Brain DB not found here — run `claude_memory.py init` to "
                     "create it (this is install, not yet onboard).")
    if not brain.get("models_present"):
        notes.append("Embedding models not in the HF cache — first heavy verb "
                     "(recall/store) will pull them (onboard step).")
    return notes
