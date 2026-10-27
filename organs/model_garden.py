#!/usr/bin/env python3
"""model_garden.py — Voidloom's unified model-access layer (L5-M0a.2).

ONE abstraction in front of every local model Voidloom uses. The "hands" of the
brain: each model is lazy, process-cached, kill-switchable via an env flag, and
GRACEFUL-ABSENT — a model that is not downloaded, OR a package that is not
installed, OR a registry entry that is disabled, DEGRADES that one capability to
None (with a logged reason) and NEVER raises into a caller. A half-provisioned
machine still runs.

Why a registry-dict abstraction (the Model Garden):
  Today the only local models are jina (embedder) + bge (reranker), each behind
  its OWN ad-hoc lazy loader. Downstream L5 organs (the belief engine D, the
  salience/affect gate G, the consolidation pipeline) each would otherwise
  re-invent model loading. The Garden unifies the existing three loaders AND
  adds the cuda:0-resident encoder pack (NLI / GLiNER / zeroshot / go_emotions)
  behind ONE `get_model(key)` front door, so no organ re-implements caching,
  device pinning, or absence handling.

Two distinct loading disciplines:
  * RESIDENT (the default) — the encoder pack and the three existing loaders
    load DIRECTLY on cuda:0 (or CPU). Encoders never "burst" both GPUs; they sit
    on the compute card permanently. No gpu_guard ritual.
  * BURST (the M0b hook) — a future GPU-burst model (a tensor-parallel local LLM
    for nightly dream-synthesis) must go through `get_burst_model(key, profile)`,
    which calls `gpu_guard.acquire(profile)` FIRST and returns
    `(model|None, verdict)`. model_garden = the hands; gpu_guard = the gatekeeper.
    model_garden CALLS gpu_guard for any burst model; it never reimplements the
    gatekeeper's logic and imports nothing of gpu_guard beyond acquire/release.
    The actual M0b LLM LOAD is DEFERRED (registered, returns None+verdict
    gracefully): the burst PATH is wired and proven now; the heavy model is
    pulled at L5-C1, not here.

model_garden is purely ADDITIVE. It does NOT change the recall path; it does NOT
modify claude_memory.py, hybrid_retrieval.py, or embedding_upgrade.py. It ROUTES
through their existing loaders (it never duplicates their module-global caches),
so the live stack keeps its single process-cache per model.

Manifest contract (the single source of truth, read defensively):
  `load_manifest(path)` reads `voidloom_manifest.json` for per-key enable flags
  and device routing. A missing / malformed manifest falls back to the in-code
  registry defaults — the manifest is an OVERLAY on the durable in-code schema,
  never a hard dependency. The INSTALLER (voidloom_manifest.write_manifest) is
  the sole writer; every consumer (this module) only reads.

Confinement: generated in an ACE workspace; the orchestrator applies it to the
live tree after close. Side-effect-free at import; the only effects are lazy
model loads (VRAM) triggered explicitly by get_model().
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("voidloom.model_garden")

# ============================================================
# Where the live substrate lives (for routing to the 3 existing loaders).
# ============================================================
# model_garden ships alongside embedding_upgrade.py / hybrid_retrieval.py /
# gpu_guard.py in <brain>/. We import them by MODULE NAME (they
# are siblings on sys.path when the package is installed). The imports are done
# LAZILY inside the loaders so that (a) import of model_garden never pulls torch,
# and (b) a missing sibling degrades that one capability gracefully.

# The canonical manifest filename + its default location (next to this module).
MANIFEST_FILENAME = "voidloom_manifest.json"


def _torch_device(torch_mod) -> str:
    """THE device for this organ (handoff round 1, lane L1): voidloom_device.torch_device() -- the backend's pin first,
    then the backend's own API (CUDA, ROCm, MPS, XPU); 'cpu' on any degraded, refused or unconfirmed basis. Replaces
    the per-organ `"cuda:0" if _cuda_usable(torch) else "cpu"`, which could only ever say CUDA or CPU."""
    try:
        from voidloom_device import torch_device as _td
    except ImportError:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from voidloom_device import torch_device as _td
    return _td(torch_mod)


def _default_manifest_path() -> str:
    """The live manifest lives beside this module. Tests / installers override."""
    return str(Path(__file__).resolve().parent.parent / MANIFEST_FILENAME)


# ============================================================
# Loader-type tags — HOW a registry entry is materialized.
# ============================================================
# Each tag maps to one library entry point. Chosen per model in MODEL_REGISTRY.
LOADER_JINA = "jina-existing-loader"          # -> embedding_upgrade._get_jina_model
LOADER_QWEN = "qwen-embedding-loader"        # -> embedding_upgrade._get_qwen_model
LOADER_NOMIC = "nomic-existing-loader"        # -> embedding_upgrade._get_nomic_model
LOADER_RERANKER = "reranker-existing-loader"  # -> hybrid_retrieval._load_reranker
LOADER_CROSS_ENCODER = "cross-encoder"        # -> sentence_transformers.CrossEncoder
LOADER_GLINER = "gliner"                       # -> gliner.GLiNER.from_pretrained
LOADER_ZEROSHOT = "transformers-zeroshot"     # -> transformers.pipeline("zero-shot-classification")
LOADER_TEXT_CLF = "transformers-text-clf"     # -> transformers.pipeline("text-classification", top_k=None)
LOADER_BURST_LLM = "burst-llm-deferred"       # -> gpu_guard-gated; DEFERRED to M0b

# The two loading disciplines (drives get_model vs get_burst_model routing).
ROLE_RESIDENT = "cuda:0-resident"   # loads directly, no gpu_guard ritual
ROLE_BURST = "dual-card-burst"      # MUST go through gpu_guard.acquire()


# ============================================================
# ModelSpec — one registry entry.
# ============================================================

@dataclass(frozen=True)
class ModelSpec:
    """A single model the Garden can serve.

    key             : the Garden handle (get_model('jina'), etc.).
    repo_id         : HuggingFace repo id (for cache-presence probing + load).
    role            : ROLE_RESIDENT (default) or ROLE_BURST (gpu_guard-gated).
    loader_type     : one of the LOADER_* tags above.
    device          : 'cuda:0' | 'cpu' | 'auto' — the resident device hint. The
                      three existing loaders own their OWN device choice; for them
                      this is documentation. 'auto' = cuda:0 if available else cpu.
    footprint_mb    : approximate RAM/VRAM footprint (for doctor + burst profile).
    enable_default  : in-code default enable flag (a manifest may override it).
    kill_switch_env : env var that, when set truthy, DISABLES this model
                      regardless of the manifest (operator panic switch).
    requires_packages: python packages that MUST be importable for this loader.
    pipeline_task   : the transformers pipeline task (only for pipeline loaders).
    description     : human note (what Voidloom organ consumes it).
    """
    key: str
    repo_id: str
    role: str
    loader_type: str
    device: str
    footprint_mb: int
    enable_default: bool
    kill_switch_env: str
    requires_packages: Tuple[str, ...]
    pipeline_task: Optional[str] = None
    description: str = ""


# ============================================================
# THE REGISTRY — the single source of truth for every local model.
# ============================================================
# Ordered: the 3 existing loaders first (wrapped, not reimplemented), then the
# cuda:0-resident encoder pack, then the DEFERRED burst LLM hook.

MODEL_REGISTRY: Dict[str, ModelSpec] = {
    # ---- The 3 existing loaders (DELEGATE — never reimplement their caching) ----
    "jina": ModelSpec(
        key="jina",
        repo_id="jinaai/jina-embeddings-v5-text-small",
        role=ROLE_RESIDENT,
        loader_type=LOADER_JINA,
        device="cuda:0",
        footprint_mb=1500,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_JINA",
        requires_packages=("sentence_transformers", "torch"),
        description="Primary embedder (1024-dim, LoRA task adapters). Recall hot path.",
    ),
    "qwen": ModelSpec(
        key="qwen",
        repo_id="Qwen/Qwen3-Embedding-0.6B",
        role=ROLE_RESIDENT,
        loader_type=LOADER_QWEN,   # its OWN loader: the jina loader hardcodes jina
        device="cuda:0",
        footprint_mb=1500,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_QWEN",
        requires_packages=("sentence_transformers", "torch"),
        description=(
            "SHIPPED GPU embedder (1024-dim, apache-2.0). Default for every "
            "installed copy on a GPU host. jina is the same width and the same "
            "base model but cc-by-nc-4.0, so it cannot ship -- it remains selectable "
            "via WIZARD_EMBED_MODEL=jina for non-commercial local use."
        ),
    ),
    "nomic": ModelSpec(
        key="nomic",
        repo_id="nomic-ai/nomic-embed-text-v1.5",
        role=ROLE_RESIDENT,
        loader_type=LOADER_NOMIC,
        device="cuda:0",
        footprint_mb=550,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_NOMIC",
        requires_packages=("sentence_transformers", "torch"),
        description="Baseline embedder for A/B comparison (cross-tier bridge basis).",
    ),
    "reranker": ModelSpec(
        key="reranker",
        repo_id="BAAI/bge-reranker-v2-m3",
        role=ROLE_RESIDENT,
        loader_type=LOADER_RERANKER,
        device="cuda:0",
        footprint_mb=1100,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_RERANKER",
        requires_packages=("sentence_transformers", "torch"),
        description=("bge-reranker-v2-m3 CrossEncoder: the FALLBACK reranker (and the VOIDLOOM_RERANKER=bge backend). "
                     "The canonical reranker is Qwen/Qwen3-Reranker-0.6B (qwen_reranker.py); whether either runs is "
                     "hybrid_retrieval.reranker_gate()'s decision."),
    ),
    # ---- The cuda:0-resident ENCODER PACK (loads directly, no gpu_guard) -------
    "nli": ModelSpec(
        key="nli",
        repo_id="cross-encoder/nli-deberta-v3-base",
        role=ROLE_RESIDENT,
        loader_type=LOADER_CROSS_ENCODER,
        device="cuda:0",
        footprint_mb=750,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_NLI",
        requires_packages=("sentence_transformers", "torch"),
        description="NLI entailment/contradiction (L5-D belief-engine contradiction edges). CRITICAL PATH.",
    ),
    "gliner": ModelSpec(
        key="gliner",
        repo_id="urchade/gliner_multi-v2.1",
        role=ROLE_RESIDENT,
        loader_type=LOADER_GLINER,
        device="cuda:0",
        footprint_mb=900,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_GLINER",
        requires_packages=("gliner", "torch"),
        description="Span NER for entity linking (L5-D.2 same_as edges).",
    ),
    "zeroshot": ModelSpec(
        key="zeroshot",
        repo_id="MoritzLaurer/deberta-v3-base-zeroshot-v2.0",
        role=ROLE_RESIDENT,
        loader_type=LOADER_ZEROSHOT,
        device="cuda:0",
        footprint_mb=750,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_ZEROSHOT",
        requires_packages=("transformers", "torch"),
        pipeline_task="zero-shot-classification",
        description="Zero-shot label scoring (L5-G salience gate).",
    ),
    "emotion": ModelSpec(
        key="emotion",
        # go_emotions: optimum is ABSENT on this machine, so we use PLAIN
        # transformers (NOT the ONNX-INT8 path). The ONNX optimization is a
        # graceful upgrade for later, not a dependency.
        repo_id="SamLowe/roberta-base-go_emotions",
        role=ROLE_RESIDENT,
        loader_type=LOADER_TEXT_CLF,
        device="cuda:0",
        footprint_mb=500,
        enable_default=True,
        kill_switch_env="VOIDLOOM_DISABLE_EMOTION",
        requires_packages=("transformers", "torch"),
        pipeline_task="text-classification",
        description="28-way affect classifier (L5-G affect / M2 lighthouse). PLAIN transformers (optimum absent).",
    ),
    # ---- The DEFERRED burst LLM (registered HOOK; load deferred to M0b) --------
    "dream_llm": ModelSpec(
        key="dream_llm",
        repo_id="Qwen/Qwen3-Reranker-0.6B",  # placeholder repo; real M0b LLM TBD
        role=ROLE_BURST,
        loader_type=LOADER_BURST_LLM,
        device="auto",
        footprint_mb=18 * 1024,  # 18 GB INT4 14B-class tensor-parallel ritual
        enable_default=False,    # DEFERRED — not provisioned until M0b
        kill_switch_env="VOIDLOOM_DISABLE_DREAM_LLM",
        requires_packages=("torch",),
        description="GPU-BURST local LLM for nightly dream-synthesis. DEFERRED to M0b; "
                    "registered so the gpu_guard burst hook is wired + proven now.",
    ),
}


# ============================================================
# Process-level model cache (per Garden key).
# ============================================================
# For jina/nomic/reranker the actual model object lives in the EXISTING loader's
# module-global cache; we cache a *reference* here so the Garden doesn't re-route
# through the delegate on every call. For the encoder pack we own the cache.
# Cache value of None means "load attempted and failed/absent — do not retry"
# (mirrors hybrid_retrieval._load_reranker's None-caching contract).
_MODEL_CACHE: Dict[str, Any] = {}
# Why each None was cached (so callers/doctor can explain a degraded capability).
_ABSENCE_REASON: Dict[str, str] = {}


def _truthy_env(name: str) -> bool:
    """An env flag is 'set' iff present and not one of the falsey spellings."""
    val = os.environ.get(name)
    if val is None:
        return False
    return val.strip().lower() not in ("", "0", "false", "no", "off")


def _resolve_device(spec: ModelSpec) -> str:
    """Resolve a spec's device hint to a concrete device string.

    A spec's 'cuda:0' or 'auto' means "the accelerator, if any": the ONE resolver
    (voidloom_device.torch_device) answers -- 'cuda:0' on CUDA or ROCm, 'mps',
    'xpu:0', or 'cpu' -- and applies that backend's pin before asking torch.
    """
    if spec.device == "cpu":
        return "cpu"
    # 'cuda:0' or 'auto' both resolve through the accelerator resolver.
    try:
        import torch  # local import: import-of-model_garden stays torch-free
        return _torch_device(torch)
    except Exception:
        return "cpu"


def _missing_packages(spec: ModelSpec) -> List[str]:
    """Return the spec's required packages that are NOT importable. [] = all present."""
    missing = []
    for pkg in spec.requires_packages:
        try:
            if importlib.util.find_spec(pkg) is None:
                missing.append(pkg)
        except Exception:
            # find_spec can raise on a half-broken namespace package; treat as missing.
            missing.append(pkg)
    return missing


def _cache_present(spec: ModelSpec) -> bool:
    """True iff `spec.repo_id` is present in the local HuggingFace cache.

    Uses huggingface_hub.scan_cache_dir (the durable cache index). Fail-graceful:
    if hub is absent or the scan errors, returns False (treat as absent → the
    capability degrades, never crashes). Does NOT download anything.
    """
    try:
        from huggingface_hub import scan_cache_dir
    except Exception:
        return False
    try:
        info = scan_cache_dir()
        return any(r.repo_id == spec.repo_id for r in info.repos)
    except Exception:
        return False


def _is_enabled(key: str, manifest: Optional[dict] = None) -> Tuple[bool, str]:
    """Resolve the enable state for a key (manifest overlay over in-code default),
    honoring the per-model kill-switch env (which ALWAYS wins, panic switch).

    Returns (enabled, reason). Never raises.
    """
    spec = MODEL_REGISTRY.get(key)
    if spec is None:
        return False, f"unknown model key {key!r}"
    # 1. Kill switch wins unconditionally (operator panic switch).
    if _truthy_env(spec.kill_switch_env):
        return False, f"kill-switch env {spec.kill_switch_env} is set"
    # 2. Manifest overlay, if present and well-formed.
    enabled = spec.enable_default
    if manifest:
        try:
            mblock = (manifest.get("models") or {}).get(key) or {}
            if isinstance(mblock, dict) and "enabled" in mblock:
                enabled = bool(mblock["enabled"])
        except Exception:
            # Malformed manifest block → fall back to the in-code default.
            enabled = spec.enable_default
    return enabled, ("enabled" if enabled else "disabled by manifest/default")


# ============================================================
# Defensive manifest reader (the in-code registry IS the fallback).
# ============================================================

def load_manifest(path: Optional[str] = None) -> dict:
    """Read voidloom_manifest.json for enable flags + device routing.

    DEFENSIVE: a missing OR malformed file returns a SYNTHESIZED manifest built
    from the in-code registry defaults (so every caller always gets a usable
    dict with the same shape). NEVER raises — the manifest is an overlay on the
    durable in-code schema, not a hard dependency.

    The synthesized fallback carries the in-code `models` enable flags and a
    minimal `device_routing` so a machine with NO manifest still routes models.
    """
    target = path or _default_manifest_path()
    fallback = _synth_manifest_from_registry()
    try:
        p = Path(target)
        if not p.is_file():
            return fallback
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return fallback
        # Merge: start from the in-code fallback, overlay any present top-level
        # blocks from the file. This guarantees the returned dict always has the
        # registry-derived `models` keys even if the file omits one.
        merged = dict(fallback)
        for k, v in data.items():
            merged[k] = v
        # Ensure every registry key still has a models entry (file may be partial).
        models = dict(fallback["models"])
        file_models = data.get("models")
        if isinstance(file_models, dict):
            for mk, mv in file_models.items():
                if isinstance(mv, dict):
                    base = dict(models.get(mk, {}))
                    base.update(mv)
                    models[mk] = base
        merged["models"] = models
        return merged
    except Exception:
        # Any read/parse failure is ambiguity → the safe in-code fallback.
        return fallback


def _synth_manifest_from_registry() -> dict:
    """Build the in-code fallback manifest (enable flags + minimal routing)."""
    models = {}
    for key, spec in MODEL_REGISTRY.items():
        models[key] = {
            "repo_id": spec.repo_id,
            "role": spec.role,
            "loader_type": spec.loader_type,
            "device": spec.device,
            "enabled": spec.enable_default,
        }
    return {
        "_source": "in-code registry fallback (no manifest file)",
        "models": models,
        "device_routing": {
            "embed": "cuda:0-single",
            "rerank": "cuda:0-single",
            "nli": "cuda:0-single",
            "ner": "cuda:0-single",
            "salience": "cuda:0-single",
            "affect": "cuda:0-single",
            "dream_synthesis": "dual-card-burst",
            "default": "cloud-claude-p",
        },
    }


# ============================================================
# Per-loader_type materializers (each fail-graceful → None on any failure).
# ============================================================

def _load_via_jina(spec: ModelSpec) -> Any:
    """Delegate to embedding_upgrade._get_jina_model (its module-global cache)."""
    eu = importlib.import_module("embedding_upgrade")
    return eu._get_jina_model()


def _load_via_qwen(spec: ModelSpec) -> Any:
    """Delegate to embedding_upgrade._get_qwen_model (its module-global cache)."""
    eu = importlib.import_module("embedding_upgrade")
    return eu._get_qwen_model()


def _load_via_nomic(spec: ModelSpec) -> Any:
    eu = importlib.import_module("embedding_upgrade")
    return eu._get_nomic_model()


def _load_via_reranker(spec: ModelSpec) -> Any:
    """Delegate to hybrid_retrieval._load_reranker (its module-global cache).

    That loader returns None on its own failure and caches the None — we honor
    that contract: a None back means 'reranker unavailable', which we surface as
    graceful-absent rather than an object.
    """
    hr = importlib.import_module("hybrid_retrieval")
    return hr._load_reranker()


def _load_cross_encoder(spec: ModelSpec) -> Any:
    """Encoder-pack CrossEncoder (e.g. the NLI model). Resident on cuda:0."""
    from sentence_transformers import CrossEncoder
    device = _resolve_device(spec)
    logger.info("model_garden: loading CrossEncoder %s on %s", spec.repo_id, device)
    return CrossEncoder(spec.repo_id, device=device)


def _load_gliner(spec: ModelSpec) -> Any:
    """GLiNER span NER. Resident on cuda:0. optimum/ONNX NOT used (load_onnx_model
    defaults False — plain torch path)."""
    from gliner import GLiNER
    device = _resolve_device(spec)
    logger.info("model_garden: loading GLiNER %s on %s", spec.repo_id, device)
    model = GLiNER.from_pretrained(spec.repo_id, map_location=device)
    # GLiNER.from_pretrained's map_location places weights; move the wrapper too
    # so .predict_entities runs on the resident device.
    try:
        model = model.to(device)
    except Exception:
        pass  # CPU-only fallback already placed by map_location.
    return model


def _load_zeroshot(spec: ModelSpec) -> Any:
    """transformers zero-shot pipeline. PLAIN transformers (no optimum/ONNX)."""
    from transformers import pipeline
    device = _resolve_device(spec)
    device_arg = device if device != "cpu" else -1   # transformers takes a device string (mps, xpu:0)
    logger.info("model_garden: loading zero-shot pipeline %s on %s", spec.repo_id, device)
    return pipeline(spec.pipeline_task or "zero-shot-classification",
                    model=spec.repo_id, device=device_arg)


def _load_text_clf(spec: ModelSpec) -> Any:
    """transformers text-classification pipeline (go_emotions), top_k=None for all
    28 labels. PLAIN transformers — optimum is ABSENT so we never take the ONNX
    path; that is a graceful upgrade for later, not a dependency."""
    from transformers import pipeline
    device = _resolve_device(spec)
    device_arg = device if device != "cpu" else -1   # transformers takes a device string (mps, xpu:0)
    logger.info("model_garden: loading text-classification pipeline %s on %s (plain transformers)",
                spec.repo_id, device)
    return pipeline(spec.pipeline_task or "text-classification",
                    model=spec.repo_id, device=device_arg, top_k=None)


# loader_type → materializer. (BURST is NOT here: it goes through get_burst_model.)
_RESIDENT_LOADERS: Dict[str, Callable[[ModelSpec], Any]] = {
    LOADER_JINA: _load_via_jina,
    LOADER_QWEN: _load_via_qwen,
    LOADER_NOMIC: _load_via_nomic,
    LOADER_RERANKER: _load_via_reranker,
    LOADER_CROSS_ENCODER: _load_cross_encoder,
    LOADER_GLINER: _load_gliner,
    LOADER_ZEROSHOT: _load_zeroshot,
    LOADER_TEXT_CLF: _load_text_clf,
}


# ============================================================
# is_available — cheap, NON-loading presence check.
# ============================================================

def is_available(key: str, manifest: Optional[dict] = None) -> bool:
    """True iff `key` is registered AND enabled AND all its packages import AND
    its repo is cache-present. Does NOT load the model. Never raises.

    A burst model (dream_llm) reports availability the same way (registered +
    enabled + packages + cache), but the actual model is only obtainable via
    get_burst_model; is_available answers "could this be served at all", which
    for the deferred burst LLM is False by default (enable_default=False).
    """
    spec = MODEL_REGISTRY.get(key)
    if spec is None:
        return False
    enabled, _reason = _is_enabled(key, manifest)
    if not enabled:
        return False
    if _missing_packages(spec):
        return False
    if not _cache_present(spec):
        return False
    return True


# ============================================================
# get_model — the lazy front door (RESIDENT models only).
# ============================================================

def get_model(key: str, *, force: bool = False, manifest: Optional[dict] = None) -> Optional[Any]:
    """Lazy, process-cached accessor for a RESIDENT model.

    Routing:
      jina/nomic/reranker  -> the 3 EXISTING loaders (delegate; their cache).
      nli/gliner/zeroshot/emotion -> load via the right library on cuda:0.
      a BURST model (dream_llm) -> NOT served here; use get_burst_model(). We
        return None + a logged reason (graceful — never raise) so a caller that
        forgets the burst hook degrades instead of crashing.

    GRACEFUL-ABSENT EVERYWHERE: unknown key / disabled / missing package /
    missing cache / load exception → return None + a logged reason, NEVER raise.
    A None is CACHED (don't retry a known-absent model) unless force=True.

    `force=True` bypasses the Garden cache (re-resolves + re-loads; for the three
    delegate loaders it re-routes through them — they keep their own cache unless
    they expose a force, which only the reranker does).
    """
    spec = MODEL_REGISTRY.get(key)
    if spec is None:
        logger.warning("model_garden.get_model: unknown key %r → None", key)
        _ABSENCE_REASON[key] = f"unknown model key {key!r}"
        return None

    if not force and key in _MODEL_CACHE:
        return _MODEL_CACHE[key]  # may be a model OR a cached None (known-absent)

    # Burst models never load through get_model.
    if spec.role == ROLE_BURST:
        reason = (f"{key!r} is a GPU-BURST model — use get_burst_model(key, profile) "
                  f"(it is gpu_guard-gated; the load is also deferred to M0b)")
        logger.warning("model_garden.get_model: %s", reason)
        _ABSENCE_REASON[key] = reason
        _MODEL_CACHE[key] = None
        return None

    # Enable / kill-switch gate.
    enabled, ereason = _is_enabled(key, manifest)
    if not enabled:
        logger.info("model_garden.get_model: %r %s → None", key, ereason)
        _ABSENCE_REASON[key] = ereason
        _MODEL_CACHE[key] = None
        return None

    # Package presence.
    missing = _missing_packages(spec)
    if missing:
        reason = f"missing package(s) {missing} for {key!r}"
        logger.warning("model_garden.get_model: %s → None (graceful-absent)", reason)
        _ABSENCE_REASON[key] = reason
        _MODEL_CACHE[key] = None
        return None

    # Cache presence (so we never trigger a blocking download inside recall).
    if not _cache_present(spec):
        reason = f"repo {spec.repo_id!r} not in HF cache for {key!r}"
        logger.warning("model_garden.get_model: %s → None (graceful-absent)", reason)
        _ABSENCE_REASON[key] = reason
        _MODEL_CACHE[key] = None
        return None

    # Materialize via the right loader.
    loader = _RESIDENT_LOADERS.get(spec.loader_type)
    if loader is None:
        reason = f"no resident loader for loader_type {spec.loader_type!r} ({key!r})"
        logger.warning("model_garden.get_model: %s → None", reason)
        _ABSENCE_REASON[key] = reason
        _MODEL_CACHE[key] = None
        return None

    try:
        model = loader(spec)
    except Exception as e:  # graceful-absent: a load failure degrades, never raises.
        reason = f"load failed for {key!r}: {type(e).__name__}: {e}"
        logger.warning("model_garden.get_model: %s → None (graceful-absent)", reason)
        _ABSENCE_REASON[key] = reason
        _MODEL_CACHE[key] = None
        return None

    if model is None:
        # A delegate loader (e.g. reranker) may itself return None on failure.
        reason = f"delegate loader returned None for {key!r}"
        logger.info("model_garden.get_model: %s", reason)
        _ABSENCE_REASON[key] = reason
        _MODEL_CACHE[key] = None
        return None

    _MODEL_CACHE[key] = model
    _ABSENCE_REASON.pop(key, None)
    return model


def absence_reason(key: str) -> Optional[str]:
    """Why is `key` currently None? Returns the last logged reason, or None if the
    model is loaded / never attempted. For doctor + caller diagnostics."""
    return _ABSENCE_REASON.get(key)


# ============================================================
# get_burst_model — the gpu_guard HOOK (BURST models only).
# ============================================================

def get_burst_model(
    key: str,
    profile: Any,
    *,
    allow_dual: bool = True,
    force: bool = False,
    manifest_path: Optional[str] = None,
    _gpu_guard: Any = None,
) -> Tuple[Optional[Any], Any]:
    """Acquire a GPU-BURST model through the gatekeeper. THE HOOK for M0b.

    Contract:
      1. Import the live gpu_guard (the SINGLE sanctioned entry to the 2nd card).
         model_garden touches NOTHING of gpu_guard beyond acquire/release/
         load_policy/BurstProfile — it never reimplements the gatekeeper.
      2. Call gpu_guard.acquire(profile, allow_dual, force, policy) FIRST. The
         policy comes from the manifest's gpu_guard_policy block (so the
         display_uuid PRIMARY pin is honored).
      3. Return (model | None, verdict). The actual M0b LLM LOAD is DEFERRED:
         even on a GRANTED verdict we return (None, verdict) for now — the
         registered burst entry has no provisioned weights yet. The PATH (acquire
         → device_plan → load-or-defer → release) is wired + proven; the heavy
         model is pulled at L5-C1.

    GRACEFUL EVERYWHERE: a missing gpu_guard, a non-burst key, a missing
    BurstProfile type, or any internal error returns (None, <a refusal verdict-
    like object or None>) and NEVER raises into the caller.

    `_gpu_guard` is a TEST SEAM: inject a mock module exposing acquire/release so
    the hook is provable with no GPU. Production: None → import the live module.
    """
    spec = MODEL_REGISTRY.get(key)
    if spec is None or spec.role != ROLE_BURST:
        logger.warning("get_burst_model: %r is not a registered BURST model → (None, None)", key)
        return None, None

    # Import the gatekeeper (test seam wins if provided).
    gg = _gpu_guard
    if gg is None:
        try:
            gg = importlib.import_module("gpu_guard")
        except Exception as e:
            logger.warning("get_burst_model: gpu_guard import failed (%s) → (None, None) "
                           "(graceful: no gatekeeper, no burst)", e)
            return None, None

    # Resolve the policy from the manifest so the display_uuid pin is honored.
    policy = None
    try:
        if hasattr(gg, "load_policy"):
            policy = gg.load_policy(manifest_path or _default_manifest_path())
    except Exception:
        policy = None  # gpu_guard.load_policy is itself fail-graceful → DEFAULT_POLICY.

    # Ask the gatekeeper. acquire() is the SINGLE sanctioned entry to the 2nd card.
    try:
        verdict = gg.acquire(profile, allow_dual=allow_dual, force=force, policy=policy)
    except Exception as e:
        logger.warning("get_burst_model: gpu_guard.acquire raised (%s) → (None, None)", e)
        return None, None

    granted = bool(getattr(verdict, "granted", False))
    plan = getattr(verdict, "device_plan", "?")
    logger.info("get_burst_model(%s): gpu_guard verdict granted=%s plan=%s", key, granted, plan)

    if not granted:
        # Degraded to single / cloud / defer — no dual-card model. Return the
        # verdict so the caller can route to cuda:0-single or cloud-claude-p.
        return None, verdict

    # GRANTED. The actual M0b weights are not provisioned yet → DEFER the load,
    # release the burst lock immediately (we are not holding the 2nd card), and
    # return (None, verdict) so the caller sees the grant but no model.
    try:
        if hasattr(gg, "release"):
            gg.release(verdict)
    except Exception:
        pass
    logger.info("get_burst_model(%s): GRANTED but M0b LLM load is DEFERRED → "
                "released lock, returning (None, verdict)", key)
    return None, verdict


# ============================================================
# doctor — probe every registry entry (the `probed` block).
# ============================================================

def doctor(manifest: Optional[dict] = None, *, smoke: bool = False) -> Dict[str, Any]:
    """Probe every registry entry: package present? cache present? enabled? and
    (optionally) a tiny smoke-load. Returns the `probed` block the manifest
    writer embeds. NEVER raises; NEVER downloads.

    `smoke=False` (default) is the cheap probe (no model load — safe to run
    anywhere). `smoke=True` additionally attempts a get_model() of each
    cache-present resident model (pulls VRAM) and records load success/failure.
    The installer runs smoke=False at write time; tests run targeted smoke loads.
    """
    out: Dict[str, Any] = {
        "_generated_by": "model_garden.doctor",
        "torch_cuda": _probe_torch_cuda(),
        "models": {},
    }
    for key, spec in MODEL_REGISTRY.items():
        enabled, ereason = _is_enabled(key, manifest)
        missing = _missing_packages(spec)
        cache = _cache_present(spec)
        entry: Dict[str, Any] = {
            "repo_id": spec.repo_id,
            "role": spec.role,
            "loader_type": spec.loader_type,
            "device": spec.device,
            "footprint_mb": spec.footprint_mb,
            "enabled": enabled,
            "enable_reason": ereason,
            "packages_present": (len(missing) == 0),
            "missing_packages": missing,
            "cache_present": cache,
            # available = the non-loading is_available verdict.
            "available": (enabled and not missing and cache),
        }
        if smoke and entry["available"] and spec.role == ROLE_RESIDENT:
            model = get_model(key, manifest=manifest)
            entry["smoke_loaded"] = model is not None
            if model is None:
                entry["smoke_reason"] = absence_reason(key)
        out["models"][key] = entry
    return out


# The CUDA probe runs in a CHILD process (env-file writer pass, iteration 4, carried item O2). MEASURED on the reference
# host (WSL2, driver 610.74): the driver's /usr/lib/wsl/lib/libcuda.so.1 SEGFAULTS inside cuInit(0) whenever
# CUDA_VISIBLE_DEVICES leaves no device visible -- empty, ' ', '-1', 'NoDevFiles', an index the host lacks -- through
# ctypes with no torch at all, and through torch.cuda.is_available() (cudaGetDeviceCount -> cuInit). Called in-process
# here, it killed `voidloom_manifest.py write` with rc 139 before it wrote a byte -- install.sh Step A.1 on a CPU-only
# user's normal setting (CUDA_VISIBLE_DEVICES=""). Now: an EMPTY (or blank) CUDA_VISIBLE_DEVICES is CUDA's own "no
# device" and is recorded as such without initialising CUDA at all; any other value is probed by a child of the same
# interpreter, whose death by a signal is recorded as "no CUDA" in words -- never a crash of this process, never a guess.
_CUDA_PROBE = (
    "import json, torch\n"
    "d = {'cuda_available': bool(torch.cuda.is_available())}\n"
    "if d['cuda_available']:\n"
    "    d['device_count'] = torch.cuda.device_count()\n"
    "    try:\n"
    "        d['device_0_name'] = torch.cuda.get_device_name(0)\n"
    "    except Exception:\n"
    "        pass\n"
    "print('CUDA_PROBE ' + json.dumps(d))\n"
)
_CUDA_PROBE_TIMEOUT_S = 180


def _probe_torch_cuda() -> Dict[str, Any]:
    """Report torch CUDA visibility (fail-graceful). NEVER initialises CUDA in this process (see _CUDA_PROBE)."""
    info: Dict[str, Any] = {"torch_present": False, "cuda_available": False, "device_count": 0}
    try:
        import torch  # importing torch does not initialise CUDA (measured: the fault is at cuInit, after the import)
        info["torch_present"] = True
        info["torch_version"] = torch.__version__
    except Exception:
        return info
    cvd = os.environ.get("CUDA_VISIBLE_DEVICES")
    if cvd is not None and cvd.strip() == "":
        info["cuda_probe"] = ("not probed: CUDA_VISIBLE_DEVICES is empty -- CUDA's own 'no device'; no GPU visible "
                              "to this process")
        return info
    import subprocess
    import sys
    try:
        p = subprocess.run([sys.executable, "-c", _CUDA_PROBE], capture_output=True, text=True,
                           timeout=_CUDA_PROBE_TIMEOUT_S, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    except Exception as e:  # noqa: BLE001 - a probe that cannot run is recorded, never raised
        info["cuda_probe"] = "the CUDA probe could not run (%s): no CUDA recorded" % type(e).__name__
        return info
    if p.returncode < 0:
        info["cuda_probe"] = ("the CUDA probe died with signal %d initialising CUDA (CUDA_VISIBLE_DEVICES=%r): no CUDA "
                              "recorded" % (-p.returncode, cvd))
        return info
    line = [ln for ln in p.stdout.splitlines() if ln.startswith("CUDA_PROBE ")]
    try:
        got = json.loads(line[-1][len("CUDA_PROBE "):])
    except Exception:  # noqa: BLE001
        info["cuda_probe"] = "the CUDA probe exited %d without a result: no CUDA recorded" % p.returncode
        return info
    info["cuda_available"] = bool(got.get("cuda_available"))
    if info["cuda_available"]:
        info["device_count"] = int(got.get("device_count", 0))
        if "device_0_name" in got:
            info["device_0_name"] = got["device_0_name"]
    return info


def doctor_table(probed: Optional[Dict[str, Any]] = None, manifest: Optional[dict] = None) -> str:
    """Render doctor() output as a readable present/absent table (for CLI / report)."""
    probed = probed or doctor(manifest)
    cuda = probed.get("torch_cuda", {})
    lines = []
    lines.append("=" * 78)
    lines.append("model_garden.doctor — per-model present/absent against the live machine")
    lines.append("=" * 78)
    lines.append(
        f"torch={cuda.get('torch_version','?')} cuda_available={cuda.get('cuda_available')} "
        f"device_count={cuda.get('device_count')} "
        f"device0={cuda.get('device_0_name','-')}"
    )
    lines.append("-" * 78)
    lines.append(f"{'key':<10} {'role':<18} {'pkg':<5} {'cache':<6} {'enab':<5} {'AVAIL':<6} repo_id")
    lines.append("-" * 78)
    for key, e in probed.get("models", {}).items():
        lines.append(
            f"{key:<10} {e['role']:<18} "
            f"{'OK' if e['packages_present'] else 'NO':<5} "
            f"{'yes' if e['cache_present'] else 'no':<6} "
            f"{'yes' if e['enabled'] else 'no':<5} "
            f"{'YES' if e['available'] else '-':<6} "
            f"{e['repo_id']}"
        )
    lines.append("=" * 78)
    return "\n".join(lines)


# ============================================================
# CLI — `python3 model_garden.py doctor` (print-only diagnostic).
# ============================================================

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="model_garden — Voidloom unified model-access layer.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("doctor", help="Print the per-model present/absent table (no model load).")
    sub.add_parser("registry", help="Print the model registry as JSON.")
    args = parser.parse_args(argv)

    if args.cmd == "doctor":
        manifest = load_manifest()
        print(doctor_table(manifest=manifest))
        return 0
    if args.cmd == "registry":
        reg = {k: {**v.__dict__} for k, v in MODEL_REGISTRY.items()}
        for v in reg.values():
            v["requires_packages"] = list(v["requires_packages"])
        print(json.dumps(reg, indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
