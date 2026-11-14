#!/usr/bin/env python3
"""Embedding Upgrade: Nomic v1.5 (768-dim) -> Jina v5-text-small (1024-dim).

Wizard Library Level 3 Component 1.

THE ONE LOADER (owner decisions D1 + D2, 2026-10-19)
    The shipped tiers -- nomic-768 (cpu) and Qwen3-Embedding-0.6B (gpu), both
    apache-2.0 -- load through ONE function, `get_locked_embedder(lock)`: the
    install lock beside the brain (or, for a brain that predates the lock, the
    catalogue entry of the tier the brain already has -- `brain_lock()`) names
    the model, its revision and the checksum of its bytes; the bytes on disk are
    hashed and compared BEFORE anything is instantiated, the model runs on the
    device the compute-agnostic layer picks, and the checksum it computed is
    what this process's provenance stamps carry. The jina tier (cc-by-nc-4.0)
    is not in the shipped catalogue: it keeps its own revision-pinned loader for
    a personal, non-commercial install that asked for it by name, and a brain
    with an install lock never reaches it.

    `swap` is REFUSED (typed, exit 1): it re-pointed an install at another table
    without re-embedding a single memory.

This module handles the full lifecycle of upgrading the embedding model:
  1. Download + verify Jina v5 model
  2. Apply migration (create memory_embeddings_v2)
  3. Re-embed all memories in batches
  4. A/B comparison against Nomic baseline
  5. Swap default -- REFUSED since the install lock (see above)
  6. Stats + diagnostics

Usage (standalone):
    python3 -u embedding_upgrade.py download     # Pre-fetch model to HF cache
    python3 -u embedding_upgrade.py migrate      # Apply migration SQL
    python3 -u embedding_upgrade.py re-embed     # Re-embed all memories
    python3 -u embedding_upgrade.py ab-compare   # Run 50-query A/B comparison
    python3 -u embedding_upgrade.py swap         # REFUSED, typed, exit 1
    python3 -u embedding_upgrade.py stats        # Show embedding table stats
    python3 -u embedding_upgrade.py full         # All steps in sequence

Design references:
  - Phase 0 memory #4021 (Jina v5 decision)
  - Memory #3911 (hybrid retrieval canonical design)
  - Memory #3812 (RRF k=15 for small corpora)

Production guardrails:
  - NEVER modifies production DB directly (uses --db-path flag)
  - Default db_path: /tmp/claude_memory_embed_test.db (test copy)
  - Production path requires explicit --production flag
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import struct
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# GPU pin. #15708 FORBIDS os.environ.setdefault("CUDA_VISIBLE_DEVICES", ...):
# setdefault yields to an existing OR EMPTY value, so an exported-but-empty
# variable silently wins and the job lands on GPU 0 -- the card driving Drew's
# monitor, which a 10.7 GB model does not share but EVICTS. voidloom_device
# resolves the fork properly: the card the env file declares (from ~/.voidloom/
# wizard-library-env), 0 everywhere else, and it overrides an empty value.
# Verified on all three cases before landing.
try:
    from voidloom_device import apply_cuda_pin as _apply_cuda_pin
except ImportError:                         # standalone / package without the module
    import sys as _s, os as _o
    _s.path.insert(0, _o.path.dirname(_o.path.abspath(__file__)))
    try:
        from voidloom_device import apply_cuda_pin as _apply_cuda_pin
    except ImportError:
        _apply_cuda_pin = None
if _apply_cuda_pin is not None:
    _apply_cuda_pin()
else:                                        # last resort: FORCED, never setdefault
    os.environ["CUDA_VISIBLE_DEVICES"] = os.environ.get("WIZARD_CUDA_DEVICE") or "0"

# --- H3 quiet-by-default ------------------------------------------------------
# This module is BOTH a standalone CLI (download/migrate/re-embed/ab-compare,
# where INFO breadcrumbs are wanted) AND an import target on the recall hot path
# (claude_memory.py imports encode_jina_* from here). The old module-level
# `logging.basicConfig(level=INFO)` attached a StreamHandler to the ROOT logger
# at import time, so every `voidloom recall` that touched the jina tier spilled
# model-load INFO to stderr — defeating the engine's quiet-by-default contract.
# Fix: do NOT touch root at import. Parent this module's logger under "voidloom"
# so it inherits the engine's quiet-by-default level (WARNING+, no handler =>
# silent) plus the VOIDLOOM_LOG_LEVEL / VOIDLOOM_LOG_FILE opt-ins. The standalone
# CLI re-enables INFO via basicConfig inside main() only.
logger = logging.getLogger("voidloom.embedding_upgrade")


def _device_api():
    """organs/voidloom_device, imported the way every organ here imports it."""
    try:
        import voidloom_device as _vd
    except ImportError:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import voidloom_device as _vd
    return _vd


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


def _lock_module():
    """organs/embedder_lock (stdlib-only), imported the way _device_api imports its module."""
    try:
        import embedder_lock as _el
    except ImportError:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import embedder_lock as _el
    return _el


def _hf_offline_ok(repo_id: str) -> bool:
    """H11: decide whether to load a HF model with local_files_only=True.

    Honor an explicit offline request (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE),
    else auto-enable offline only when the model is already in the local HF
    cache (a warm cache => no hub round-trip / no "Fetching 8 files" line). A
    cold machine (no cache) must still be allowed to download, so we return
    False there and let the first call reach the hub.
    """
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        v = os.environ.get(var)
        if v is not None and v.strip().lower() in ("1", "true", "yes", "on"):
            return True
    try:
        # The SAME cache resolver the lock verifier uses (HF_HUB_CACHE >
        # HUGGINGFACE_HUB_CACHE > HF_HOME/hub > ~/.cache/huggingface/hub). The old
        # chain ignored HF_HUB_CACHE and read HUGGINGFACE_HUB_CACHE as a root, so it
        # could answer about a cache the loader would never read.
        embedder_lock = _lock_module()
        hub = embedder_lock.hf_hub_cache()
        marker = "models--" + repo_id.replace("/", "--")
        cached = hub / marker / "snapshots"
        if cached.exists() and any(cached.iterdir()):
            return True
    except Exception:
        pass  # graceful: on any doubt, fall through to online (allow download)
    return False


def _quiet_model_load_noise() -> None:
    """H3: keep a successful load quiet on default stderr.

    The SentenceTransformers / transformers stack emits, via ``logger.warning``,
    a version-skew line ("You are trying to use a model that was created with
    Sentence Transformers version 5.1.2, but you're currently using version
    4.1.0…") plus INFO chatter during load. Because the version-skew line is a
    *logging* WARNING (not a ``warnings.warn``), suppressing it requires raising
    those loggers ABOVE warning. On the quiet recall path (effective level >
    INFO) we lift them to ERROR so their INFO + WARNING vanish while a genuine
    ERROR still surfaces; we also filter the ``warnings.warn`` variants
    (torch_dtype deprecation, any UserWarning-phrased version skew). When the
    operator opts into INFO/DEBUG via VOIDLOOM_LOG_LEVEL we leave the breadcrumbs
    visible.
    """
    eff = logger.getEffectiveLevel()
    quiet = eff > logging.INFO
    target = max(eff, logging.ERROR) if quiet else eff
    for name in ("sentence_transformers", "transformers"):
        try:
            logging.getLogger(name).setLevel(target)
        except Exception:
            pass
    if quiet:
        warnings.filterwarnings(
            "ignore", message=r".*[Cc]reated with [Ss]entence Transformers version.*"
        )
        warnings.filterwarnings(
            "ignore", message=r".*sentence-transformers.*version.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore", message=r".*torch_dtype.*deprecated.*"
        )
        # H3: huggingface_hub prints its own "Fetching N files" tqdm bar to
        # stderr during snapshot resolution (separate from the ST loggers above).
        # The env var only sets the import-time default, so also disable at
        # runtime (idempotent); an INFO/DEBUG opt-in leaves it visible.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        try:
            from huggingface_hub.utils import disable_progress_bars
            disable_progress_bars()
        except Exception:
            pass

# ============================================================
# Constants
# ============================================================

JINA_MODEL_NAME = "jinaai/jina-embeddings-v5-text-small"
JINA_DIM = 1024
QWEN_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"
QWEN_DIM = 1024
NOMIC_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
NOMIC_DIM = 768

# Voidloom #176 (RCE/iter2): pin each embedder to a verified HuggingFace commit
# SHA. Both models carry the HF `custom_code` tag and load via the remote-code
# path, so trust_remote_code=True is REQUIRED (trust_remote_code=False makes
# sentence-transformers raise). Pinning `revision` to a fixed commit makes the
# remote code tree (modeling_*.py) IMMUTABLE: a moved/replaced `main` tag can no
# longer swap the model's code for an attacker payload that would then run
# in-process on EVERY store/recall (the RCE Thessaly named). The residual -- the
# pinned, auditable remote code still executes -- is documented in the seal.
# SHAs verified 2026-06-11 against HF /api/models/<repo>/refs AND /commits/main
# (both endpoints returned the identical head SHA per model).
JINA_REVISION = "dd76d535f5447ca3897a9c893fb1e612ead98192"
NOMIC_REVISION = "e9b6763023c676ca8431644204f50c2b100d9aab"
# Qwen3-Embedding-0.6B is Apache-2.0 and loads WITHOUT remote code, so the pin here
# is for reproducibility, not RCE: an unpinned `main` could change the weights
# between two benchmark runs. Verified 2026-10-27 (refs and commits/main agree).
QWEN_REVISION = "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"

# Jina v5 LoRA adapter tasks (from adapters/ directory)
# What a whole snapshot holds at that pin: the files the load reads. An interrupted download leaves the directory and
# some of these, and a blob the hub has not finished is not linked in.
JINA_SNAPSHOT_FILES = ("config.json", "modules.json", "tokenizer.json", "model.safetensors", "custom_st.py",
                       "configuration_jina_embeddings_v5.py", "modeling_jina_embeddings_v5.py",
                       "adapters/retrieval/adapter_config.json", "adapters/retrieval/adapter_model.safetensors")
JINA_TASK_RETRIEVAL = "retrieval"
JINA_TASK_MATCHING = "text-matching"
JINA_TASK_CLUSTERING = "clustering"
JINA_TASK_CLASSIFICATION = "classification"

# Sentence-transformers prompt names (from config_sentence_transformers.json)
JINA_PROMPT_QUERY = "query"      # Prepends "Query: "
JINA_PROMPT_DOCUMENT = "document"  # Prepends "Document: "

# Batch size for re-embedding. 16 is safe for 2080 Ti (11GB) with
# Jina v5 (~1.3GB VRAM). Some memories are 2000+ chars which expand to
# many tokens — batch_size=64 causes OOM on these. 16 provides headroom.
BATCH_SIZE = 16

# Test DB default path
DEFAULT_TEST_DB = "/tmp/claude_memory_embed_test.db"
PRODUCTION_DB = str(Path(__file__).resolve().parent.parent / "claude_memory.db")
MIGRATION_SQL = Path(__file__).parent.parent / "migrations" / "migration_013_embedding_upgrade.sql"

# Eval queries path (from L2-C1). Env-overridable; the package does not ship
# eval/queries.jsonl, and the previous code fell back to a literal naming an
# ACE workspace on this author's machine.
EVAL_QUERIES_PATH = Path(
    os.environ.get("VOIDLOOM_EVAL_QUERIES")
    or Path(__file__).parent.parent / "eval" / "queries.jsonl"
)

# ============================================================
# Model cache (process-level singleton)
# ============================================================

_jina_model_cache: Dict[str, Any] = {}


def _get_jina_model():
    """Load Jina v5 model with retrieval adapter as default.

    Uses sentence-transformers for consistent API with existing codebase.
    The model auto-loads LoRA adapters based on the `task` kwarg in encode().
    """
    if "model" in _jina_model_cache:
        return _jina_model_cache["model"]

    import torch
    from sentence_transformers import SentenceTransformer

    _quiet_model_load_noise()  # H3: keep load INFO + ST version warning off default stderr
    device = _torch_device(torch)
    logger.info("Loading Jina v5 model on %s...", device)
    t0 = time.time()

    # H11 offline: on a warm cache prefer local files (no HF round-trip /
    # "Fetching N files"); honor HF_HUB_OFFLINE explicitly. A cold machine still
    # downloads (first call reaches the hub).
    offline = _hf_offline_ok(JINA_MODEL_NAME)
    import contextlib
    import embedder_lock as _el
    try:
        with contextlib.redirect_stdout(sys.stderr):  # remote code print()s; keep stdout clean
            model = SentenceTransformer(
                JINA_MODEL_NAME,
                revision=JINA_REVISION,  # Voidloom #176 RCE: pin code tree (moved tag can't swap it)
                trust_remote_code=True,
                device=device,
                local_files_only=offline,
                model_kwargs={"default_task": JINA_TASK_RETRIEVAL},
            )
    except OSError as e:
        if not _el.hf_offline():
            raise
        fix = _el.fetch_command()
        raise _el.EmbedderLockError(
            "SNAPSHOT_ABSENT", f"{JINA_MODEL_NAME}@{JINA_REVISION[:12]} cannot load from {_el.hf_hub_cache()} ({e}). "
            f"Fetch it, with the network: `{fix}`", retry=[fix], model_id=JINA_MODEL_NAME, revision=JINA_REVISION)

    elapsed = time.time() - t0
    logger.info("Jina v5 loaded in %.1fs (device=%s)", elapsed, device)
    _jina_model_cache["model"] = model
    return model


def jina_snapshot_whole() -> bool:
    """Is every file the load reads on this disk, at the engine's pin? A directory alone is not a model."""
    import embedder_lock as _el
    return _el.snapshot_complete(_el.snapshot_dir_for(JINA_MODEL_NAME, JINA_REVISION), JINA_SNAPSHOT_FILES)


def fetch_jina_at_pin() -> str:
    """The jina snapshot, on this disk at the engine's pin, loadable offline. Its remote code fetches its adapters with
    no revision, which offline means `refs/main`; a fetch by commit writes no such ref, so one is written, naming the pin.
    A ref already there is the hub's and is left. Loads nothing: the model's code does not run here."""
    import embedder_lock as _el
    if not jina_snapshot_whole():
        from huggingface_hub import snapshot_download
        snapshot_download(JINA_MODEL_NAME, revision=JINA_REVISION, **_el.fetch_options())
    refs = _el.hf_hub_cache() / ("models--" + JINA_MODEL_NAME.replace("/", "--")) / "refs"
    if not (refs / "main").exists():
        refs.mkdir(parents=True, exist_ok=True)
        (refs / "main").write_text(JINA_REVISION, encoding="utf-8")
    return JINA_REVISION


# ============================================================
# Encoding functions
# ============================================================

def serialize_f32(vec) -> bytes:
    """Serialize a float32 vector to bytes for sqlite-vec storage."""
    return struct.pack(f"{len(vec)}f", *vec)


def encode_jina(texts: List[str], task: str = JINA_TASK_RETRIEVAL,
                prompt_name: str = JINA_PROMPT_DOCUMENT) -> List[bytes]:
    """Encode texts using Jina v5 with specified task adapter.

    Args:
        texts: list of text strings to encode
        task: LoRA adapter to use (retrieval, text-matching, clustering, classification)
        prompt_name: sentence-transformers prompt name ('query' or 'document')

    Returns:
        List of serialized float32 vectors (1024-dim each)
    """
    import numpy as np

    model = _get_jina_model()
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        task=task,
        prompt_name=prompt_name,
        batch_size=min(len(texts), BATCH_SIZE),
        show_progress_bar=False,
    )
    return [
        serialize_f32(emb[:JINA_DIM].astype(np.float32).tolist())
        for emb in embeddings
    ]


def encode_jina_query(text: str) -> bytes:
    """Encode a single query text for retrieval (query-side encoding)."""
    results = encode_jina([text], task=JINA_TASK_RETRIEVAL, prompt_name=JINA_PROMPT_QUERY)
    return results[0]


def encode_jina_document(text: str) -> bytes:
    """Encode a single document text for storage (document-side encoding)."""
    results = encode_jina([text], task=JINA_TASK_RETRIEVAL, prompt_name=JINA_PROMPT_DOCUMENT)
    return results[0]


# ============================================================
# THE ONE LOADER for the shipped tiers (owner decision D1, 2026-10-19)
#
# The device layer decides WHERE a model runs (_torch_device, the compute-agnostic
# resolver). The install lock and the shipped catalogue (organs/embedders.lock.json)
# decide WHICH BYTES run. One loader holds both halves:
#
#   1. the caller passes the lock it resolved for ITS OWN database (brain_lock());
#   2. embedder_lock.verify_snapshot_against_lock(lock) locates the snapshot the
#      lock's (model_id, revision) name -- and the remote-code repository its
#      `code` pin names -- hashes every listed file, and compares the result to
#      lock.weights_hash BEFORE anything is instantiated (WEIGHTS_MISMATCH,
#      LOCK_NOT_SHIPPED and SNAPSHOT_ABSENT refuse, typed). An absent snapshot is
#      fetched only at the catalogue's pin and only when the process may go online
#      (HF_HUB_OFFLINE=1 refuses instead): the old per-model loaders' "prefer local
#      files, honour an explicit offline request" lives here now;
#   3. the model is instantiated with local_files_only=True on the verified bytes;
#   4. embedder_lock.register_loaded(... weights_hash=<the COMPUTED value>), the value
#      every provenance stamp this process writes carries.
#
# One model resident per process; a lock with another hash under a running process
# is refused. The non-commercial jina tier is not in the catalogue and never comes
# through here (owner decision D2): it keeps its own revision-pinned loader above,
# for a personal install that asked for it by name.
# ============================================================

_model_cache: Dict[str, Any] = {}      # {"model": SentenceTransformer, "lock": dict}

# Engine tier keys (claude_memory.EMBED_TIERS) -> the catalogue's tier names.
_TIER_KEY_TO_CATALOGUE = {"nomic": "cpu", "qwen": "gpu"}


def brain_lock(target, *, tier: str) -> Dict[str, Any]:
    """The lock the brain at `target` (a connection, a database path or a data
    dir) embeds under -- owner decision D2: the brain's lock is read FIRST.

    A brain that predates the lock (no embedder.lock.json beside its database:
    every brain installed before it existed) gets the catalogue entry for the tier
    the engine already resolved for it (`tier`, the engine's key, read by the
    caller from the DB). The bytes are verified against that shipped entry exactly
    as against a written lock. Every other lock defect (LOCK_MALFORMED, ...) still
    refuses -- only ABSENCE falls back -- and a tier the catalogue does not ship
    (jina, or a typo) is LOCK_NOT_SHIPPED, never another model."""
    el = _lock_module()
    try:
        return el.resolve(target)
    except el.EmbedderLockError as e:
        if e.code != "LOCK_MISSING" or (getattr(e, "detail", None) or {}).get("was_locked"):
            raise
    cat_tier = _TIER_KEY_TO_CATALOGUE.get((tier or "").strip().lower())
    entry = el.catalogue_tiers().get(cat_tier) if cat_tier else None
    if entry is None:
        raise el.EmbedderLockError(
            "LOCK_NOT_SHIPPED",
            f"no install lock beside {target!r}, and the tier {tier!r} is not in the shipped "
            f"catalogue ({sorted(_TIER_KEY_TO_CATALOGUE)}); refusing to load anything else",
            tier=tier)
    lock = dict(entry)
    lock["tier"] = cat_tier
    lock["_source"] = "catalogue (pre-lock brain: no install lock beside its database)"
    return lock


def get_locked_embedder(lock: Dict[str, Any]):
    """The embedder `lock` names, loaded once per process, checksum-verified BEFORE
    instantiation and registered with the COMPUTED checksum. Returns (model, lock).

    `lock` is REQUIRED: there is no lock-less form and no package-root default."""
    embedder_lock = _lock_module()
    if not isinstance(lock, dict) or "weights_hash" not in lock:
        raise embedder_lock.EmbedderLockError(
            "LOCK_MISSING",
            "get_locked_embedder needs the lock the caller resolved for its own database "
            "(brain_lock); there is no default lock")
    if "model" in _model_cache:
        held = _model_cache["lock"]
        if held["weights_hash"] != lock["weights_hash"]:
            raise embedder_lock.EmbedderLockError(
                "WEIGHTS_MISMATCH",
                f"this process already holds {held['model_id']} ({held['weights_hash'][:12]}…) "
                f"and the lock now asks for {lock['model_id']} ({lock['weights_hash'][:12]}…); "
                "one model per process, and a lock does not change under a running process",
                resident=held["weights_hash"], requested=lock["weights_hash"])
        return _model_cache["model"], lock

    snapshot, computed = embedder_lock.verify_snapshot_against_lock(lock)   # refuses, typed

    import torch
    from sentence_transformers import SentenceTransformer
    _quiet_model_load_noise()
    device = _torch_device(torch)
    logger.info("Loading %s@%s on %s (weights %s…)", lock["model_id"], lock["revision"][:12],
                device, computed[:12])
    t0 = time.time()
    style = _encode_style(lock["model_id"])
    if style == "qwen":
        # Qwen3-Embedding loads without remote code, so loading it BY NAME at the
        # pinned revision with local_files_only reads exactly the snapshot just
        # hashed. The revision constant is public main's reproducibility pin; the
        # catalogue must agree with it, or this is a packaging defect, refused.
        if lock["model_id"] != QWEN_MODEL_NAME or lock["revision"] != QWEN_REVISION:
            raise embedder_lock.EmbedderLockError(
                "WEIGHTS_MISMATCH",
                f"the lock names {lock['model_id']}@{lock['revision'][:12]} but this engine pins "
                f"{QWEN_MODEL_NAME}@{QWEN_REVISION[:12]}; the catalogue and the engine disagree",
                requested=lock["revision"], pinned=QWEN_REVISION)
        model = SentenceTransformer(
            QWEN_MODEL_NAME,
            revision=QWEN_REVISION,
            device=device,
            local_files_only=True,
        )
    else:
        kwargs: Dict[str, Any] = {
            "device": device,
            "local_files_only": True,       # verify_snapshot_against_lock just proved the bytes are here
        }
        if style == "nomic":
            kwargs["trust_remote_code"] = True   # carries the HF custom_code tag
        code = lock.get("code")
        if code:
            # The remote-code repository the model's auto_map names, pinned to the
            # commit whose bytes were just hashed into weights_hash; without it the
            # code tree would follow that repository's `main`.
            kwargs["model_kwargs"] = {"code_revision": code["revision"]}
            kwargs["config_kwargs"] = {"code_revision": code["revision"]}
        # FROM THE VERIFIED SNAPSHOT DIRECTORY, never by repo name: the remote code
        # nomic ships resolves its weights BY NAME at `main` when handed a repo id,
        # and a cache filled at the pin carries no `refs/main` -- an offline load of
        # exactly the bytes the lock names failed that way. Handed the directory, it
        # reads the weights from the directory: the bytes hashed are the bytes run.
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):  # remote code print()s; keep stdout clean
            model = SentenceTransformer(str(snapshot), **kwargs)
    embedder_lock.register_loaded(model_id=lock["model_id"], revision=lock["revision"],
                                  weights_hash=computed, dim=int(lock["dim"]),
                                  vec_table=lock["vec_table"], tier=lock.get("tier"),
                                  snapshot=str(snapshot))
    logger.info("%s loaded in %.1fs (device=%s, weights %s…)", lock["model_id"], time.time() - t0,
                device, computed[:12])
    _model_cache["model"] = model
    _model_cache["lock"] = dict(lock)
    return model, lock


def _encode_style(model_id: str) -> str:
    """How a model wants its text framed. Keyed on the hub id the LOCK names."""
    mid = (model_id or "").lower()
    if mid.startswith("nomic-ai/"):
        return "nomic"
    if mid.startswith("qwen/"):
        return "qwen"
    return "plain"


def encode_locked(text: str, *, is_query: bool, lock: Dict[str, Any]) -> bytes:
    """Encode ONE text with the locked embedder, framed the way that model wants
    (nomic: a task prefix; Qwen3-Embedding is ASYMMETRIC: prompt_name='query' on the
    query side, none on documents -- older sentence-transformers builds lack the
    prompt, and a plain encode is slightly worse retrieval where a crash is an
    outage). Width is the lock's dim."""
    import numpy as np
    model, lock = get_locked_embedder(lock)
    dim = int(lock["dim"])
    style = _encode_style(lock["model_id"])
    if style == "nomic":
        vec = model.encode(f"{'search_query' if is_query else 'search_document'}: {text}",
                           normalize_embeddings=True)
    elif style == "qwen" and is_query:
        try:
            vec = model.encode(text, prompt_name="query", normalize_embeddings=True)
        except (TypeError, ValueError, KeyError):
            vec = model.encode(text, normalize_embeddings=True)
    else:
        vec = model.encode(text, normalize_embeddings=True)
    return serialize_f32(np.asarray(vec)[:dim].astype(np.float32).tolist())


def encode_locked_batch(texts: List[str], *, is_query: bool = False,
                        lock: Dict[str, Any]) -> List[bytes]:
    """Batch form of encode_locked, for the re-embed path."""
    import numpy as np
    model, lock = get_locked_embedder(lock)
    dim = int(lock["dim"])
    style = _encode_style(lock["model_id"])
    kw: Dict[str, Any] = {"normalize_embeddings": True, "show_progress_bar": False,
                          "batch_size": min(max(len(texts), 1), BATCH_SIZE)}
    if style == "nomic":
        prefix = "search_query" if is_query else "search_document"
        embs = model.encode([f"{prefix}: {t}" for t in texts], **kw)
    elif style == "qwen" and is_query:
        try:
            embs = model.encode(texts, prompt_name="query", **kw)
        except (TypeError, ValueError, KeyError):
            embs = model.encode(texts, **kw)
    else:
        embs = model.encode(texts, **kw)
    return [serialize_f32(np.asarray(e)[:dim].astype(np.float32).tolist()) for e in embs]


def _model_for(model_id: str, lock: Dict[str, Any]):
    """A per-tier entry point's way in: the locked embedder IF the lock the caller
    resolved names this model; otherwise a typed refusal, never another model."""
    embedder_lock = _lock_module()
    model, lock = get_locked_embedder(lock)
    if lock["model_id"] != model_id:
        raise embedder_lock.EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"{model_id} was requested but the lock names {lock['model_id']} "
            f"({lock['weights_hash'][:12]}…); this install embeds with nothing else",
            requested=model_id, locked=lock["model_id"])
    return model


def _get_nomic_model(lock: Dict[str, Any]):
    return _model_for(NOMIC_MODEL_NAME, lock)


def _get_qwen_model(lock: Dict[str, Any]):
    return _model_for(QWEN_MODEL_NAME, lock)


def encode_qwen_query(text: str, *, lock: Dict[str, Any]) -> bytes:
    _model_for(QWEN_MODEL_NAME, lock)
    return encode_locked(text, is_query=True, lock=lock)


def encode_qwen_document(text: str, *, lock: Dict[str, Any]) -> bytes:
    _model_for(QWEN_MODEL_NAME, lock)
    return encode_locked(text, is_query=False, lock=lock)


def encode_nomic(text: str, prefix: str = "search_document", *, lock: Dict[str, Any]) -> bytes:
    """Encode using Nomic v1.5 under the lock. Matches the engine's _encode() API."""
    _model_for(NOMIC_MODEL_NAME, lock)
    return encode_locked(text, is_query=(prefix == "search_query"), lock=lock)


class VerbRefused(RuntimeError):
    """A typed refusal from a verb of this module. `.code` names it; `.as_dict()` is
    the envelope; main() prints the envelope and EXITS 1."""

    def __init__(self, code: str, message: str, **detail: Any):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.detail = dict(detail)

    def as_dict(self) -> Dict[str, Any]:
        return {"status": "error", "error": self.code, "message": self.message, **self.detail}


# ============================================================
# Database operations
# ============================================================

def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a sqlite3 connection with vec0 loaded."""
    import sqlite_vec

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a table (regular or virtual) exists."""
    # Virtual tables don't appear in sqlite_master as 'table' — they're 'table' type too
    # but let's use a safe check:
    try:
        conn.execute(f"SELECT COUNT(*) FROM {table_name} LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


# ============================================================
# Command: download
# ============================================================

def cmd_download(args) -> Dict[str, Any]:
    """Pre-fetch Jina v5 model weights to HuggingFace cache."""
    logger.info("Downloading Jina v5 model: %s", JINA_MODEL_NAME)
    t0 = time.time()

    # This triggers the full download + caching
    model = _get_jina_model()

    # Quick sanity: encode a test sentence
    test_vec = encode_jina_query("test embedding")
    dim = len(struct.unpack(f"{JINA_DIM}f", test_vec))

    elapsed = time.time() - t0
    result = {
        "status": "success",
        "model": JINA_MODEL_NAME,
        "embedding_dim": dim,
        "download_time_s": round(elapsed, 1),
        "device": str(model.device),
    }
    logger.info("Download complete: %s", json.dumps(result, indent=2))
    return result


# ============================================================
# Command: migrate
# ============================================================

def cmd_migrate(args) -> Dict[str, Any]:
    """Apply migration_013 to create memory_embeddings_v2 table."""
    db_path = args.db_path
    logger.info("Applying embedding upgrade migration to: %s", db_path)

    if not Path(db_path).exists():
        return {"status": "error", "message": f"Database not found: {db_path}"}

    conn = _get_conn(db_path)

    # Check if already applied
    if _table_exists(conn, "memory_embeddings_v2"):
        v2_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings_v2").fetchone()[0]
        logger.info("memory_embeddings_v2 already exists (%d rows)", v2_count)
        conn.close()
        return {
            "status": "already-applied",
            "v2_row_count": v2_count,
        }

    # Apply migration SQL
    if not MIGRATION_SQL.exists():
        conn.close()
        return {"status": "error", "message": f"Migration SQL not found: {MIGRATION_SQL}"}

    sql = MIGRATION_SQL.read_text()
    conn.executescript(sql)
    conn.commit()
    conn.close()

    logger.info("Migration applied successfully")
    return {"status": "success", "migration": "013_embedding_upgrade"}


# ============================================================
# Command: re-embed
# ============================================================

def cmd_re_embed(args) -> Dict[str, Any]:
    """Re-embed the memories that need it, under the brain's OWN model.

    A brain with an install lock (nomic or qwen): every memory without a vector
    stamped under the lock's checksum for its current text is embedded with the
    locked model, through the same core as `tier set` (organs/tier_set.py) with the
    target equal to the lock -- the lock and the tier do not move. The jina loader is
    never touched on that path.

    A brain with no lock that embeds under nomic or qwen (it predates the lock): the
    same, under the shipped catalogue entry for the tier it already uses. No lock is
    written and the metadata rows stay as found.

    A brain that embeds under jina (kept for personal installs): the original
    behaviour -- Jina v5 into memory_embeddings_v2, in batches of BATCH_SIZE,
    skipping memories that already have a v2 row.

    A brain whose tier cannot be read refuses, typed (TIER_UNRESOLVED).
    """
    db_path = args.db_path
    batch_size = getattr(args, "batch_size", BATCH_SIZE)
    import tier_set
    try:
        locked = tier_set.re_embed_under_lock(Path(db_path), batch_size=batch_size)
    except tier_set.TierRefused as e:
        raise VerbRefused(e.code, e.message, **e.detail)
    except _lock_module().EmbedderLockError as e:
        raise VerbRefused(e.code, str(e), **{k: v for k, v in e.detail.items()})
    if locked is not None:
        return locked
    logger.info("Re-embedding all memories with Jina v5 (batch_size=%d)", batch_size)

    conn = _get_conn(db_path)

    # Ensure migration has been applied
    if not _table_exists(conn, "memory_embeddings_v2"):
        conn.close()
        return {"status": "error", "message": "memory_embeddings_v2 table does not exist. Run 'migrate' first."}

    # Get all memory IDs + content
    rows = conn.execute("""
        SELECT m.id, m.content, m.summary
        FROM memories m
        WHERE m.is_archived = 0
        ORDER BY m.id
    """).fetchall()
    total = len(rows)
    logger.info("Total active memories to embed: %d", total)

    # Check which are already embedded in v2
    existing_ids = set()
    try:
        existing_rows = conn.execute("SELECT memory_id FROM memory_embeddings_v2").fetchall()
        existing_ids = {r[0] for r in existing_rows}
    except sqlite3.OperationalError:
        pass
    logger.info("Already embedded in v2: %d", len(existing_ids))

    # Filter to un-embedded
    to_embed = [(mid, content, summary) for mid, content, summary in rows if mid not in existing_ids]
    logger.info("Remaining to embed: %d", len(to_embed))

    if not to_embed:
        conn.close()
        return {
            "status": "success",
            "message": "All memories already embedded",
            "total": total,
            "existing": len(existing_ids),
        }

    # Log the upgrade run
    started_at = datetime.now().isoformat()
    conn.execute(
        "INSERT INTO embedding_upgrade_log (started_at, model_name, embedding_dim, memories_total, status) "
        "VALUES (?, ?, ?, ?, ?)",
        # migration_043: the log names the model this run embeds with; no column DEFAULT names it for us
        (started_at, JINA_MODEL_NAME, JINA_DIM, total, "in_progress"),
    )
    conn.commit()

    # Pre-load model (triggers download if not cached)
    _get_jina_model()

    # Batch processing
    processed = 0
    errors = 0
    t0 = time.time()
    import memory_write

    def _write_one(mid: int, text: str, emb: bytes) -> None:
        """The vector and its stamp. A pre-lock brain keeps writing: the catalogue ships no
        checksum for this tier, so the stamp carries the declared absence."""
        conn.execute(
            "INSERT OR REPLACE INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?, ?)",
            (mid, emb),
        )
        memory_write._stamp_provenance(conn, mid, "memory_embeddings_v2", JINA_DIM, text,
                                       "embedding_upgrade.re_embed")

    for batch_start in range(0, len(to_embed), batch_size):
        batch = to_embed[batch_start:batch_start + batch_size]
        texts = []
        ids = []

        for mid, content, summary in batch:
            # Use content primarily; fall back to summary if content is empty
            text = content if content else (summary or "")
            texts.append(text)
            ids.append(mid)

        try:
            embeddings = encode_jina(
                texts,
                task=JINA_TASK_RETRIEVAL,
                prompt_name=JINA_PROMPT_DOCUMENT,
            )

            for mid, text, emb in zip(ids, texts, embeddings):
                _write_one(mid, text, emb)

            conn.commit()
            processed += len(batch)

        except RuntimeError as e:
            conn.rollback()
            if _device_api().is_out_of_memory(e):
                # OOM (any backend: CUDA, HIP, MPS, XPU): clear the backend's cache and retry item by item
                logger.warning("OOM at offset %d, retrying item-by-item...", batch_start)
                _device_api().empty_cache()
                for mid, text in zip(ids, texts):
                    try:
                        single_emb = encode_jina(
                            [text],
                            task=JINA_TASK_RETRIEVAL,
                            prompt_name=JINA_PROMPT_DOCUMENT,
                        )
                        _write_one(mid, text, single_emb[0])
                        conn.commit()
                        processed += 1
                    except Exception as inner_e:
                        conn.rollback()
                        logger.error("Single-item embed failed for id=%d: %s", mid, inner_e)
                        errors += 1
                        _device_api().empty_cache()
            else:
                logger.error("Batch error at offset %d: %s", batch_start, e)
                errors += len(batch)

        except Exception as e:
            conn.rollback()
            logger.error("Batch error at offset %d: %s", batch_start, e)
            errors += len(batch)
            continue

        # Clear the backend's allocator cache every 4 batches to prevent fragmentation OOM
        if (batch_start // batch_size) % 4 == 3:
            try:
                _device_api().empty_cache()
            except Exception:
                pass

        # Progress logging every 100 memories
        if processed % 100 < batch_size:
            elapsed = time.time() - t0
            rate = processed / elapsed if elapsed > 0 else 0
            logger.info(
                "Progress: %d/%d (%.1f%%) — %.1f mem/s — elapsed %.1fs",
                processed, len(to_embed),
                100 * processed / len(to_embed),
                rate, elapsed,
            )

    elapsed = time.time() - t0

    # Update log
    status = "completed" if errors == 0 else "completed_with_errors"
    conn.execute("""
        UPDATE embedding_upgrade_log
        SET completed_at = ?, memories_processed = ?, status = ?
        WHERE started_at = ?
    """, (datetime.now().isoformat(), processed, status, started_at))
    conn.commit()
    conn.close()

    result = {
        "status": status,
        "processed": processed,
        "errors": errors,
        "total": total,
        "elapsed_s": round(elapsed, 1),
        "rate_mem_per_s": round(processed / elapsed, 1) if elapsed > 0 else 0,
    }
    logger.info("Re-embedding complete: %s", json.dumps(result, indent=2))
    return result


# ============================================================
# Command: ab-compare
# ============================================================

def cmd_ab_compare(args) -> Dict[str, Any]:
    """Run 50-query A/B comparison: Nomic (768) vs Jina v5 (1024).

    Compares recall@5 and nDCG@10 for both embedding models against
    ground-truth labels from the L2-C1 eval set.

    Only evaluates queries that have ground_truth_top_3 defined (non-null).
    """
    db_path = args.db_path
    logger.info("Running A/B comparison (Nomic vs Jina v5) on: %s", db_path)

    conn = _get_conn(db_path)

    # Verify both tables exist
    if not _table_exists(conn, "memory_embeddings"):
        conn.close()
        return {"status": "error", "message": "memory_embeddings (Nomic) table missing"}
    if not _table_exists(conn, "memory_embeddings_v2"):
        conn.close()
        return {"status": "error", "message": "memory_embeddings_v2 (Jina) table missing"}

    # Load eval queries
    queries_path = EVAL_QUERIES_PATH
    if not queries_path.exists():
        conn.close()
        return {"status": "error",
                "message": f"Eval queries not found at {EVAL_QUERIES_PATH}; "
                           "set VOIDLOOM_EVAL_QUERIES"}

    queries = []
    with open(queries_path) as f:
        for line in f:
            line = line.strip()
            if line:
                queries.append(json.loads(line))

    # Filter to scoreable queries (have ground truth)
    scoreable = [q for q in queries if q.get("ground_truth_top_3")]
    logger.info("Total queries: %d, scoreable: %d", len(queries), len(scoreable))

    # Run both models on all scoreable queries
    results_nomic = []
    results_jina = []
    per_query_results = []

    top_k = 10  # Retrieve top-10 for nDCG@10, evaluate recall@5 on top-5
    # The nomic side encodes under the lock of THIS database (or, before the lock
    # existed, the shipped catalogue's cpu entry): verified bytes, never a bare name.
    nomic_lock = brain_lock(db_path, tier="nomic")

    for qi, q in enumerate(scoreable):
        query_text = q["query"]
        ground_truth = set(q["ground_truth_top_3"])

        # --- Nomic path (existing memory_embeddings) ---
        nomic_vec = encode_nomic(query_text, prefix="search_query", lock=nomic_lock)
        try:
            nomic_rows = conn.execute("""
                SELECT emb.memory_id, emb.distance
                FROM memory_embeddings AS emb
                JOIN memories AS m ON m.id = emb.memory_id
                WHERE emb.embedding MATCH ?
                  AND k = ?
                  AND m.is_archived = 0
                ORDER BY emb.distance
            """, (nomic_vec, top_k)).fetchall()
            nomic_ids = [int(r[0]) for r in nomic_rows]
        except sqlite3.OperationalError as e:
            logger.warning("Nomic search failed for query %d: %s", qi, e)
            nomic_ids = []

        # --- Jina v5 path (memory_embeddings_v2) ---
        jina_vec = encode_jina_query(query_text)
        try:
            jina_rows = conn.execute("""
                SELECT emb.memory_id, emb.distance
                FROM memory_embeddings_v2 AS emb
                JOIN memories AS m ON m.id = emb.memory_id
                WHERE emb.embedding MATCH ?
                  AND k = ?
                  AND m.is_archived = 0
                ORDER BY emb.distance
            """, (jina_vec, top_k)).fetchall()
            jina_ids = [int(r[0]) for r in jina_rows]
        except sqlite3.OperationalError as e:
            logger.warning("Jina search failed for query %d: %s", qi, e)
            jina_ids = []

        # Compute metrics
        nomic_recall5 = _recall_at_k(nomic_ids, ground_truth, k=5)
        jina_recall5 = _recall_at_k(jina_ids, ground_truth, k=5)
        nomic_ndcg10 = _ndcg_at_k(nomic_ids, ground_truth, k=10)
        jina_ndcg10 = _ndcg_at_k(jina_ids, ground_truth, k=10)

        results_nomic.append({"recall5": nomic_recall5, "ndcg10": nomic_ndcg10})
        results_jina.append({"recall5": jina_recall5, "ndcg10": jina_ndcg10})

        per_query_results.append({
            "id": q["id"],
            "type": q["type"],
            "query": query_text,
            "ground_truth": list(ground_truth),
            "nomic_top5": nomic_ids[:5],
            "jina_top5": jina_ids[:5],
            "nomic_recall5": round(nomic_recall5, 4),
            "jina_recall5": round(jina_recall5, 4),
            "nomic_ndcg10": round(nomic_ndcg10, 4),
            "jina_ndcg10": round(jina_ndcg10, 4),
            "delta_recall5": round(jina_recall5 - nomic_recall5, 4),
            "delta_ndcg10": round(jina_ndcg10 - nomic_ndcg10, 4),
        })

    conn.close()

    # Aggregate metrics
    n = len(scoreable)
    avg_nomic_recall5 = sum(r["recall5"] for r in results_nomic) / n if n else 0
    avg_jina_recall5 = sum(r["recall5"] for r in results_jina) / n if n else 0
    avg_nomic_ndcg10 = sum(r["ndcg10"] for r in results_nomic) / n if n else 0
    avg_jina_ndcg10 = sum(r["ndcg10"] for r in results_jina) / n if n else 0

    # Per-type breakdown
    type_breakdown = {}
    for pq in per_query_results:
        qtype = pq["type"]
        if qtype not in type_breakdown:
            type_breakdown[qtype] = {"nomic_recall5": [], "jina_recall5": [],
                                      "nomic_ndcg10": [], "jina_ndcg10": []}
        type_breakdown[qtype]["nomic_recall5"].append(pq["nomic_recall5"])
        type_breakdown[qtype]["jina_recall5"].append(pq["jina_recall5"])
        type_breakdown[qtype]["nomic_ndcg10"].append(pq["nomic_ndcg10"])
        type_breakdown[qtype]["jina_ndcg10"].append(pq["jina_ndcg10"])

    type_summary = {}
    for qtype, vals in type_breakdown.items():
        n_t = len(vals["nomic_recall5"])
        type_summary[qtype] = {
            "n": n_t,
            "nomic_recall5": round(sum(vals["nomic_recall5"]) / n_t, 4),
            "jina_recall5": round(sum(vals["jina_recall5"]) / n_t, 4),
            "nomic_ndcg10": round(sum(vals["nomic_ndcg10"]) / n_t, 4),
            "jina_ndcg10": round(sum(vals["jina_ndcg10"]) / n_t, 4),
            "delta_recall5_pp": round(100 * (sum(vals["jina_recall5"]) - sum(vals["nomic_recall5"])) / n_t, 2),
            "delta_ndcg10_pp": round(100 * (sum(vals["jina_ndcg10"]) - sum(vals["nomic_ndcg10"])) / n_t, 2),
        }

    # Decision: is Jina v5 better?
    delta_ndcg10_pp = round(100 * (avg_jina_ndcg10 - avg_nomic_ndcg10), 2)
    delta_recall5_pp = round(100 * (avg_jina_recall5 - avg_nomic_recall5), 2)
    passes_threshold = delta_ndcg10_pp >= 3.0  # +3pp nDCG@10 threshold
    no_regression = delta_ndcg10_pp >= 0.0

    verdict = "UPGRADE" if passes_threshold else ("NO_REGRESSION" if no_regression else "REGRESSION")

    comparison_result = {
        "timestamp": datetime.now().isoformat(),
        "db_path": db_path,
        "queries_total": len(queries),
        "queries_scoreable": n,
        "models": {
            "baseline": {"name": NOMIC_MODEL_NAME, "dim": NOMIC_DIM, "table": "memory_embeddings"},
            "candidate": {"name": JINA_MODEL_NAME, "dim": JINA_DIM, "table": "memory_embeddings_v2"},
        },
        "aggregate": {
            "nomic_recall5": round(avg_nomic_recall5, 4),
            "jina_recall5": round(avg_jina_recall5, 4),
            "delta_recall5_pp": delta_recall5_pp,
            "nomic_ndcg10": round(avg_nomic_ndcg10, 4),
            "jina_ndcg10": round(avg_jina_ndcg10, 4),
            "delta_ndcg10_pp": delta_ndcg10_pp,
        },
        "per_type": type_summary,
        "threshold": "+3pp nDCG@10 (or at minimum: no regression)",
        "passes_threshold": passes_threshold,
        "no_regression": no_regression,
        "verdict": verdict,
        "per_query": per_query_results,
    }

    # Save to file
    output_path = Path(__file__).parent.parent / "eval" / "ab_comparison_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(comparison_result, f, indent=2)
    logger.info("A/B comparison saved to: %s", output_path)

    # Summary log
    logger.info("=" * 60)
    logger.info("A/B COMPARISON RESULTS")
    logger.info("=" * 60)
    logger.info("Queries: %d total, %d scoreable", len(queries), n)
    logger.info("Nomic v1.5: recall@5=%.4f, nDCG@10=%.4f", avg_nomic_recall5, avg_nomic_ndcg10)
    logger.info("Jina v5:    recall@5=%.4f, nDCG@10=%.4f", avg_jina_recall5, avg_jina_ndcg10)
    logger.info("Delta:      recall@5=%+.2fpp, nDCG@10=%+.2fpp", delta_recall5_pp, delta_ndcg10_pp)
    logger.info("Verdict:    %s (threshold: +3pp nDCG@10)", verdict)
    logger.info("=" * 60)

    return comparison_result


# ============================================================
# Command: swap
# ============================================================

def cmd_swap(args) -> Dict[str, Any]:
    """REFUSED, typed, and the process EXITS 1.

    `swap` re-pointed a brain at another tier by rewriting the metadata rows
    (`active_embedding_table` and friends) without re-embedding a single vector:
    after it, the engine read one model's vectors with another model's queries --
    same width, different space, no error anywhere. A brain with an install lock
    takes its tier from the lock, so the rows would not even move it; a brain
    without one would be silently re-pointed. Neither is a swap.

    Changing an install's tier is re-embedding every memory under the new weights
    and only then writing the new lock: `voidloom tier set <nomic|qwen>`
    (organs/tier_set.py), which keeps the old vectors as the rollback path.

    It RAISES; main() prints the envelope and exits 1. An error envelope with
    process exit 0 reads as success to every shell gate that wraps the verb.
    """
    raise VerbRefused(
        "SWAP_REFUSED",
        "`swap` re-pointed an install without re-embedding and is refused. The tier of an "
        "install is its embedder.lock.json; changing it means re-embedding every memory "
        "under the new weights before the lock moves: `voidloom tier set <nomic|qwen>` "
        "(`--plan` first for the counts; the old vectors are kept until `voidloom tier "
        "cleanup`).",
        replacement="tier set", built=True,
        usage="voidloom tier set <nomic|qwen> [--plan]",
        db_path=getattr(args, "db_path", None))


# ============================================================
# Command: stats
# ============================================================

def cmd_stats(args) -> Dict[str, Any]:
    """Embedding statistics: the lock, and THE READER'S coverage envelope --
    matched/total, three numbers -- beside one raw count, labelled as a raw count.
    No presence percentage over a table by literal name: a presence count is not
    coverage, and on most installs one of those tables belongs to no model in use."""
    import memory_write
    db_path = args.db_path
    conn = _get_conn(db_path)

    active_count = conn.execute(
        "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived, 0) = 0").fetchone()[0]

    def _meta(key: str) -> Optional[str]:
        try:
            row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None

    active_model = _meta("active_embedding_model")
    active_table = _meta("active_embedding_table")
    # The install lock first: on a locked brain it names the model and table, and the metadata rows
    # only echo it (`tier set` moves them together). A brain with no lock is read from metadata.
    # embed_resolver's rule decides (a lock naming a table or width the engine does not build refuses).
    table_source, lock_error = "metadata", None
    lock_state: Dict[str, Any] = {"present": False, "basis": "lockless",
                                  "note": "no install lock beside this brain: it predates the lock or embeds "
                                          "under a tier the catalogue does not ship"}
    _el = _lock_module()
    import embed_resolver as _er            # stdlib-only, beside embedder_lock (imported above)
    try:
        _locked = _er._from_lock(conn)
        if _locked is not None:
            _lock = _el.resolve(conn)
            active_model = _lock["model_id"]
            active_table, table_source = _locked[0], "lock"
            lock_state = {"present": True, "tier": _lock["tier"], "model_id": _lock["model_id"],
                          "vec_table": _lock["vec_table"], "dim": int(_lock["dim"]),
                          "weights_hash": _lock["weights_hash"], "code": _lock.get("code")}
    except (_er.EmbedTierUnresolved, _el.EmbedderLockError) as e:
        lock_error = f"{e.code}: {e}"
        # LOCK_MISSING here is a lock that was written and is gone (one never written is the lockless note above)
        lock_state = {"present": e.code != "LOCK_MISSING", "error": e.code, "message": str(e),
                      "was_locked": bool((getattr(e, "detail", None) or {}).get("was_locked"))}

    coverage = memory_write.coverage_envelope(conn)
    raw_vectors = None
    if active_table and _table_exists(conn, active_table):
        raw_vectors = conn.execute(f"SELECT COUNT(*) FROM {active_table}").fetchone()[0]

    # Check upgrade log
    upgrade_log = []
    if _table_exists(conn, "embedding_upgrade_log"):
        log_rows = conn.execute(
            "SELECT started_at, completed_at, status, memories_processed, model_name "
            "FROM embedding_upgrade_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
        upgrade_log = [
            {"started": r[0], "completed": r[1], "status": r[2], "processed": r[3], "model": r[4]}
            for r in log_rows
        ]

    conn.close()

    result = {
        "status": "success",
        "active_memories": active_count,
        "lock": lock_state,
        "coverage": coverage,                  # the reader's envelope: matched/total, three numbers
        "vectors_in_lock_table": raw_vectors,  # a raw presence count of the active table, labelled as such
        "active_model": active_model,
        "active_table": active_table,
        "table_source": table_source,
        "lock_error": lock_error,
        "recent_upgrade_log": upgrade_log,
    }
    logger.info("Stats: %s", json.dumps(result, indent=2, default=str))
    return result


# ============================================================
# Metrics helpers
# ============================================================

def _recall_at_k(retrieved_ids: List[int], ground_truth: set, k: int = 5) -> float:
    """Recall@k: fraction of ground truth items found in top-k results."""
    if not ground_truth:
        return 0.0
    top_k_set = set(retrieved_ids[:k])
    hits = len(top_k_set & ground_truth)
    return hits / len(ground_truth)


def _ndcg_at_k(retrieved_ids: List[int], ground_truth: set, k: int = 10) -> float:
    """nDCG@k: normalized discounted cumulative gain.

    Binary relevance: items in ground_truth get relevance=1, others get 0.
    """
    import math

    if not ground_truth:
        return 0.0

    # DCG
    dcg = 0.0
    for i, mid in enumerate(retrieved_ids[:k]):
        if mid in ground_truth:
            dcg += 1.0 / math.log2(i + 2)  # i+2 because positions are 1-indexed

    # Ideal DCG (all ground truth items at top positions)
    ideal_k = min(len(ground_truth), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_k))

    if idcg == 0:
        return 0.0
    return dcg / idcg


# ============================================================
# Command: full (all steps in sequence)
# ============================================================

def cmd_full(args) -> Dict[str, Any]:
    """Run the full upgrade pipeline: download -> migrate -> re-embed -> ab-compare -> (conditional swap).

    The jina tooling, for a brain that embeds under jina. Its first step fetches a
    cc-by-nc model, so on any other brain it refuses before touching the network."""
    import tier_set
    db = Path(args.db_path).resolve()
    try:
        not_jina = tier_set._installed_lock(db) is not None or tier_set._prelock_engine_tier(db) is not None
    except tier_set.TierRefused as e:           # a lost lock, an unreadable tier: typed, before any fetch
        raise VerbRefused(e.code, e.message, **e.detail)
    if not_jina:
        raise VerbRefused(
            "JINA_PIPELINE_ONLY",
            "`full` fetches and compares the jina tier, and this brain does not embed under it. "
            "To change a brain's model: `voidloom tier set <nomic|qwen>`. To re-embed it under the "
            "model it has: `re-embed`.", db_path=str(db))
    results = {}

    logger.info("=" * 60)
    logger.info("FULL EMBEDDING UPGRADE PIPELINE")
    logger.info("=" * 60)

    # Step 1: Download
    logger.info("\n[1/5] Downloading model...")
    results["download"] = cmd_download(args)

    # Step 2: Migrate
    logger.info("\n[2/5] Applying migration...")
    results["migrate"] = cmd_migrate(args)

    # Step 3: Re-embed
    logger.info("\n[3/5] Re-embedding all memories...")
    results["re_embed"] = cmd_re_embed(args)
    if str(results["re_embed"].get("status", "")).startswith("error"):
        results["pipeline_status"] = "failed_at_re_embed"
        return results

    # Step 4: A/B compare
    logger.info("\n[4/5] Running A/B comparison...")
    results["ab_compare"] = cmd_ab_compare(args)
    if str(results["ab_compare"].get("status", "")).startswith("error"):
        results["pipeline_status"] = "failed_at_ab_compare"
        return results

    # Step 5: Conditional swap
    verdict = results["ab_compare"].get("verdict", "REGRESSION")
    if verdict in ("UPGRADE", "NO_REGRESSION"):
        logger.info("\n[5/5] swap is refused under the install lock (verdict: %s)", verdict)
        try:
            results["swap"] = cmd_swap(args)
        except VerbRefused as e:
            results["swap"] = e.as_dict()
    else:
        logger.warning("\n[5/5] SKIPPING swap (verdict: %s — regression detected)", verdict)
        results["swap"] = {"status": "skipped", "reason": f"verdict={verdict}"}

    results["pipeline_status"] = "completed"
    logger.info("\nPipeline complete. Verdict: %s", verdict)
    return results


# ============================================================
# Unit tests (self-contained, run with: python3 embedding_upgrade.py test)
# ============================================================

def cmd_test(args) -> Dict[str, Any]:
    """Self-test on a LOCKED temp brain, with no jina anywhere. Vectors are written only
    through the guarded writer; nothing names a table by literal; the assertions are on
    the lock's own vec_table and the reader's coverage. The model-dependent test SKIPS
    with a reason when the locked snapshot or the stack is absent: it never fetches, and
    a skip is reported as a skip, never as a pass."""
    import shutil
    import tempfile
    import memory_write
    embedder_lock = _lock_module()

    results: Dict[str, Any] = {"tests": [], "passed": 0, "failed": 0, "skipped": 0}

    def _assert(name: str, condition: bool, msg: str = ""):
        if condition:
            results["tests"].append({"name": name, "status": "PASS"})
            results["passed"] += 1
            logger.info("  PASS: %s", name)
        else:
            results["tests"].append({"name": name, "status": "FAIL", "message": msg})
            results["failed"] += 1
            logger.error("  FAIL: %s — %s", name, msg)

    def _skip(name: str, why: str):
        results["tests"].append({"name": name, "status": "SKIP", "message": why})
        results["skipped"] += 1
        logger.warning("  SKIP: %s — %s", name, why)

    logger.info("Running embedding upgrade unit tests...")

    # Test 1: serialize_f32 produces correct byte length
    vec_768 = [0.1] * 768
    vec_1024 = [0.2] * 1024
    _assert("serialize_768_dim", len(serialize_f32(vec_768)) == 768 * 4, f"Expected {768*4}, got {len(serialize_f32(vec_768))}")
    _assert("serialize_1024_dim", len(serialize_f32(vec_1024)) == 1024 * 4, f"Expected {1024*4}, got {len(serialize_f32(vec_1024))}")

    # Test 2: serialize/deserialize roundtrip
    original = [0.123, -0.456, 0.789]
    deserialized = list(struct.unpack(f"{len(original)}f", serialize_f32(original)))
    _assert("serialize_roundtrip", all(abs(a - b) < 1e-6 for a, b in zip(original, deserialized)))

    # Test 3: Metric helpers
    _assert("recall_at_5_full", abs(_recall_at_k([1, 2, 3, 4, 5], {1, 2, 3}, 5) - 1.0) < 1e-6)
    _assert("recall_at_5_partial", abs(_recall_at_k([1, 2, 6, 7, 8], {1, 2, 3}, 5) - 2/3) < 1e-6)
    _assert("recall_at_5_empty_gt", abs(_recall_at_k([1, 2, 3], set(), 5)) < 1e-6)
    _assert("ndcg_at_10_perfect", abs(_ndcg_at_k([1, 2, 3], {1, 2, 3}, 10) - 1.0) < 1e-6)
    _assert("ndcg_at_10_partial", _ndcg_at_k([4, 1, 5, 2, 6], {1, 2, 3}, 10) > 0)
    _assert("ndcg_at_10_empty_gt", abs(_ndcg_at_k([1, 2, 3], set(), 10)) < 1e-6)

    # Test 4: swap is a typed refusal
    class _A:
        db_path = "/nonexistent"
    try:
        cmd_swap(_A())
        _assert("swap_refuses_typed", False, "cmd_swap returned instead of raising")
    except VerbRefused as e:
        _assert("swap_refuses_typed", e.code == "SWAP_REFUSED" and e.as_dict().get("replacement") == "tier set")

    # Test 5: a locked temp brain, re-embedded through the guarded writer
    tmp = tempfile.mkdtemp(prefix="vl_embed_test_")
    temp_db = os.path.join(tmp, "brain.db")
    try:
        lock = embedder_lock.write_lock(tmp, embedder_lock.DEFAULT_TIER, locked_by="embedding_upgrade.test")
        table, dim = lock["vec_table"], int(lock["dim"])
        conn = _get_conn(temp_db)
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, content TEXT NOT NULL, summary TEXT, "
                     "is_archived INTEGER NOT NULL DEFAULT 0)")
        conn.execute("CREATE TABLE memory_tags (memory_id INTEGER, tag TEXT)")
        conn.execute(f"CREATE VIRTUAL TABLE {table} USING vec0(memory_id INTEGER PRIMARY KEY, "
                     f"embedding float[{dim}])")
        conn.execute("INSERT INTO memories (id, content) VALUES (1, 'test memory one')")
        conn.execute("INSERT INTO memories (id, content) VALUES (2, 'test memory two')")
        conn.commit()
        memory_write.ensure_provenance_table(conn)
        conn.commit()
        flt = memory_write.provenance_filter(conn)
        _assert("unit_of_work_before", memory_write.count_pending_embeddings(conn, flt=flt) == 2)
        conn.close()

        snap = embedder_lock.snapshot_dir_for(lock["model_id"], lock["revision"])
        code = lock.get("code")
        code_snap = embedder_lock.snapshot_dir_for(code["model_id"], code["revision"]) if code else True
        if snap is None or code_snap is None:
            _skip("re_embed_on_locked_brain", f"the locked snapshot ({lock['model_id']}@{lock['revision'][:12]}"
                  + (f" + {code['model_id']}" if code else "") + ") is not in this host's HF cache")
        else:
            try:
                import sentence_transformers  # noqa: F401
                import torch  # noqa: F401
            except Exception as e:
                _skip("re_embed_on_locked_brain", f"the semantic stack is not importable ({e})")
            else:
                class FakeArgs:
                    db_path = temp_db
                    batch_size = 2
                r = cmd_re_embed(FakeArgs())
                receipt = r.get("receipt") or {}
                _assert("re_embed_completes", r.get("status") == "ok", json.dumps(r, default=str)[:300])
                _assert("re_embed_processed_two", receipt.get("embedded") == 2, str(receipt.get("embedded")))
                conn = _get_conn(temp_db)
                cov = memory_write.provenance_coverage(conn)
                _assert("coverage_two_of_two", cov.coverage == {"matched": 2, "total": 2, "value": 1.0},
                        str(cov.coverage))
                _assert("no_unstamped_vectors", cov.vectors_without_stamp == 0)
                row = conn.execute("SELECT weights_hash, dim FROM embedding_provenance WHERE memory_id = 1").fetchone()
                _assert("stamp_carries_lock_hash", row is not None and row[0] == lock["weights_hash"] and row[1] == dim)
                q = encode_locked("test memory", is_query=True, lock=lock)
                hits = memory_write.knn(conn, q, 2)
                _assert("knn_over_search_set", len(hits) == 2, str(hits))
                _assert("unit_of_work_after", memory_write.count_pending_embeddings(conn, flt=flt) == 0)
                conn.close()
                r2 = cmd_re_embed(FakeArgs())
                _assert("second_run_is_idempotent", (r2.get("receipt") or {}).get("embedded") == 0,
                        json.dumps(r2, default=str)[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # Summary
    logger.info("\n%d/%d tests passed (%d skipped)", results["passed"],
                results["passed"] + results["failed"], results["skipped"])
    results["status"] = "success" if results["failed"] == 0 else "failed"
    return results


# ============================================================
# CLI entry point
# ============================================================

def main(argv: Optional[List[str]] = None) -> int:
    # Standalone CLI use (download/migrate/re-embed/ab-compare/...) wants the
    # INFO breadcrumbs. basicConfig lives HERE (not at import) so importing this
    # module on the recall hot path never attaches a handler to the root logger
    # (H3 quiet-by-default). The voidloom-child `logger` also needs INFO enabled
    # for this run; respect VOIDLOOM_LOG_LEVEL if the operator set one.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    _cli_level = os.environ.get("VOIDLOOM_LOG_LEVEL", "INFO").strip().upper()
    logger.setLevel(getattr(logging, _cli_level, logging.INFO))

    parser = argparse.ArgumentParser(
        description="Embedding Upgrade: Nomic v1.5 -> Jina v5-text-small"
    )
    parser.add_argument(
        "--db-path", default=DEFAULT_TEST_DB,
        help=f"Database path (default: {DEFAULT_TEST_DB})",
    )
    parser.add_argument(
        "--production", action="store_true",
        help="Use production database path (requires explicit flag for safety)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"Batch size for re-embedding (default: {BATCH_SIZE})",
    )

    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("download", help="Download Jina v5 model")
    subparsers.add_parser("migrate", help="Apply migration SQL")
    subparsers.add_parser("re-embed", help="Re-embed what needs it under the brain's own lock (jina: the v2 path)")
    subparsers.add_parser("ab-compare", help="Run A/B comparison")
    subparsers.add_parser("swap", help="REFUSED: re-pointing without re-embedding (exit 1); use `voidloom tier set`")
    subparsers.add_parser("stats", help="Show embedding stats")
    subparsers.add_parser("full", help="Full jina pipeline (all steps); refused on any other brain")
    subparsers.add_parser("test", help="Run unit tests")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    # Resolve DB path
    if args.production:
        args.db_path = PRODUCTION_DB
        logger.warning("*** PRODUCTION MODE: using %s ***", PRODUCTION_DB)
    elif args.db_path == DEFAULT_TEST_DB and not Path(DEFAULT_TEST_DB).exists():
        # Auto-copy production to test path
        if Path(PRODUCTION_DB).exists():
            import shutil
            logger.info("Copying production DB to test path: %s", DEFAULT_TEST_DB)
            shutil.copy2(PRODUCTION_DB, DEFAULT_TEST_DB)

    # Dispatch
    commands = {
        "download": cmd_download,
        "migrate": cmd_migrate,
        "re-embed": cmd_re_embed,
        "ab-compare": cmd_ab_compare,
        "swap": cmd_swap,
        "stats": cmd_stats,
        "full": cmd_full,
        "test": cmd_test,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    # A refusal is an envelope on stdout AND a non-zero exit, and so is a result that
    # says it failed: an error envelope with process exit 0 reads as success to every
    # shell gate that wraps this verb.
    try:
        result = handler(args)
    except Exception as e:
        code = getattr(e, "code", None)
        if not code:
            raise
        env = e.as_dict() if hasattr(e, "as_dict") else {"status": "error", "error": code, "message": str(e)}
        print(json.dumps(env, indent=2, default=str))
        return 1
    print(json.dumps(result, indent=2, default=str))
    state = str(result.get("status") or result.get("pipeline_status") or "")
    return 1 if state.startswith(("error", "failed", "completed_with_errors")) else 0


if __name__ == "__main__":
    sys.exit(main())
