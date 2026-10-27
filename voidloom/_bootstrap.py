"""_bootstrap.py — STDLIB-ONLY host detection (install != onboard).

``voidloom bootstrap`` answers: *what kind of machine is this, and is the brain
already laid down here?* — WITHOUT importing a single heavy dependency. It is the
first thing a fresh install runs, and it must succeed on a bare host that has no
torch, no models, and maybe not even the DB yet.

Hard rules (plan #6042):
  * **Cannot import torch / jina / claude_memory.** Everything here is Python
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

    models = _detect_models()
    return {
        "db_path": str(db),
        "db_present": db_present,
        "db_size_mb": db_size_mb,
        "engine_present": engine_present,
        "models_present": models["present"],
        "models_detail": models,
    }


def _hf_cache_roots() -> List[Path]:
    roots: List[Path] = []
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        roots.append(Path(hf_home) / "hub")
    hub = os.environ.get("HUGGINGFACE_HUB_CACHE")
    if hub:
        roots.append(Path(hub))
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    # De-dup preserving order.
    seen = set()
    out = []
    for r in roots:
        rs = str(r)
        if rs not in seen:
            seen.add(rs)
            out.append(r)
    return out


def _engine_model_dirs() -> tuple:
    """``({tier: hf_cache_dirname}, source)`` read from the engine's EMBED_TIERS.

    BY AST, NEVER BY IMPORT. This module is the stdlib-only host detector -- the
    one thing that must run on a bare machine -- so importing ``claude_memory``
    to learn three strings would pull torch into ``voidloom bootstrap``.

    The engine writes ``EMBED_TIERS = {"nomic": (table, dim, NOMIC_MODEL_NAME), ...}``
    where the repo id is a module constant, so the constants are resolved first
    and then substituted. Anything unparseable yields the two-tier literal with
    ``source`` saying so: a fallback that pretends to be a derivation is the
    defect this replaces.
    """
    def _dirname(repo: str) -> str:
        return "models--" + repo.replace("/", "--")

    fallback = ({"nomic": _dirname("nomic-ai/nomic-embed-text-v1.5"),
                 "jina": _dirname("jinaai/jina-embeddings-v5-text-small")},
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
        out: Dict[str, str] = {}
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
            if repo:
                out[k.value] = _dirname(repo)
        if not out:
            return fallback
        return out, "derived from organs/claude_memory.py EMBED_TIERS (AST)"
    except Exception:
        return fallback


def _detect_models() -> Dict[str, Any]:
    """Heuristic: are the embedding models the engine names present in the HF
    cache? Path-only; we never import or load them. The HF hub cache encodes a
    repo id ``org/name`` as a directory ``models--org--name``.
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
    wanted, _derivation = _engine_model_dirs()
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
    return {"present": any_present, "by_model": found,
            # `present` is an ANY over the set and the field never said so.
            # D1-F2's secondary claim; stating the semantics costs one key.
            "present_semantics": "any: true when AT LEAST ONE named model is cached",
            "tiers_censused": sorted(found),
            "census_derived_from": _derivation,
            "cache_roots_checked": [str(r) for r in _hf_cache_roots()]}


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
        notes.append(f"CUDA GPU tier: {n} GPU(s) visible — Jina (1024-dim) "
                     "embedding tier is available.")
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
