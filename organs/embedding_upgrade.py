#!/usr/bin/env python3
"""Embedding Upgrade: Nomic v1.5 (768-dim) -> Jina v5-text-small (1024-dim).

Wizard Library Level 3 Component 1.

This module handles the full lifecycle of upgrading the embedding model:
  1. Download + verify Jina v5 model
  2. Apply migration (create memory_embeddings_v2)
  3. Re-embed all memories in batches
  4. A/B comparison against Nomic baseline
  5. Swap default (make Jina v5 the production path)
  6. Stats + diagnostics

Usage (standalone):
    python3 -u embedding_upgrade.py download     # Pre-fetch model to HF cache
    python3 -u embedding_upgrade.py migrate      # Apply migration SQL
    python3 -u embedding_upgrade.py re-embed     # Re-embed all memories
    python3 -u embedding_upgrade.py ab-compare   # Run 50-query A/B comparison
    python3 -u embedding_upgrade.py swap         # Swap to Jina v5 as default
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
        cache_root = Path(
            os.environ.get("HF_HOME")
            or os.environ.get("HUGGINGFACE_HUB_CACHE")
            or (Path.home() / ".cache" / "huggingface")
        )
        hub = cache_root / "hub" if (cache_root / "hub").exists() else cache_root
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
_nomic_model_cache: Dict[str, Any] = {}


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
    with contextlib.redirect_stdout(sys.stderr):  # remote code print()s; keep stdout clean
        model = SentenceTransformer(
            JINA_MODEL_NAME,
            revision=JINA_REVISION,  # Voidloom #176 RCE: pin code tree (moved tag can't swap it)
            trust_remote_code=True,
            device=device,
            local_files_only=offline,
            model_kwargs={"default_task": JINA_TASK_RETRIEVAL},
        )

    elapsed = time.time() - t0
    logger.info("Jina v5 loaded in %.1fs (device=%s)", elapsed, device)
    _jina_model_cache["model"] = model
    return model


def _get_nomic_model():
    """Load Nomic v1.5 model (baseline for A/B comparison)."""
    if "model" in _nomic_model_cache:
        return _nomic_model_cache["model"]

    import torch
    from sentence_transformers import SentenceTransformer

    _quiet_model_load_noise()  # H3: keep load INFO + ST version warning off default stderr
    device = _torch_device(torch)
    logger.info("Loading Nomic v1.5 model on %s...", device)
    t0 = time.time()

    # H11 offline: warm cache => local-only (no hub round-trip); explicit
    # HF_HUB_OFFLINE honored; cold machine still downloads on first call.
    offline = _hf_offline_ok(NOMIC_MODEL_NAME)
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):  # remote code print()s; keep stdout clean
        model = SentenceTransformer(
            NOMIC_MODEL_NAME,
            revision=NOMIC_REVISION,  # Voidloom #176 RCE: pin code tree (moved tag can't swap it)
            trust_remote_code=True,
            device=device,
            local_files_only=offline,
        )

    elapsed = time.time() - t0
    logger.info("Nomic v1.5 loaded in %.1fs (device=%s)", elapsed, device)
    _nomic_model_cache["model"] = model
    return model


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


_qwen_model_cache: dict = {}


def _get_qwen_model():
    """Load Qwen3-Embedding-0.6B (apache-2.0) — the SHIPPED 1024-dim tier.

    This exists because `_get_jina_model()` takes no arguments and hardcodes
    JINA_MODEL_NAME. Registering Qwen against that loader (as model_garden briefly
    did) would have loaded JINA while the package claimed Apache — a licence
    violation that reports success. A loader that ignores its spec's repo_id can
    only ever load one model; each tier needs its own, or one that takes the id.
    """
    if "model" in _qwen_model_cache:
        return _qwen_model_cache["model"]
    import torch
    from sentence_transformers import SentenceTransformer
    _quiet_model_load_noise()
    device = _torch_device(torch)
    logger.info("Loading Qwen3-Embedding-0.6B on %s...", device)
    # H11 offline: warm cache => local-only, the same as the nomic and jina loaders.
    offline = _hf_offline_ok(QWEN_MODEL_NAME)
    model = SentenceTransformer(
        QWEN_MODEL_NAME,
        revision=QWEN_REVISION,
        device=device,
        local_files_only=offline,
    )
    _qwen_model_cache["model"] = model
    return model


def _encode_qwen(text: str, *, is_query: bool) -> bytes:
    """Qwen3-Embedding is ASYMMETRIC: queries carry an instruction prompt,
    documents do not. sentence-transformers exposes that as prompt_name='query'.
    Older builds lack the prompt; fall back to plain encoding rather than raising,
    because a symmetric encode is slightly worse retrieval and a crash is an
    outage."""
    import numpy as np
    model = _get_qwen_model()
    kwargs = {"normalize_embeddings": True}
    if is_query:
        try:
            vec = model.encode(text, prompt_name="query", **kwargs)
        except (TypeError, ValueError, KeyError):
            vec = model.encode(text, **kwargs)
    else:
        vec = model.encode(text, **kwargs)
    return serialize_f32(np.asarray(vec)[:QWEN_DIM].astype(np.float32).tolist())


def encode_qwen_query(text: str) -> bytes:
    return _encode_qwen(text, is_query=True)


def encode_qwen_document(text: str) -> bytes:
    return _encode_qwen(text, is_query=False)


def encode_nomic(text: str, prefix: str = "search_document") -> bytes:
    """Encode using Nomic v1.5 (baseline). Matches existing _encode() API."""
    import numpy as np

    model = _get_nomic_model()
    query = f"{prefix}: {text}"
    vec = model.encode(query, normalize_embeddings=True)
    return serialize_f32(vec[:NOMIC_DIM].astype(np.float32).tolist())


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
    """Re-embed all memories using Jina v5 into memory_embeddings_v2.

    Processes in batches of BATCH_SIZE. Checkpoint-safe: skips already-embedded
    memories (checks v2 table for existing rowid).
    """
    db_path = args.db_path
    batch_size = getattr(args, "batch_size", BATCH_SIZE)
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
        "INSERT INTO embedding_upgrade_log (started_at, memories_total, status) VALUES (?, ?, ?)",
        (started_at, total, "in_progress"),
    )
    conn.commit()

    # Pre-load model (triggers download if not cached)
    _get_jina_model()

    # Batch processing
    processed = 0
    errors = 0
    t0 = time.time()

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

            for mid, emb in zip(ids, embeddings):
                conn.execute(
                    "INSERT OR REPLACE INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?, ?)",
                    (mid, emb),
                )

            conn.commit()
            processed += len(batch)

        except RuntimeError as e:
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
                        conn.execute(
                            "INSERT OR REPLACE INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?, ?)",
                            (mid, single_emb[0]),
                        )
                        processed += 1
                    except Exception as inner_e:
                        logger.error("Single-item embed failed for id=%d: %s", mid, inner_e)
                        errors += 1
                        _device_api().empty_cache()
                conn.commit()
            else:
                logger.error("Batch error at offset %d: %s", batch_start, e)
                errors += len(batch)

        except Exception as e:
            logger.error("Batch error at offset %d: %s", batch_start, e)
            errors += len(batch)
            # Continue with next batch (checkpoint-safe: already-inserted rows persist)
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

    for qi, q in enumerate(scoreable):
        query_text = q["query"]
        ground_truth = set(q["ground_truth_top_3"])

        # --- Nomic path (existing memory_embeddings) ---
        nomic_vec = encode_nomic(query_text, prefix="search_query")
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
    """Mark Jina v5 as the default embedding model.

    This updates metadata in the database. The actual code change (pointing
    _encode() at Jina v5) is done in the patched claude_memory.py.
    """
    db_path = args.db_path
    logger.info("Swapping default embedding to Jina v5 in: %s", db_path)

    conn = _get_conn(db_path)

    # Verify v2 table is fully populated
    v1_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    v2_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings_v2").fetchone()[0]

    if v2_count < v1_count * 0.95:
        conn.close()
        return {
            "status": "error",
            "message": f"v2 table incomplete: {v2_count}/{v1_count} rows ({100*v2_count/v1_count:.1f}%)",
        }

    # Update metadata
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("active_embedding_model", JINA_MODEL_NAME),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("active_embedding_dim", str(JINA_DIM)),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("active_embedding_table", "memory_embeddings_v2"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("embedding_upgrade_completed", datetime.now().isoformat()),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("schema_version", "3.7-L3-embedding-upgrade"),
    )
    conn.commit()
    conn.close()

    result = {
        "status": "success",
        "active_model": JINA_MODEL_NAME,
        "active_dim": JINA_DIM,
        "active_table": "memory_embeddings_v2",
        "v1_rows": v1_count,
        "v2_rows": v2_count,
    }
    logger.info("Swap complete: %s", json.dumps(result, indent=2))
    return result


# ============================================================
# Command: stats
# ============================================================

def cmd_stats(args) -> Dict[str, Any]:
    """Show embedding table statistics."""
    db_path = args.db_path
    conn = _get_conn(db_path)

    v1_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    v2_exists = _table_exists(conn, "memory_embeddings_v2")
    v2_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings_v2").fetchone()[0] if v2_exists else 0
    active_count = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]

    # Check active model metadata
    active_model_row = conn.execute(
        "SELECT value FROM metadata WHERE key = 'active_embedding_model'"
    ).fetchone()
    active_model = active_model_row[0] if active_model_row else NOMIC_MODEL_NAME

    active_table_row = conn.execute(
        "SELECT value FROM metadata WHERE key = 'active_embedding_table'"
    ).fetchone()
    active_table = active_table_row[0] if active_table_row else "memory_embeddings"

    # Check upgrade log
    upgrade_log = []
    if _table_exists(conn, "embedding_upgrade_log"):
        log_rows = conn.execute(
            "SELECT started_at, completed_at, status, memories_processed FROM embedding_upgrade_log ORDER BY id DESC LIMIT 5"
        ).fetchall()
        upgrade_log = [
            {"started": r[0], "completed": r[1], "status": r[2], "processed": r[3]}
            for r in log_rows
        ]

    conn.close()

    result = {
        "status": "success",
        "active_memories": active_count,
        "memory_embeddings_v1": {
            "table": "memory_embeddings",
            "model": NOMIC_MODEL_NAME,
            "dim": NOMIC_DIM,
            "rows": v1_count,
        },
        "memory_embeddings_v2": {
            "table": "memory_embeddings_v2",
            "model": JINA_MODEL_NAME,
            "dim": JINA_DIM,
            "rows": v2_count,
            "exists": v2_exists,
        },
        "active_model": active_model,
        "active_table": active_table,
        "coverage_v1_pct": round(100 * v1_count / active_count, 1) if active_count else 0,
        "coverage_v2_pct": round(100 * v2_count / active_count, 1) if active_count else 0,
        "recent_upgrade_log": upgrade_log,
    }
    logger.info("Stats: %s", json.dumps(result, indent=2))
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
    """Run the full upgrade pipeline: download -> migrate -> re-embed -> ab-compare -> (conditional swap)."""
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
    if results["re_embed"].get("status", "").startswith("error"):
        results["pipeline_status"] = "failed_at_re_embed"
        return results

    # Step 4: A/B compare
    logger.info("\n[4/5] Running A/B comparison...")
    results["ab_compare"] = cmd_ab_compare(args)

    # Step 5: Conditional swap
    verdict = results["ab_compare"].get("verdict", "REGRESSION")
    if verdict in ("UPGRADE", "NO_REGRESSION"):
        logger.info("\n[5/5] Swapping to Jina v5 (verdict: %s)...", verdict)
        results["swap"] = cmd_swap(args)
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
    """Run unit tests for the embedding upgrade module."""
    import tempfile

    results = {"tests": [], "passed": 0, "failed": 0}

    def _assert(name: str, condition: bool, msg: str = ""):
        if condition:
            results["tests"].append({"name": name, "status": "PASS"})
            results["passed"] += 1
            logger.info("  PASS: %s", name)
        else:
            results["tests"].append({"name": name, "status": "FAIL", "message": msg})
            results["failed"] += 1
            logger.error("  FAIL: %s — %s", name, msg)

    logger.info("Running embedding upgrade unit tests...")

    # Test 1: serialize_f32 produces correct byte length
    vec_768 = [0.1] * 768
    vec_1024 = [0.2] * 1024
    _assert("serialize_768_dim", len(serialize_f32(vec_768)) == 768 * 4, f"Expected {768*4}, got {len(serialize_f32(vec_768))}")
    _assert("serialize_1024_dim", len(serialize_f32(vec_1024)) == 1024 * 4, f"Expected {1024*4}, got {len(serialize_f32(vec_1024))}")

    # Test 2: serialize/deserialize roundtrip
    original = [0.123, -0.456, 0.789]
    serialized = serialize_f32(original)
    deserialized = list(struct.unpack(f"{len(original)}f", serialized))
    _assert("serialize_roundtrip", all(abs(a - b) < 1e-6 for a, b in zip(original, deserialized)))

    # Test 3: Jina model loads and produces 1024-dim embeddings
    try:
        model = _get_jina_model()
        _assert("jina_model_loads", model is not None)

        test_emb = encode_jina_query("test embedding quality")
        dim = len(struct.unpack(f"{JINA_DIM}f", test_emb))
        _assert("jina_produces_1024_dim", dim == 1024, f"Got dim={dim}")

        # Test 4: Query vs document encoding are different
        query_emb = encode_jina_query("memory retrieval technology")
        doc_emb = encode_jina_document("memory retrieval technology")
        _assert("query_doc_asymmetric", query_emb != doc_emb,
                "Query and document embeddings should differ (asymmetric encoding)")

    except Exception as e:
        _assert("jina_model_loads", False, str(e))
        _assert("jina_produces_1024_dim", False, "Skipped (model load failed)")
        _assert("query_doc_asymmetric", False, "Skipped (model load failed)")

    # Test 5: Nomic model loads and produces 768-dim embeddings
    try:
        nomic_emb = encode_nomic("test embedding", prefix="search_query")
        dim = len(struct.unpack(f"{NOMIC_DIM}f", nomic_emb))
        _assert("nomic_produces_768_dim", dim == 768, f"Got dim={dim}")
    except Exception as e:
        _assert("nomic_produces_768_dim", False, str(e))

    # Test 6: Metric helpers
    _assert("recall_at_5_full", abs(_recall_at_k([1, 2, 3, 4, 5], {1, 2, 3}, 5) - 1.0) < 1e-6)
    _assert("recall_at_5_partial", abs(_recall_at_k([1, 2, 6, 7, 8], {1, 2, 3}, 5) - 2/3) < 1e-6)
    _assert("recall_at_5_empty_gt", abs(_recall_at_k([1, 2, 3], set(), 5)) < 1e-6)
    _assert("ndcg_at_10_perfect", abs(_ndcg_at_k([1, 2, 3], {1, 2, 3}, 10) - 1.0) < 1e-6)
    _assert("ndcg_at_10_partial", _ndcg_at_k([4, 1, 5, 2, 6], {1, 2, 3}, 10) > 0)
    _assert("ndcg_at_10_empty_gt", abs(_ndcg_at_k([1, 2, 3], set(), 10)) < 1e-6)

    # Test 7: Migration on temp DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db = tf.name

    try:
        import sqlite_vec
        conn = sqlite3.connect(temp_db)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Create minimal schema
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, content TEXT, summary TEXT, is_archived INTEGER DEFAULT 0)")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768])")
        conn.commit()

        # Insert test data
        conn.execute("INSERT INTO memories (id, content, is_archived) VALUES (1, 'test memory one', 0)")
        conn.execute("INSERT INTO memories (id, content, is_archived) VALUES (2, 'test memory two', 0)")
        conn.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (1, ?)", (serialize_f32([0.1] * 768),))
        conn.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (2, ?)", (serialize_f32([0.2] * 768),))
        conn.commit()
        conn.close()

        # Test migration
        class FakeArgs:
            db_path = temp_db
            batch_size = 2
        fake_args = FakeArgs()

        migrate_result = cmd_migrate(fake_args)
        _assert("migration_applies", migrate_result["status"] in ("success", "already-applied"))

        # Test re-embed on temp DB
        re_embed_result = cmd_re_embed(fake_args)
        _assert("re_embed_completes", re_embed_result.get("status", "").startswith("complete") or
                re_embed_result.get("status") == "success")

        # Verify v2 table has data
        conn = _get_conn(temp_db)
        v2_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings_v2").fetchone()[0]
        _assert("v2_has_embeddings", v2_count == 2, f"Expected 2, got {v2_count}")

        # Verify v2 embeddings are 1024-dim
        row = conn.execute("SELECT embedding FROM memory_embeddings_v2 WHERE memory_id = 1").fetchone()
        if row:
            dim = len(struct.unpack(f"{JINA_DIM}f", row[0]))
            _assert("v2_embedding_1024_dim", dim == 1024, f"Got dim={dim}")
        else:
            _assert("v2_embedding_1024_dim", False, "No row found")

        # Test vector search on v2
        test_query_vec = encode_jina_query("test memory")
        search_result = conn.execute("""
            SELECT emb.memory_id, emb.distance
            FROM memory_embeddings_v2 AS emb
            WHERE emb.embedding MATCH ?
              AND k = 2
            ORDER BY emb.distance
        """, (test_query_vec,)).fetchall()
        _assert("v2_vector_search_works", len(search_result) == 2,
                f"Expected 2 results, got {len(search_result)}")

        conn.close()

    finally:
        os.unlink(temp_db)

    # Test 8: Swap on temp DB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db2 = tf.name

    try:
        conn = sqlite3.connect(temp_db2)
        conn.enable_load_extension(True)
        import sqlite_vec
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, content TEXT, is_archived INTEGER DEFAULT 0)")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[768])")
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings_v2 USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[1024])")
        # Insert 10 rows in both
        for i in range(1, 11):
            conn.execute("INSERT INTO memories (id, content, is_archived) VALUES (?, ?, 0)", (i, f"mem {i}"))
            conn.execute("INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)", (i, serialize_f32([0.1] * 768)))
            conn.execute("INSERT INTO memory_embeddings_v2 (memory_id, embedding) VALUES (?, ?)", (i, serialize_f32([0.2] * 1024)))
        conn.commit()
        conn.close()

        class FakeArgs2:
            db_path = temp_db2
        swap_result = cmd_swap(FakeArgs2())
        _assert("swap_succeeds", swap_result["status"] == "success")

        # Verify metadata was set
        conn = _get_conn(temp_db2)
        active = conn.execute("SELECT value FROM metadata WHERE key = 'active_embedding_model'").fetchone()
        _assert("swap_sets_active_model", active and active[0] == JINA_MODEL_NAME)
        conn.close()

    finally:
        os.unlink(temp_db2)

    # Summary
    logger.info("\n%d/%d tests passed", results["passed"], results["passed"] + results["failed"])
    results["status"] = "success" if results["failed"] == 0 else "failed"
    return results


# ============================================================
# CLI entry point
# ============================================================

def main():
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
    subparsers.add_parser("re-embed", help="Re-embed all memories")
    subparsers.add_parser("ab-compare", help="Run A/B comparison")
    subparsers.add_parser("swap", help="Swap default to Jina v5")
    subparsers.add_parser("stats", help="Show embedding stats")
    subparsers.add_parser("full", help="Full pipeline (all steps)")
    subparsers.add_parser("test", help="Run unit tests")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

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
    if handler:
        result = handler(args)
        print(json.dumps(result, indent=2))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
