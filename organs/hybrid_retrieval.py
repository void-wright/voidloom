"""Hybrid Retrieval for Wizard Library L2 Component 1.

A stand-alone module that adds:

    FTS5 BM25 keyword search
      +
    sqlite-vec cosine vector search (existing)
      +
    Reciprocal Rank Fusion (RRF)
      +
    Qwen3-Reranker-0.6B reranking (default; BAAI/bge-reranker-v2-m3 selectable
    fallback)

to the Claude narrative memory system. Designed to be imported by the patched
``claude_memory.py``; can also be exercised stand-alone via the CLI smoke
test at the bottom of this file.

Public API
----------
``fts_match(conn, query, top_k=50) -> List[Tuple[int, float]]``
    Keyword search via SQLite FTS5. Returns ``[(memory_id, neg_bm25_score)]``
    where higher scores are better matches (we negate FTS5's lower-is-better
    bm25() values so the downstream RRF treats it consistently with vector
    cosine scores).

``rrf_fuse(*ranked_lists, k=15, top_k=30) -> List[Tuple[int, float]]``
    Reciprocal Rank Fusion. Parameter-free combination of N ranked lists,
    robust to score-scale mismatches. ``k=15`` is the production sweet spot
    for corpora <10K docs (per Cormack 2009 + Microsoft Bing defaults +
    Phase 0 memory #3812).

``reranker_gate(confirm=True) -> dict``
    THE reranker switch (handoff round 1, owner ruling 2026-10-14): the reranker runs ONLY when a non-CPU
    accelerator is present AND the machine has >= 15.0 GiB of RAM as the OS presents it; never on CPU-only. The
    config (``reranker_enabled``, ``VOIDLOOM_RERANKER=off``, ``--no-rerank``) can only turn it OFF. Every path that
    could load a reranker model passes ``reranker_score``, which asks this gate first.

``reranker_score(conn, query, candidate_ids, top_k=10) -> List[Tuple[int, float]]``
    Reranking with ``Qwen/Qwen3-Reranker-0.6B`` (THE canonical reranker, a causal-LM
    reranker in the sibling ``qwen_reranker`` module) and a guaranteed fallback
    to the ``BAAI/bge-reranker-v2-m3`` CrossEncoder (568M, Apache-2.0) on any
    Qwen failure. Both read ``(query, candidate_text)`` together and produce
    fine-grained relevance scores — meaningfully better than embedding cosine
    for exact-term + technical-jargon ranking. Lazy-loaded + cached for process
    lifetime; fails gracefully to RRF-order on any model error. Force the bge
    backend with ``VOIDLOOM_RERANKER=bge``.

``recall_hybrid(conn, query, query_vec, top_k=10, mode='hybrid', ...) -> List[dict]``
    Orchestrates the 3-stage pipeline. Returns list of memory dicts (id,
    content, summary, category, importance, tags, distance, retrieval_path,
    ...) ready for the caller (``claude_memory.cmd_recall``) to emit as JSON.

``apply_fts5_migration(db_path, migration_sql_path) -> dict``
    One-shot idempotent migration runner. Backs the DB up, applies the
    migration SQL, verifies row-count match.

Mode values for ``recall_hybrid``::

    'hybrid'      : full 3-stage pipeline (default)
    'vector-only' : skip FTS + RRF + rerank — pure cosine baseline
    'bm25-only'   : skip vector + RRF + rerank — pure keyword baseline
    'no-rerank'   : vector + FTS + RRF, skip rerank (faster, lower quality)

A ``hybrid`` recall on a machine the gate turns off returns exactly the ``no-rerank`` ranking and says so: its rows
carry ``retrieval_path='no-rerank'``.

All entry points accept ``conn`` explicitly so the caller controls
connection + transaction lifetime (matches the existing
``claude_memory.py`` pattern — every cmd_* opens, uses, and closes its
own connection).

Phase 0 design memories: #3911 (canonical), #3812 (pattern), #3925
(invisible enrichment), #3919 (ACE-loop discipline 9.8/10 HARD).
"""

from __future__ import annotations

import logging
import os
import platform
import re
import sqlite3
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# --- H3 quiet-by-default ------------------------------------------------------
# Parent this module's logger under "voidloom" so reranker-load INFO inherits the
# engine's quiet-by-default level (WARNING+, no handler => silent) plus the
# VOIDLOOM_LOG_LEVEL / VOIDLOOM_LOG_FILE opt-ins set up in claude_memory.py. This
# module never calls logging.basicConfig (it must not attach a handler to root on
# the recall hot path), so a successful recall emits nothing to stderr.
logger = logging.getLogger("voidloom.hybrid_retrieval")


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


def _reranker_offline_ok(repo_id: str) -> bool:
    """H11: decide whether to load the reranker with local_files_only=True.

    Honor an explicit offline request (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE),
    else auto-enable offline only when the model is already in the local HF
    cache (warm cache => no hub round-trip). A cold machine (no cache) still
    downloads on first call.
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


def _quiet_reranker_load_noise() -> None:
    """H3: keep a successful reranker load quiet on default stderr.

    The version-skew line ("You are trying to use a model that was created with
    Sentence Transformers version 5.1.2, but you're currently using version
    4.1.0…") is emitted via ``logger.warning`` on the sentence_transformers
    logger, so on the quiet recall path (effective level > INFO) we must raise
    that logger ABOVE warning (to ERROR) to suppress it; we also filter the
    ``warnings.warn`` variants (torch_dtype deprecation, any UserWarning-phrased
    skew). When VOIDLOOM_LOG_LEVEL opts into INFO/DEBUG the breadcrumbs stay.
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
        # H3: silence huggingface_hub's own "Fetching N files" tqdm bar (separate
        # from the ST loggers). The env var only sets the import-time default, so
        # also disable at runtime (idempotent); INFO/DEBUG opt-in leaves it.
        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        try:
            from huggingface_hub.utils import disable_progress_bars
            disable_progress_bars()
        except Exception:
            pass

# ============================================================
# Configuration constants
# ============================================================

# The reranker's device is the C1 resolver's answer (_torch_device). On CUDA the pin (CUDA_VISIBLE_DEVICES, set
# before torch by claude_memory.py) leaves PyTorch one card, addressed as cuda:0.
# The active embedding model (jina-embeddings-v5-text-small, 1024-dim, ~1.3GB)
# already lives there; the reranker (Qwen3-Reranker-0.6B by default, ~1.2GB, or
# the bge-reranker-v2-m3 fallback ~1.1GB FP16) adds ~1GB+ — well within 11GB.
_RERANKER_DEVICE = "cuda:0"   # documentation of the CUDA case only: the device is _torch_device()'s answer
# The bge fallback backend's HF id. The DEFAULT runtime reranker is
# Qwen/Qwen3-Reranker-0.6B (loaded by the sibling qwen_reranker module); this
# constant names the bge CrossEncoder that _load_reranker() loads as the
# guaranteed fallback (and as the VOIDLOOM_RERANKER=bge selectable backend).
_RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
_RERANKER_MAX_LENGTH = 512  # tokens per (query, passage) pair; model handles truncation

# RRF k-parameter. For corpora <10K docs, k=15 is the production sweet spot
# (Cormack 2009 reciprocal rank fusion paper; Microsoft Bing internal default).
# The textbook k=60 is tuned for TREC-scale (millions of docs); applying it to
# our ~4K corpus dilutes the signal. Phase 0 memory #3812 codifies this.
_RRF_K = 15

# Stage budgets. Each stage oversamples to feed the next:
#   Stage 1a (vector): top-50 candidates from sqlite-vec
#   Stage 1b (BM25):   top-50 candidates from FTS5
#   Stage 2  (RRF):    fuse → top-30
#   Stage 3  (rerank): cross-encoder → final top_k (usually 5-10)
_VEC_CANDIDATES = 50
_BM25_CANDIDATES = 50
_RRF_CANDIDATES = 30
# R2-09: how far a leg may be re-fetched (x2 per step) to refill a pool the channel partition left short.
_REFILL_MAX_FACTOR = 8
_RERANK_BATCH_SIZE = 16  # cross-encoder batch size on cuda:0; tuned empirically


# L5-C1 reranker backend selector. Qwen is canonical; whether ANY reranker runs is reranker_gate()'s decision.
# 'qwen' -> the Qwen/Qwen3-Reranker-0.6B causal-LM adapter (qwen_reranker.py) —
#           the DEFAULT as of the 2026-06-11 SOAK decision (Drew). It FALLS BACK
#           to bge on ANY failure (OOM, load, score) so the swap can never break
#           recall. Running Qwen as the everyday reranker accrues lived-in data
#           (latency, fallback frequency, ranking feel) to decide whether Qwen
#           earns a place in the generic-Voidloom GitHub seed's reranker tier.
# 'bge'  -> the BAAI/bge-reranker-v2-m3 CrossEncoder — the proven fallback +
#           the comparison baseline. Force it with VOIDLOOM_RERANKER=bge (e.g.
#           to A/B against Qwen via ab_harness.py).
# Read once at import; an unrecognised value degrades to the 'qwen' default.
def _resolve_reranker_backend() -> str:
    val = (os.environ.get("VOIDLOOM_RERANKER") or "").strip().lower()
    if val in ("qwen", "bge"):
        return val
    return "qwen"


_RERANKER_BACKEND = _resolve_reranker_backend()

# ============================================================
# THE RERANKER GATE (handoff round 1, lane L2; owner ruling 2026-10-14, contract C3)
# ============================================================
# ON only when a non-CPU accelerator is present AND MemTotal >= 15.0 GiB (the RAM a 16 GB machine presents: a 16 GB
# Mac reports 16.0, a 16 GB PC 15.5-15.9 after firmware reservations; 8 and 12 GB machines stay out). NEVER on
# CPU-only. On CPU the reranker was 94% of a ~80 s recall (memory anchor vl_cpu_rerank_hotspot); the gate is the speed
# fix. The same rule, as a fact about the machine, is capability/detect.py's reranker_fits() (the installer's sweep);
# test_reranker_gate.py binds the two together. The live check here is what decides: a wizard-config.json copied from
# another machine, or a stale sweep, cannot switch the reranker on. Only OFF can be configured:
#   VOIDLOOM_RERANKER=off (this process; `voidloom recall/batch-recall --no-rerank` set it), reranker_enabled=false
#   (wizard-config.json, the laptop/teammate profiles, WIZARD_RERANKER_ENABLED=false).
RERANKER_RAM_THRESHOLD_GIB = 15.0
CANONICAL_RERANKER_MODEL = "Qwen/Qwen3-Reranker-0.6B"     # what runs (since L5-C1) and what the installer pre-caches
RERANKER_OFF_ENV = "VOIDLOOM_RERANKER"
_RERANKER_OFF_VALUES = ("off", "none", "0", "false", "no", "disabled")
# The OWNER's switch (docs/ONBOARDING_GUIDE.md "The reranker gate"): a WIZARD_RERANKER line in the env file, outside the
# managed block (the product never writes this key). It can only CLOSE the gate -- the gate's "never on CPU-only, never
# under 15.0 GiB" is the owner's ruling -- and it is read HERE, at recall time (round 2, m5): install.sh read it once, so
# a line added after the install did nothing until install.sh was re-run.
OWNER_RERANKER_KEY = "WIZARD_RERANKER"
_OWNER_OFF_VALUES = ("off", "0", "false", "no")


def reranker_model_name() -> str:
    """The model this process would load: Qwen3-Reranker-0.6B, or bge-reranker-v2-m3 under VOIDLOOM_RERANKER=bge."""
    return _RERANKER_MODEL_NAME if _RERANKER_BACKEND == "bge" else CANONICAL_RERANKER_MODEL


def _organ(name: str):
    try:
        return __import__(name)
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        return __import__(name)


def _resolve_accelerator(confirm: bool, torch_mod=None):
    """C1 (lane L1). A seam so the gate's table can be proven with no device: tests replace this function."""
    return _organ("voidloom_device").resolve_accelerator(confirm=confirm, torch_mod=torch_mod)


def _config_reranker_enabled() -> Tuple[Optional[bool], str]:
    """(reranker_enabled, where it came from) from wizard_config; (None, reason) when the config cannot be read --
    an unreadable config never switches the reranker on, and never off: the live rule decides alone."""
    try:
        wc = _organ("wizard_config")
        cfg = wc.get_config()
        src = "wizard-config (%s profile%s)" % (
            cfg.active_profile, "; WIZARD_RERANKER_ENABLED" if os.environ.get("WIZARD_RERANKER_ENABLED") else "")
        return bool(cfg.reranker_enabled), src
    except Exception as e:  # noqa: BLE001 - the gate never breaks recall
        return None, "wizard-config unreadable (%s)" % type(e).__name__


def _owner_reranker_line() -> Tuple[Optional[str], str]:
    """(value, where) of the owner's WIZARD_RERANKER: the environment (what a unit receives from its EnvironmentFile),
    else the env file read as the units read it (voidloom_device's one reader). (None, "") when absent or unreadable."""
    env = os.environ.get(OWNER_RERANKER_KEY)
    if env is not None and env.strip():
        return env, "the environment"
    try:
        vd = _organ("voidloom_device")
        value, health = vd._read_env_file(OWNER_RERANKER_KEY, lambda v: True)
        if value is not None and str(value).strip():
            return value, str(vd._env_file_path())
    except Exception:  # noqa: BLE001 - an unreadable file never opens or closes anything
        pass
    return None, ""


def reranker_gate(confirm: bool = True, torch_mod=None) -> Dict[str, Any]:
    """THE reranker switch. Returns ``{enabled, why, decided_by, accelerator, torch_device, basis, ram_gib,
    threshold_gib, model, config_enabled, config_source, confirmed}``. Never raises; ``enabled`` is False on any doubt.

    ``confirm=True`` (the recall path) asks torch through the C1 resolver, exactly as the embedder's device was chosen.
    ``confirm=False`` (doctor, the session banner) is stdlib only: no torch import, so it reports what the resolver
    plans; the recall path re-checks live."""
    gate: Dict[str, Any] = {
        "enabled": False, "why": "", "decided_by": "", "accelerator": None, "torch_device": None, "basis": None,
        "ram_gib": None, "threshold_gib": RERANKER_RAM_THRESHOLD_GIB, "model": reranker_model_name(),
        "config_enabled": None, "config_source": None, "confirmed": bool(confirm)}
    off_env = (os.environ.get(RERANKER_OFF_ENV) or "").strip().lower() in _RERANKER_OFF_VALUES
    owner_val, owner_src = _owner_reranker_line()
    owner_off = (owner_val or "").split("#", 1)[0].strip().strip("'\"").lower() in _OWNER_OFF_VALUES
    cfg_on, cfg_src = _config_reranker_enabled()
    gate["config_enabled"], gate["config_source"] = cfg_on, cfg_src
    # Live facts. Asking torch is skipped when the switch is already off -- nothing would load anyway.
    live_confirm = bool(confirm) and not off_env and not owner_off and cfg_on is not False
    try:
        backend, device, basis, _shared, ram = _resolve_accelerator(live_confirm, torch_mod)
    except Exception as e:  # noqa: BLE001
        backend, device, basis, ram = "cpu", "cpu", "degraded:resolver-fault:%s" % type(e).__name__, None
    gate.update(accelerator=backend, torch_device=device, basis=basis, confirmed=live_confirm)
    gib = (ram / float(1 << 30)) if ram else None
    gate["ram_gib"] = round(gib, 2) if gib is not None else None
    if off_env:
        gate.update(why="%s=%s in this process: off" % (RERANKER_OFF_ENV, os.environ.get(RERANKER_OFF_ENV)),
                    decided_by="process")
    elif owner_off:
        gate.update(why="your %s=%s line (%s): off" % (OWNER_RERANKER_KEY, owner_val.strip(), owner_src),
                    decided_by="owner")
    elif cfg_on is False:
        gate.update(why="reranker_enabled is false in %s: off" % cfg_src, decided_by="config")
    elif backend == "cpu":
        gate.update(why="no accelerator (CPU-only): the reranker is never on", decided_by="cpu-only")
    elif gib is None:
        gate.update(why="RAM could not be read: off", decided_by="ram")
    elif gib < RERANKER_RAM_THRESHOLD_GIB:
        gate.update(why="%.2f GiB RAM < %.1f GiB: off" % (gib, RERANKER_RAM_THRESHOLD_GIB), decided_by="ram")
    else:
        gate.update(enabled=True, decided_by="gate",
                    why="%s accelerator and %.2f GiB RAM >= %.1f GiB: on" % (backend, gib, RERANKER_RAM_THRESHOLD_GIB))
    return gate


def _install_record_field(path: Optional[str], key: str) -> Optional[str]:
    """One `key=value` line of an INSTALL_RECORD (contract C4, written by the installer); None when absent."""
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(key + "="):
                    return line[len(key) + 1:].strip() or None
    except OSError:
        return None
    return None


FAILED_HERE = "FAILED-ON-THIS-MACHINE"


def _proof_label_for_stub(backend, recorded_label=None, recorded_backend=None, osname=None, failed_backend=None):
    """Lane R2b's voidloom_device.proof_label_for, verbatim in signature and semantics, for an organ that predates it
    (the integration merge makes this dead code: proof_label() below prefers the organ's own function)."""
    words = ("PROVEN", "PROVEN-BY-PROXY", "SUPPORTED-UNPROVEN", FAILED_HERE)
    rl = (recorded_label or "").strip()
    rb = (recorded_backend or "").strip().lower()
    if rl == FAILED_HERE and (failed_backend or "").strip():
        rb = failed_backend.strip().lower()
    if rl in words and (not rb or rb == (backend or "").strip().lower()):
        return rl
    osname = osname or platform.system()
    return "PROVEN" if (osname == "Linux" and backend in ("cuda", "cpu")) else "SUPPORTED-UNPROVEN"


def _label_fn():
    return getattr(_organ("voidloom_device"), "proof_label_for", None) or _proof_label_for_stub


def code_label(backend: str) -> str:
    """The C4 label the CODE can claim for ``backend`` ON THIS OS (round 2, M2): PROVEN only where Linux proved it --
    a Mac's CPU route is SUPPORTED-UNPROVEN until its CI route job and the friend's preflight are read. The source of
    truth is organs/voidloom_device.proof_label_for (lane R2b) with no record."""
    return _label_fn()(backend, None, None, platform.system(), None)


def accelerator_proof_label(backend: str, install_record: Optional[str] = None) -> Tuple[Optional[str], str]:
    """(label, source) for the accelerator a reader is about to name, through voidloom_device.proof_label_for(backend,
    recorded_label, recorded_backend, osname, failed_backend) -- R2b's one rule: INSTALL_RECORD's ``proof_label`` wins
    only when it is a C4 word FOR THIS BACKEND (an empty value, an Intel Mac's record before m1, is absent).

    After a failed device proof the record holds accelerator=cpu, proof_label=FAILED-ON-THIS-MACHINE and
    accelerator_failed=mps: that label is about the FAILED device, so failed_backend is passed and the reader says
    both -- 'cpu [SUPPORTED-UNPROVEN; mps FAILED-ON-THIS-MACHINE]', never 'cpu [FAILED-ON-THIS-MACHINE]', which said
    the CPU failed (round 2, m2)."""
    rec_label = _install_record_field(install_record, "proof_label")
    rec_acc = _install_record_field(install_record, "accelerator")
    failed = _install_record_field(install_record, "accelerator_failed")
    try:
        label = _label_fn()(backend, rec_label, rec_acc, platform.system(), failed)
        source = "INSTALL_RECORD" if (rec_label and label == rec_label.strip()) else "code"
        if failed and failed.strip().lower() != (backend or "").strip().lower():
            label = "%s; %s %s" % (label, failed.strip(), FAILED_HERE)
            source = "%s + INSTALL_RECORD (failed %s)" % (source, failed.strip())
        return label, source
    except Exception:  # noqa: BLE001 - a label never breaks doctor or the banner
        return None, "unavailable"


def reranker_status(install_record: Optional[str] = None, confirm: bool = False, torch_mod=None) -> Dict[str, Any]:
    """The reranker's state for the people who read it (doctor, the session banner). Stdlib only by default.

    The gate (above) plus: the PROOF LABEL of the accelerator (INSTALL_RECORD's ``proof_label`` when this install
    recorded one -- C4: PROVEN / PROVEN-BY-PROXY / SUPPORTED-UNPROVEN / FAILED-ON-THIS-MACHINE -- else what the code
    can claim for the backend), the installer's stored sweep (C3 ``reranker_gate``) beside the live answer, and
    whether the model is in the local cache -- "on but not cached" is what a RAM upgrade after a gated install
    looks like, and the next recall would download ~1.2 GB."""
    gate = reranker_gate(confirm=confirm, torch_mod=torch_mod)
    st: Dict[str, Any] = dict(gate)
    st["proof_label"], st["proof_label_source"] = accelerator_proof_label(gate.get("accelerator") or "cpu",
                                                                          install_record)
    try:
        st["stored_gate"] = dict(_organ("wizard_config").get_config().reranker_gate or {})
    except Exception:  # noqa: BLE001
        st["stored_gate"] = {}
    # C3: the sweep writes reranker_enabled beside reranker_gate{...}; with no sweep record there is nothing to compare.
    stored_on = gate["config_enabled"] if st["stored_gate"] else None
    st["stored_agrees"] = None if stored_on is None else (bool(stored_on) == bool(gate["enabled"]))
    st["model_cached"] = _reranker_offline_ok(gate["model"]) if gate["enabled"] else None
    notes = []
    if gate["enabled"] and st["model_cached"] is False:
        notes.append("reranker enabled but not cached: the next recall downloads %s" % gate["model"])
    if st["stored_agrees"] is False:
        notes.append("the installer recorded the reranker %s; this machine now says %s (the live answer decides)"
                     % ("on" if stored_on else "off", "on" if gate["enabled"] else "off"))
    st["notes"] = notes
    return st


def reranker_banner_line(st: Dict[str, Any]) -> str:
    """ONE plain line for the session banner: the reranker's state, why, and the accelerator's proof label."""
    state = "ON" if st.get("enabled") else "OFF"
    acc = st.get("accelerator") or "cpu"
    label = st.get("proof_label") or "?"
    line = "reranker %s · %s · %s [%s]" % (state, st.get("why") or "", acc, label)
    if st.get("notes"):
        line += " · " + st["notes"][0]
    return line


# Module-level cache so the reranker only loads once per process.
# Cache value is the loaded model OR None (signals "load failed, don't retry").
_reranker_cache: Dict[str, Any] = {}

# Valid mode strings for `recall_hybrid`.
VALID_MODES = frozenset({"hybrid", "vector-only", "bm25-only", "no-rerank"})


# ============================================================
# FTS5 BM25 keyword search
# ============================================================

def _sanitize_fts_query(query: str) -> str:
    """Make a free-form query safe for the FTS5 MATCH grammar.

    FTS5 MATCH is strict and has gotchas that aren't obvious:

        * Hyphens are interpreted as the NEGATION operator. So ``a-b`` parses
          as ``a -b`` (match a, exclude b), and bare hyphenated identifiers
          like ``bge-reranker-v2-m3`` raise ``no such column: reranker``
          because FTS5 then tries to resolve the post-hyphen tokens as
          column-qualified identifiers.
        * Dots inside numbers (``9.8``, ``v1.5``) raise ``syntax error near
          "."`` because FTS5 wants either a token or an operator there.
        * Boolean keywords ``AND``/``OR``/``NOT``/``NEAR`` only have special
          meaning when uppercase — but operators inside conversational
          queries (e.g., "false positives AND false negatives") will trip
          parsing without warning.

    Strategy:
        1. Strip everything except word chars, whitespace, hyphens, and dots.
        2. Lowercase ``AND``/``OR``/``NOT``/``NEAR`` so they become terms.
        3. Any token containing ``-`` or ``.`` is wrapped in double quotes,
           which makes FTS5 treat it as an exact PHRASE (the tokenizer still
           splits on punctuation internally, so ``"bge-reranker-v2-m3"``
           matches documents containing the sequence "bge reranker v2 m3").
        4. Embedded double-quotes in the source are dropped to keep the
           wrapping unambiguous.

    Empty / whitespace-only queries return an empty string; callers should
    treat that as "no FTS results" rather than raising.

    Examples (cleaned → final)::

        "bge-reranker-v2-m3"        → '"bge-reranker-v2-m3"'
        "sqlite-vec library"        → '"sqlite-vec" library'
        "memory #3868"              → 'memory 3868'
        "what's 9.8/10?"            → '"9.8" 10'
        "claude_memory.py recall"   → '"claude_memory.py" recall'
        "Phase 8 deprecation"       → 'Phase 8 deprecation'
        "false-positives AND ..."   → '"false-positives" and ...'
    """
    # 1. Drop everything that isn't word/space/hyphen/dot. Also drop embedded
    #    double-quotes — we wrap multi-segment tokens ourselves below.
    cleaned = re.sub(r'["]', " ", query)
    cleaned = re.sub(r"[^\w\s.\-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""

    tokens: List[str] = []
    for tok in cleaned.split():
        # A token with no word character ("." or "-" standing alone, "--") is an FTS5 syntax error, not a term: the
        # whole BM25 channel used to fail and the recall silently fell back to vectors only.
        if not re.search(r"\w", tok):
            continue
        # Lowercase the FTS5 boolean keywords so they're treated as ordinary terms.
        if tok.upper() in {"AND", "OR", "NOT", "NEAR"}:
            tokens.append(tok.lower())
            continue
        # Wrap any multi-segment token (contains hyphen or dot) in quotes
        # so FTS5 treats it as a phrase, not as a column ref or NEGATION.
        if ("-" in tok or "." in tok) and tok not in {"-", "."}:
            # Strip leading/trailing punctuation that would be inside the quotes.
            stripped = tok.strip(".-")
            if stripped:
                tokens.append(f'"{stripped}"')
        else:
            tokens.append(tok)
    return " ".join(tokens)


def fts_match(
    conn: sqlite3.Connection,
    query: str,
    top_k: int = _BM25_CANDIDATES,
    exclude_archived: bool = True,
) -> List[Tuple[int, float]]:
    """BM25 keyword search via FTS5. Returns ``[(memory_id, neg_bm25_score)]``.

    FTS5's ``bm25()`` returns values where *lower magnitude is better* (it's
    a relevance distance). We negate so higher = better, consistent with the
    cosine search path. RRF only cares about rank order, so sign convention
    is for readability — it doesn't change fused output.

    Args:
        conn: sqlite3 connection with FTS5 extension (built into sqlite3).
        query: free-text query string. Will be sanitised for FTS5 grammar.
        top_k: max results.
        exclude_archived: if True (default), filter out is_archived=1
            memories at the JOIN step. Matches ``cmd_recall``'s behaviour.

    Returns:
        ``[(memory_id, score)]`` sorted by score descending (best first).
        Empty list if the sanitised query has no tokens, FTS5 table is
        missing, or no matches found.

    Raises:
        Does not raise; logs warning + returns [] on OperationalError so
        callers can fall back to vector-only without try/except boilerplate.
    """
    sanitized = _sanitize_fts_query(query)
    if not sanitized:
        return []

    sql = """
        SELECT f.rowid, bm25(memories_fts) AS score
        FROM memories_fts AS f
    """
    if exclude_archived:
        sql += """
        JOIN memories AS m ON m.id = f.rowid
        WHERE memories_fts MATCH ? AND m.is_archived = 0
        """
    else:
        sql += "WHERE memories_fts MATCH ?\n"
    sql += "ORDER BY bm25(memories_fts) LIMIT ?"

    try:
        rows = conn.execute(sql, (sanitized, top_k)).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning("FTS5 query failed (sanitized=%r): %s", sanitized, e)
        return []

    # Invert bm25 sign so higher=better.
    return [(int(rowid), -float(score)) for rowid, score in rows]


# ============================================================
# Reciprocal Rank Fusion
# ============================================================

def rrf_fuse(
    *ranked_lists: Sequence[Tuple[int, float]],
    k: int = _RRF_K,
    top_k: int = _RRF_CANDIDATES,
) -> List[Tuple[int, float]]:
    """Combine N ranked lists via Reciprocal Rank Fusion.

    Each input list is ``[(memory_id, score)]`` sorted by score descending
    (best first). **Score VALUES are ignored — only RANK matters.** This is
    the key strength of RRF: it doesn't care that BM25 scores live in
    [-15, 0] and cosine distances live in [0, 2]; it only sees rank order.

    Formula::

        score(d) = sum over rankers r of  1 / (k + rank_r(d))

    where ``rank`` starts at 0 (best result) and ``k`` controls how steeply
    the contribution decays. ``k=15`` is the production sweet spot for
    corpora <10K docs.

    Args:
        *ranked_lists: 1+ ranked lists. Each ``[(memory_id, score)]`` with
            best result first. Different lists can have different lengths
            and can overlap or be disjoint.
        k: RRF decay constant. Default 15. Use higher k (60+) for
            TREC-scale corpora.
        top_k: maximum fused results to return.

    Returns:
        ``[(memory_id, rrf_score)]`` sorted by rrf_score descending, up to
        ``top_k`` results. Memories appearing in multiple lists are summed.
        Memories appearing in zero lists are not in the output.

    Examples:
        >>> a = [(1, 0.9), (2, 0.8), (3, 0.7)]
        >>> b = [(2, 5.0), (4, 4.0), (1, 3.0)]
        >>> rrf_fuse(a, b, k=15)
        [(2, 0.12849...), (1, 0.12353...), (3, 0.05882...), (4, 0.0625...)]
    """
    scores: Dict[int, float] = {}
    for results in ranked_lists:
        for rank, (mem_id, _orig_score) in enumerate(results):
            scores[mem_id] = scores.get(mem_id, 0.0) + 1.0 / (k + rank)

    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused[:top_k]


# ============================================================
# bge CrossEncoder reranker (the fallback backend + VOIDLOOM_RERANKER=bge)
# Default runtime reranker is Qwen3-Reranker-0.6B — see _qwen_rerank below.
# ============================================================

def _load_reranker(force_reload: bool = False):
    """Lazy-load + cache the bge-reranker-v2-m3 CrossEncoder (the fallback).

    This is the GUARANTEED-fallback / VOIDLOOM_RERANKER=bge backend; the default
    runtime reranker is Qwen3-Reranker-0.6B (``_qwen_rerank``). Returns the
    loaded ``CrossEncoder`` on success, or ``None`` on failure. On failure,
    ``None`` is cached so subsequent calls don't retry the expensive
    download/load. Set ``force_reload=True`` to retry (e.g., if GPU comes back
    online).

    The model is ~1.1GB on disk and loads in ~7s warm from HF cache.
    """
    if not force_reload and "model" in _reranker_cache:
        return _reranker_cache["model"]
    try:
        from sentence_transformers import CrossEncoder
        import torch
        _quiet_reranker_load_noise()  # H3: load INFO + ST version warning off default stderr
        device = _torch_device(torch)
        # H11 offline: warm cache => local-only (no HF "Fetching N files");
        # honor HF_HUB_OFFLINE explicitly; cold machine still downloads on first
        # call. `local_files_only` is a first-class CrossEncoder param (it flows
        # to the underlying HF from_pretrained for both model + tokenizer).
        offline = _reranker_offline_ok(_RERANKER_MODEL_NAME)
        model = CrossEncoder(
            _RERANKER_MODEL_NAME,
            device=device,
            max_length=_RERANKER_MAX_LENGTH,
            local_files_only=offline,
        )
        _reranker_cache["model"] = model
        logger.info("Loaded reranker %s on %s", _RERANKER_MODEL_NAME, device)
        return model
    except Exception as e:
        logger.warning(
            "Reranker load failed: %s — falling back to RRF-only mode for the rest of this process",
            e,
        )
        _reranker_cache["model"] = None
        return None


def _qwen_rerank(
    query: str,
    ordered_ids: List[int],
    id_to_text: Dict[int, str],
    top_k: int,
    temporal_status: Optional[Dict[int, dict]] = None,
) -> Optional[List[Tuple[int, float]]]:
    """L5-C1: score the candidate pool with the Qwen3-Reranker-0.6B causal-LM
    adapter. Returns ``[(id, score)]`` sorted desc, or ``None`` on ANY
    failure/absence so the caller falls back to bge. NEVER raises.

    L5-C2: when ``temporal_status`` is given (the trust-filtered superseded map), a
    "rank current above superseded" instruction is passed to the judge and a
    deterministic confidence-scaled penalty is applied to the superseded ids'
    scores. ``id_to_text`` is already tag-decorated by the caller."""
    try:
        import qwen_reranker  # sibling module; lazy import so absence is graceful.
    except Exception as e:  # noqa: BLE001
        logger.warning("Qwen reranker import failed: %s — falling back to bge", e)
        return None
    try:
        docs = [id_to_text[mid] for mid in ordered_ids]
        instruction = None
        if temporal_status:
            try:
                import temporal_rerank
                instruction = temporal_rerank.temporal_instruction()
            except Exception:  # noqa: BLE001 -- instruction is best-effort.
                instruction = None
        scores = qwen_reranker.score_pairs(query, docs, instruction=instruction)
        if scores is None:
            return None
        scored = list(zip(ordered_ids, [float(s) for s in scores]))
        if temporal_status:
            try:
                import temporal_rerank
                scored = temporal_rerank.apply_score_penalty(scored, temporal_status)
            except Exception:  # noqa: BLE001 -- penalty is best-effort; raw scores stand.
                scored.sort(key=lambda x: x[1], reverse=True)
        else:
            scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
    except Exception as e:  # noqa: BLE001
        logger.warning("Qwen reranker scoring failed: %s — falling back to bge", e)
        return None


# L5-C2 temporal-aware reranking: down-rank trustworthy-superseded content.
# Default-ON; disable per-process with VOIDLOOM_NO_TEMPORAL_RERANK (or the
# --no-temporal-rerank CLI flag, which sets that env). Kill-switch read at CALL
# TIME (mirrors the PPR/diversity env checks), so the flag works regardless of
# import order. When off/absent/failed, reranker_score is byte-identical to C1.
def _temporal_rerank_disabled() -> bool:
    val = (os.environ.get("VOIDLOOM_NO_TEMPORAL_RERANK") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _temporal_status(conn: "sqlite3.Connection", ids: List[int]) -> Dict[int, dict]:
    """Trust-filtered superseded status for ``ids`` (L5-C2). Returns
    ``{id: {superseded_at, confidence}}`` for the trustworthy-superseded subset,
    or ``{}`` when disabled / absent / failed. NEVER raises."""
    if _temporal_rerank_disabled():
        return {}
    try:
        import temporal_rerank  # sibling module; lazy import so absence is graceful.
    except Exception as e:  # noqa: BLE001
        logger.debug("temporal_rerank import failed: %s — temporal reranking off", e)
        return {}
    try:
        if not temporal_rerank.is_temporal_available(conn):
            return {}
        return temporal_rerank.superseded_status(conn, ids)
    except Exception as e:  # noqa: BLE001
        logger.warning("temporal status failed: %s — no temporal decoration", e)
        return {}


def reranker_score(
    conn: sqlite3.Connection,
    query: str,
    candidate_ids: Sequence[int],
    top_k: int = 10,
) -> List[Tuple[int, float]]:
    """Re-score candidates with the reranker: Qwen3-Reranker-0.6B (canonical), bge-reranker-v2-m3 the fallback.

    THE GATE FIRST (handoff round 1): when ``reranker_gate()`` is off, no model is loaded and the candidates come
    back in their input (RRF) order -- this function is the only door to either reranker model.

    Cross-encoders attend over (query, passage) together — meaningfully
    better than bi-encoder embedding similarity for fine-grained ranking,
    especially on exact-term + scientific-jargon queries.

    Args:
        conn: sqlite3 connection.
        query: free-text query.
        candidate_ids: ranked list of memory IDs (best first) to re-score.
        top_k: max results to return after reranking.

    Returns:
        ``[(memory_id, rerank_score)]`` sorted by rerank_score descending.
        Higher = more relevant.

    Failure mode:
        If the reranker model fails to load or `predict` raises, returns
        candidates in their input order with synthetic scores (preserves
        the RRF ranking — graceful degradation).
    """
    if not candidate_ids:
        return []

    candidate_ids = list(candidate_ids)  # accept tuples / generators

    # ---- THE GATE (owner ruling 2026-10-14): off -> no model load, RRF order kept. ----
    if not reranker_gate()["enabled"]:
        return [(mid, 1.0 / (i + 1)) for i, mid in enumerate(candidate_ids[:top_k])]

    # Pull candidate text in a single query (avoid N round-trips).
    placeholders = ",".join("?" * len(candidate_ids))
    rows = conn.execute(
        f"SELECT id, summary, content FROM memories WHERE id IN ({placeholders})",
        candidate_ids,
    ).fetchall()
    id_to_text = {
        row[0]: f"{row[1] or ''}\n{row[2] or ''}".strip()
        for row in rows
    }

    # Preserve candidate_ids order (so ties downstream feel deterministic).
    ordered_ids: List[int] = [mid for mid in candidate_ids if id_to_text.get(mid)]

    if not ordered_ids:
        return []

    # ---- L5-C2 temporal-aware reranking: decorate superseded candidates. ----
    # Trust-filtered (high-confidence supersedes only; the conf-0.24 heuristic
    # poison band is excluded). The tag is the SOFT steer both backends weigh; the
    # status also drives the Qwen instruction + the deterministic penalty below.
    # When off/absent/failed, temporal_status is {} and id_to_text is unchanged ->
    # byte-identical to the C1 path.
    temporal_status = _temporal_status(conn, ordered_ids)
    if temporal_status:
        try:
            import temporal_rerank
            id_to_text = temporal_rerank.decorate_docs(id_to_text, temporal_status)
        except Exception as e:  # noqa: BLE001 -- decoration is best-effort.
            logger.warning("temporal decorate_docs failed: %s — undecorated text", e)

    # ---- L5-C1: Qwen first (the canonical backend; VOIDLOOM_RERANKER=bge skips it). ----
    # On any failure/absence this returns None and we fall through to the bge
    # CrossEncoder path below (the guaranteed fallback — the swap can never
    # break recall).
    if _RERANKER_BACKEND == "qwen":
        qwen_scored = _qwen_rerank(query, ordered_ids, id_to_text, top_k, temporal_status)
        if qwen_scored is not None:
            return qwen_scored
        logger.info("Qwen reranker unavailable/failed — falling back to bge CrossEncoder")

    # ---- bge CrossEncoder path (the fallback, and the backend under VOIDLOOM_RERANKER=bge). ----
    model = _load_reranker()
    if model is None:
        # Graceful degradation: preserve input order, synthesise descending scores.
        return [(mid, 1.0 / (i + 1)) for i, mid in enumerate(ordered_ids[:top_k])]

    pairs: List[Tuple[str, str]] = [(query, id_to_text[mid]) for mid in ordered_ids]
    try:
        scores = model.predict(pairs, batch_size=_RERANK_BATCH_SIZE)
    except Exception as e:
        logger.warning("Reranker predict() failed: %s — fallback to RRF order", e)
        return [(mid, 1.0 / (i + 1)) for i, mid in enumerate(ordered_ids[:top_k])]

    scored = list(zip(ordered_ids, [float(s) for s in scores]))
    # ---- L5-C2: deterministic confidence-scaled penalty on superseded ids. ----
    if temporal_status:
        try:
            import temporal_rerank
            scored = temporal_rerank.apply_score_penalty(scored, temporal_status)
        except Exception as e:  # noqa: BLE001 -- penalty is best-effort; raw scores stand.
            logger.warning("temporal score penalty failed: %s — raw scores", e)
            scored.sort(key=lambda x: x[1], reverse=True)
    else:
        scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


# ============================================================
# Orchestrator: vector + BM25 + RRF + rerank pipeline
# ============================================================

# ROUND GAMMA (A7): the fallback is a NAMED CONSTANT beside the resolver, not a
# literal buried in an except branch. It points at the table the brain is
# currently written to, so a metadata failure degrades toward the LIVE tier.
_LIVE_EMB_FALLBACK = "memory_embeddings_v2"


def _active_embedding_table(conn: sqlite3.Connection) -> str:
    """Resolve the active vec table (two-tier embedding, Council #4258).

    Reads metadata.active_embedding_table; defaults to memory_embeddings (Nomic).
    Keeps the recall side on the SAME tier the store side wrote to.
    """
    # ROUND GAMMA (A7): one resolver, and the default is EXPLICIT AT THE CALL SITE.
    # The old body's `except: pass -> "memory_embeddings"` fails toward the table
    # retired 2026-05-28, which is architecture-009 inverted: the refusing value
    # must be the default, and the dormant table is the opposite of refusing.
    #
    # This is a RECALL HOT PATH, so a hard raise would turn degraded recall into
    # no recall. The compromise is deliberate and is not silence: resolve through
    # embed_resolver, and when it refuses, log at ERROR and fall back to the table
    # the brain is CURRENTLY WRITTEN TO, never to the dormant one.
    import sys as _s, os as _o
    _d = _o.path.dirname(_o.path.abspath(__file__))
    if _d not in _s.path:
        _s.path.insert(0, _d)
    try:
        import embed_resolver
        return embed_resolver.resolve(conn)[0]
    except Exception as e:
        logger.error(
            "hybrid_retrieval: embedding tier UNRESOLVED (%s: %s); the vector arm "
            "falls back to %r. Lexical retrieval is unaffected.",
            type(e).__name__, e, _LIVE_EMB_FALLBACK,
        )
        return _LIVE_EMB_FALLBACK


def _vector_search(
    conn: sqlite3.Connection,
    query_vec: bytes,
    top_k: int,
    exclude_archived: bool = True,
) -> List[Tuple[int, float]]:
    """Run sqlite-vec cosine search. Returns ``[(memory_id, neg_distance)]``.

    Negates the distance so higher = better, consistent with FTS path.
    Filters out archived memories at the JOIN step (matches cmd_recall).
    Queries the ACTIVE embedding table (Nomic 768 or Jina v2 1024) so the
    recall side always matches whatever tier the store side wrote to.
    """
    emb_table = _active_embedding_table(conn)
    sql = f"""
        SELECT emb.memory_id, emb.distance
        FROM {emb_table} AS emb
    """
    if exclude_archived:
        sql += """
        JOIN memories AS m ON m.id = emb.memory_id
        WHERE emb.embedding MATCH ?
          AND k = ?
          AND m.is_archived = 0
        """
    else:
        sql += "WHERE emb.embedding MATCH ? AND k = ?\n"
    sql += "ORDER BY emb.distance"

    try:
        rows = conn.execute(sql, (query_vec, top_k)).fetchall()
    except sqlite3.OperationalError as e:
        logger.error("Vector search failed: %s", e)
        return []
    return [(int(mid), -float(dist)) for mid, dist in rows]


def _arms_path(mode: str, vector_results: list, bm25_results: list) -> str:
    """Which arms ACTUALLY produced rows — not which mode was asked for.

    CRUCIBLE-3 D6-F1 (LIES). ``retrieval_path`` was a LITERAL passed by the mode
    and never derived from which leg produced the row, so every result on every
    install was labelled ``"hybrid"`` even when the vector arm had returned
    nothing. The arm's death is a ``logger.error`` and an empty list (``:694``);
    nothing above it was told. Measured on one brain: ``--vector-only`` -> 0
    results, ``--bm25-only`` -> 3, default -> 3 results all labelled "hybrid",
    four of which have no row in the active embedding table at all and therefore
    could not have come from the vector arm.

    The repair stays inside the EXISTING vocabulary — ``hybrid`` / ``bm25-only``
    / ``vector-only`` / ``no-rerank`` — because those words are already correct
    and already humanized by ``recall_explain``. Inventing a new token would
    change a value consumers read, and adding a truthful field beside the
    untrue one would just be a field written by one site and read by none.
    """
    if bm25_results and not vector_results:
        return "bm25-only"
    if vector_results and not bm25_results:
        return "vector-only"
    return mode


def recall_hybrid(
    conn: sqlite3.Connection,
    query: str,
    query_vec: bytes,
    top_k: int = 10,
    mode: str = "hybrid",
    vec_candidates: int = _VEC_CANDIDATES,
    bm25_candidates: int = _BM25_CANDIDATES,
    rrf_candidates: int = _RRF_CANDIDATES,
    rrf_k: int = _RRF_K,
    exclude_archived: bool = True,
    divert: Optional[Callable[[List[int]], Set[int]]] = None,
    diverted_out: Optional[List[int]] = None,
) -> List[Dict[str, Any]]:
    """Orchestrate the 3-stage hybrid retrieval pipeline.

    CHANNELLED RECALL (the dream fix, A3): ``divert`` names the ids that belong to a side channel (recall_channels);
    they are PARTITIONED OUT after RRF fusion and BEFORE the reranker -- the reranker scores fewer pairs, never more,
    and the memory lane holds memories only. In the single-leg modes the same partition runs before truncation.
    The diverted ids are appended to ``diverted_out`` when given. ``divert=None`` is byte-identical to before.

    Args:
        conn: sqlite3 connection (sqlite-vec loaded, FTS5 migration applied).
        query: free-text query.
        query_vec: serialised Nomic query embedding (output of ``_encode``
            with prefix ``search_query`` — same bytes format as
            ``memory_embeddings.embedding``).
        top_k: number of final results to return.
        mode: one of ``VALID_MODES``.
        vec_candidates / bm25_candidates: pool size per Stage 1 retriever.
        rrf_candidates: pool size after Stage 2 RRF fusion.
        rrf_k: RRF k-parameter (default 15).
        exclude_archived: if True, archived memories never appear.

    Returns:
        List of memory dicts with keys::

            id, content, summary, category, importance, source_project,
            access_count, created_at, tags, distance, score,
            retrieval_path (str — which mode produced this result)

        ``distance`` is provided for backward compatibility with cmd_recall
        consumers; it's the negated score so lower=better (vector-style).
        ``score`` is the raw pipeline score (higher=better).

    Raises:
        ValueError if ``mode`` is not in ``VALID_MODES``.

    Failure modes (graceful degradation):
        * FTS5 query fails → hybrid mode falls through to vector-only
          behaviour for that query
        * Vector search fails → hybrid mode falls through to BM25 + rerank
        * Reranker load/predict fails → hybrid mode returns RRF-fused order
        * All-empty (both stages return zero) → returns []
    """
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid mode={mode!r}; valid: {sorted(VALID_MODES)}"
        )

    # R2-09 (Round 3 G2): the partition takes the channel rows OUT of a fixed-size pool, so a recall whose pool held
    # dreams or playbook candidates came back SHORT (measured on the owner's brain: bm25-only 17 of 17 adversarial
    # queries short, some empty). With a divert, each leg is REFILLED -- re-fetched at twice the size, up to
    # _REFILL_MAX_FACTOR x -- until the partitioned pool holds what the stage needs or the corpus has no more; the
    # fused pool is cut to ``rrf_candidates`` AFTER the partition, never before. ``divert=None``: byte-identical.
    def _vec(n: int) -> List[Tuple[int, float]]:
        return _vector_search(conn, query_vec, n, exclude_archived=exclude_archived)

    def _bm25(n: int) -> List[Tuple[int, float]]:
        return fts_match(conn, query, top_k=n, exclude_archived=exclude_archived)

    # ---- Stage 1a: vector search ----
    vector_results: List[Tuple[int, float]] = []
    if mode in {"hybrid", "vector-only", "no-rerank"}:
        vector_results = _vec(vec_candidates)
        if mode == "vector-only" and not vector_results:
            return []

    # ---- Stage 1b: BM25 keyword search ----
    bm25_results: List[Tuple[int, float]] = []
    if mode in {"hybrid", "bm25-only", "no-rerank"}:
        bm25_results = _bm25(bm25_candidates)
        if mode == "bm25-only" and not bm25_results:
            return []

    def _partition(pairs: List[Tuple[int, float]]) -> List[Tuple[int, float]]:
        if divert is None or not pairs:
            return pairs
        try:
            gone = divert([mid for mid, _s in pairs]) or set()
        except Exception as e:  # noqa: BLE001 - a channel predicate never breaks recall
            logger.warning("channel partition failed (%s); the memory lane is unpartitioned", e)
            return pairs
        if diverted_out is not None:
            diverted_out.extend(mid for mid, _s in pairs if mid in gone and mid not in diverted_out)
        return [(mid, sc) for mid, sc in pairs if mid not in gone]

    def _refilled(fetch, first: List[Tuple[int, float]], n0: int, want: int) -> List[Tuple[int, float]]:
        """One leg, partitioned, re-fetched at 2x (to _REFILL_MAX_FACTOR x) while it is short of ``want``."""
        got, n = first, n0
        kept = _partition(got)
        while (divert is not None and len(kept) < want and len(got) >= n
               and n < n0 * _REFILL_MAX_FACTOR):
            n *= 2
            got = fetch(n)
            kept = _partition(got)
        return kept

    # ---- Single-source modes return early ----
    if mode == "vector-only":
        scored = _refilled(_vec, vector_results, vec_candidates, top_k)[:top_k]
        return _hydrate_memory_rows(conn, scored, retrieval_path="vector-only")
    if mode == "bm25-only":
        scored = _refilled(_bm25, bm25_results, bm25_candidates, top_k)[:top_k]
        return _hydrate_memory_rows(conn, scored, retrieval_path="bm25-only")

    # ---- Stage 2: RRF fusion ----
    # Handle one-leg-empty cases. If both legs are empty, return [].
    legs: List[List[Tuple[int, float]]] = []
    if vector_results:
        legs.append(vector_results)
    if bm25_results:
        legs.append(bm25_results)
    if not legs:
        return []
    if divert is None:
        fused = rrf_fuse(*legs, k=rrf_k, top_k=rrf_candidates)
        if not fused:
            return []
    else:
        # ---- Stage 2b: the channel partition (after RRF, BEFORE the reranker), with the refill ----
        nv, nb = vec_candidates, bm25_candidates
        while True:
            fused_all = rrf_fuse(*legs, k=rrf_k, top_k=len(vector_results) + len(bm25_results))
            fused = _partition(fused_all)[:rrf_candidates]
            exhausted = len(vector_results) < nv and len(bm25_results) < nb
            if (len(fused) >= rrf_candidates or exhausted
                    or max(nv, nb) >= max(vec_candidates, bm25_candidates) * _REFILL_MAX_FACTOR):
                break
            nv, nb = nv * 2, nb * 2
            if vector_results:
                vector_results = _vec(nv)
            if bm25_results:
                bm25_results = _bm25(nb)
            legs = [l for l in (vector_results, bm25_results) if l]
        if not fused:
            return []

    # ---- no-rerank mode returns RRF results directly ----
    if mode == "no-rerank":
        scored = fused[:top_k]
        return _hydrate_memory_rows(
            conn, scored,
            retrieval_path=_arms_path("no-rerank", vector_results, bm25_results))

    # ---- Stage 3: rerank -- through the gate (reranker_score asks it first and loads nothing when it is off) ----
    fused_ids = [mid for mid, _score in fused]
    reranked = reranker_score(conn, query, fused_ids, top_k=top_k)
    if not reranker_gate()["enabled"]:
        # The gate kept RRF order; give the rows their RRF scores and the label that says what ran.
        rrf = dict(fused)
        reranked = [(mid, rrf.get(mid, sc)) for mid, sc in reranked]
        return _hydrate_memory_rows(
            conn, reranked,
            retrieval_path=_arms_path("no-rerank", vector_results, bm25_results))
    return _hydrate_memory_rows(
        conn, reranked,
        retrieval_path=_arms_path("hybrid", vector_results, bm25_results))


def _hydrate_memory_rows(
    conn: sqlite3.Connection,
    scored: List[Tuple[int, float]],
    retrieval_path: str,
) -> List[Dict[str, Any]]:
    """Fetch full memory rows + tags for the given ``[(id, score)]`` list.

    Preserves the input order (which is the ranking we want to surface).
    Sets ``distance`` = ``-score`` for backward compatibility with the
    existing ``cmd_recall`` output shape (where lower distance == better).
    """
    if not scored:
        return []
    ids = [mid for mid, _s in scored]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"""
        SELECT id, content, summary, category, importance,
               source_project, access_count, created_at
        FROM memories
        WHERE id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    by_id = {r[0]: r for r in rows}

    # Tags in one query, grouped by memory_id.
    tags_rows = conn.execute(
        f"SELECT memory_id, tag FROM memory_tags WHERE memory_id IN ({placeholders})",
        ids,
    ).fetchall()
    tags_by_id: Dict[int, List[str]] = {}
    for mid, tag in tags_rows:
        tags_by_id.setdefault(mid, []).append(tag)

    out: List[Dict[str, Any]] = []
    for mem_id, score in scored:
        r = by_id.get(mem_id)
        if r is None:
            # Memory was deleted between scoring and hydration — skip.
            continue
        out.append({
            "id": r[0],
            "content": r[1],
            "summary": r[2],
            "category": r[3],
            "importance": r[4],
            "source_project": r[5],
            "access_count": r[6],
            "created_at": r[7],
            "tags": tags_by_id.get(mem_id, []),
            "distance": round(-score, 4),  # back-compat (lower=better)
            "score": round(score, 6),
            "retrieval_path": retrieval_path,
        })
    return out


# ============================================================
# Migration runner (one-shot, safe to re-run)
# ============================================================

def apply_fts5_migration(
    db_path: str,
    migration_sql_path: str,
    backup_suffix: str = ".pre-hybrid-retrieval.bak",
) -> Dict[str, Any]:
    """Apply ``migration_001_fts5.sql`` to the given DB. Idempotent + safe.

    Workflow:
        1. Backup ``db_path`` → ``db_path + backup_suffix`` (only if missing).
        2. Open connection + load sqlite-vec (required even though migration
           doesn't touch vec0 — schema introspection reads vec0 tables).
        3. Run migration SQL inside its own transaction (the SQL file itself
           wraps everything in BEGIN/COMMIT).
        4. Verify by row-count match: ``memories_fts == memories``.

    Args:
        db_path: path to the SQLite DB.
        migration_sql_path: path to the .sql migration file.
        backup_suffix: appended to db_path to produce backup filename.

    Returns:
        Dict with ``status``, ``steps`` (per-step diagnostics).
        ``status`` is one of ``ok``, ``error``, ``verify-mismatch``.
    """
    import shutil
    result: Dict[str, Any] = {"status": "pending", "steps": {}}
    db = Path(db_path)
    if not db.exists():
        return {"status": "error", "message": f"DB not found: {db_path}"}

    # 1. Backup (only if missing — never overwrite an existing backup).
    backup = db.with_suffix(db.suffix + backup_suffix)
    if not backup.exists():
        shutil.copy2(db, backup)
        result["steps"]["backup"] = {
            "path": str(backup),
            "size_bytes": backup.stat().st_size,
            "note": "created",
        }
    else:
        result["steps"]["backup"] = {
            "path": str(backup),
            "size_bytes": backup.stat().st_size,
            "note": "already-exists",
        }

    # 2. Connect + load sqlite-vec.
    conn = sqlite3.connect(str(db), timeout=60.0)
    conn.enable_load_extension(True)
    try:
        import sqlite_vec
        sqlite_vec.load(conn)
    except Exception as e:
        conn.close()
        return {"status": "error", "message": f"sqlite-vec load failed: {e}"}
    conn.enable_load_extension(False)

    # 3. Run migration SQL.
    try:
        migration_text = Path(migration_sql_path).read_text()
    except Exception as e:
        conn.close()
        return {
            "status": "error",
            "message": f"Could not read migration SQL: {e}",
        }
    try:
        conn.executescript(migration_text)
        result["steps"]["migration"] = "ok"
    except Exception as e:
        result["steps"]["migration"] = {"status": "error", "message": str(e)}
        conn.close()
        result["status"] = "error"
        return result

    # 4. Verify.
    memories_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    fts_count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
    schema_version_row = conn.execute(
        "SELECT value FROM metadata WHERE key='schema_version'"
    ).fetchone()
    result["steps"]["verify"] = {
        "memories_count": memories_count,
        "fts_count": fts_count,
        "match": memories_count == fts_count,
        "schema_version": schema_version_row[0] if schema_version_row else None,
    }
    conn.close()
    result["status"] = "ok" if memories_count == fts_count else "verify-mismatch"
    return result


def is_fts5_migration_applied(conn: sqlite3.Connection) -> bool:
    """Cheap idempotency check — does ``memories_fts`` exist?

    Used by the patched ``cmd_init`` to decide whether to auto-apply the
    migration. Doesn't verify trigger health or row-count match; that's
    the ``apply_fts5_migration`` runner's job.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories_fts'"
    ).fetchone()
    return row is not None


# ============================================================
# Unit tests (run via __main__ tests; cheap correctness checks)
# ============================================================

def unit_test_sanitize_fts_query() -> Dict[str, Any]:
    """Parameter-free correctness checks for ``_sanitize_fts_query``.

    Returns a dict ``{name: bool}`` of test results. No external dependencies.

    Tested contract (the FTS5 gotcha matrix from Phase 0 + spec):

        * Hyphenated identifiers (e.g., ``bge-reranker-v2-m3``) are wrapped in
          double quotes so FTS5 treats them as PHRASE searches (the tokenizer
          still splits the phrase on punctuation internally), rather than
          parsing the hyphens as NEGATION operators.
        * Multi-segment dotted tokens (e.g., ``9.8/10``, ``claude_memory.py``,
          ``v1.5.0``) are wrapped in double quotes for the same reason
          (otherwise the FTS5 parser raises ``syntax error near "."``).
        * The FTS5 boolean keywords ``AND``/``OR``/``NOT``/``NEAR`` only have
          operator semantics when uppercase, so we lowercase them so a
          conversational query containing those words is treated as terms.
        * Punctuation that wasn't covered by the above (``#``, ``/``, ``?``,
          ``!``, etc.) is stripped to whitespace.
        * Empty / whitespace-only / all-symbols-only input returns ``''``
          (caller treats as "no FTS results", not as an error).
        * Embedded double-quotes in the source are dropped to keep the
          wrapping unambiguous.
        * Diacritics are preserved verbatim (the unicode61 tokenizer
          decides what to do with them at MATCH time).

    Cases mirror the 17 spec gotcha cases the Reflector ran independently in
    iter-1, plus a few extra (mixed-case AND, all-symbols, multi-dot,
    naive-diacritic) to lock down the contract.
    """
    cases: List[Tuple[str, str]] = [
        # --- the hyphenated-identifier gotcha (FTS5 NEGATION) ---
        ("bge-reranker-v2-m3", '"bge-reranker-v2-m3"'),
        ("sqlite-vec library", '"sqlite-vec" library'),
        ("a-b", '"a-b"'),
        # --- the multi-dot gotcha (FTS5 syntax error near ".") ---
        ("9.8/10", '"9.8" 10'),
        ("claude_memory.py recall", '"claude_memory.py" recall'),
        ("v1.5.0", '"v1.5.0"'),
        ("a.b", '"a.b"'),
        # --- boolean-keyword lowercasing ---
        (
            "false-positives AND false-negatives",
            '"false-positives" and "false-negatives"',
        ),
        ("And so", "and so"),
        (
            "and AND OR or NOT not NEAR near",
            "and and or or not not near near",
        ),
        # --- non-special chars (no transformation expected beyond hash strip) ---
        ("memory #3868", "memory 3868"),
        ("Phase 8 deprecation", "Phase 8 deprecation"),
        # --- embedded quotes stripped, hyphenated still wrapped ---
        (
            '"bge-reranker-v2-m3" AND "false-positives"',
            '"bge-reranker-v2-m3" and "false-positives"',
        ),
        # --- degenerate / empty cases ---
        ("", ""),
        ("   ", ""),
        ("!@#$%", ""),
        ("...", ""),
        ("---", ""),
        # --- diacritics preserved ---
        ("café résumé naïve", "café résumé naïve"),
        # --- single non-special token round-trip ---
        ("tok", "tok"),
    ]

    results: Dict[str, Any] = {}
    failures: List[Dict[str, str]] = []
    for inp, expected in cases:
        actual = _sanitize_fts_query(inp)
        passed = actual == expected
        # Use the input as the test case name (escaped for JSON readability).
        # Truncate to keep the result dict scannable.
        case_name = f"sanitize::{inp!r}"
        if len(case_name) > 80:
            case_name = case_name[:77] + "..."
        results[case_name] = passed
        if not passed:
            failures.append({
                "input": inp,
                "expected": expected,
                "actual": actual,
            })

    # Roll-up flags for callers.
    results["all_pass"] = len(failures) == 0
    results["failures"] = failures
    return results


def unit_test_rrf_fuse() -> Dict[str, Any]:
    """Parameter-free correctness checks for ``rrf_fuse``.

    Returns a dict ``{name: bool}`` of test results. No external dependencies.

    Tested invariants:
        1. Single-list input returns the same memories in original order
        2. Two lists with one common memory: the common one gets a higher score
        3. Score depends only on rank, not on the original scores
        4. k=15 default vs k=60: smaller k gives sharper differences
        5. Empty input → empty output
        6. top_k respected
        7. Doubled identical list returns memories with 2x score
    """
    results: Dict[str, Any] = {}

    # 1. Single-list pass-through
    a = [(1, 0.9), (2, 0.8), (3, 0.7)]
    out = rrf_fuse(a, k=15, top_k=10)
    results["1_single_list_order_preserved"] = [m for m, _ in out] == [1, 2, 3]

    # 2. Two lists, shared memory bubbles up
    a = [(1, 0.9), (2, 0.8), (3, 0.7)]
    b = [(2, 5.0), (4, 4.0), (1, 3.0)]
    out = rrf_fuse(a, b, k=15, top_k=10)
    # Memory 2: rank 1 in a, rank 0 in b → 1/16 + 1/15 = 0.12916
    # Memory 1: rank 0 in a, rank 2 in b → 1/15 + 1/17 = 0.12549
    # Memory 2 should be first
    results["2_shared_membership_wins"] = out[0][0] == 2

    # 3. Rank-only invariant: score values shouldn't affect rrf score
    a1 = [(1, 0.9), (2, 0.8)]
    a2 = [(1, 0.001), (2, 0.0005)]
    out1 = rrf_fuse(a1, k=15)
    out2 = rrf_fuse(a2, k=15)
    results["3_rank_only_invariant"] = out1 == out2

    # 4. Smaller k = sharper signal
    a = [(1, 0.9), (2, 0.8)]
    b = [(1, 0.9), (2, 0.8)]
    out_k15 = rrf_fuse(a, b, k=15)
    out_k60 = rrf_fuse(a, b, k=60)
    # With smaller k, the rank-0 vs rank-1 gap is wider
    gap_k15 = out_k15[0][1] - out_k15[1][1]
    gap_k60 = out_k60[0][1] - out_k60[1][1]
    results["4_smaller_k_sharper"] = gap_k15 > gap_k60

    # 5. Empty input
    results["5_empty_input"] = rrf_fuse(k=15) == []
    results["5b_all_empty_lists"] = rrf_fuse([], [], k=15) == []

    # 6. top_k respected
    big = [(i, 1.0 / (i + 1)) for i in range(100)]
    out = rrf_fuse(big, k=15, top_k=5)
    results["6_top_k_respected"] = len(out) == 5

    # 7. Doubled list = 2x score
    a = [(1, 0.9)]
    out_single = rrf_fuse(a, k=15)
    out_double = rrf_fuse(a, a, k=15)
    expected_single = 1.0 / 15
    expected_double = 2.0 / 15
    results["7_doubled_2x"] = (
        abs(out_single[0][1] - expected_single) < 1e-9
        and abs(out_double[0][1] - expected_double) < 1e-9
    )

    results["all_pass"] = all(v for v in results.values() if isinstance(v, bool))
    return results


# ============================================================
# CLI smoke test entry point
# ============================================================

def _cli_smoke(db_path: str, query: str, mode: str = "hybrid") -> None:
    """Stand-alone smoke test. Run as ``python3 hybrid_retrieval.py DB QUERY [MODE]``."""
    # Need an embedding for the query — borrow claude_memory._encode if available.
    # Lookup happens relative to the DB's parent (which is where claude_memory.py lives
    # in production); the workspace path is different so we fall through to manual import.
    encode_fn = None
    for candidate in [
        Path(db_path).parent,
        Path(__file__).resolve().parent,   # organs/ — where claude_memory.py lives
    ]:
        if (candidate / "claude_memory.py").exists():
            sys.path.insert(0, str(candidate))
            from claude_memory import _encode as encode_fn  # type: ignore
            break

    if encode_fn is None:
        raise RuntimeError(
            "Could not locate claude_memory._encode; pass a working DB path."
        )

    query_vec = encode_fn(query, "search_query")

    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    t0 = time.time()
    results = recall_hybrid(conn, query, query_vec, top_k=10, mode=mode)
    elapsed_ms = int((time.time() - t0) * 1000)

    print(f"Mode: {mode}, elapsed: {elapsed_ms}ms, results: {len(results)}")
    for r in results:
        path = r["retrieval_path"]
        sid = r["id"]
        score = r["score"]
        summary = (r["summary"] or "")[:80]
        print(f"  #{sid:>5} [{path}] score={score:>7.4f}: {summary}")
    conn.close()


def test_triggers_smoke(db_path: str) -> bool:
    """Smoke-test the FTS5 INSERT / UPDATE / DELETE triggers in 5 stages.

    Verifies the migration's three triggers (``memories_ai`` /
    ``memories_au`` / ``memories_ad``) keep the FTS5 shadow table in sync
    with the base ``memories`` table for the full lifecycle of a row.

    Stages:
        0. Baseline — ``memories`` row-count must equal ``memories_fts``
           row-count (the FTS table is full-coverage).
        1. INSERT — insert a sentinel row with a unique time-stamped marker
           in ``content``. Verify ``memories_fts MATCH 'trigger_smoke_marker_*'``
           returns 1 hit + total fts count = baseline + 1.
        2. UPDATE — overwrite the sentinel's ``content`` with a fresh marker.
           Verify old marker MATCH returns 0 hits + new marker MATCH returns 1.
        3. DELETE — delete the sentinel row. Verify the new marker MATCH
           drops to 0 hits + total fts count returns to baseline.
        4. Final reconciliation — memories.count == memories_fts.count again.

    The sentinel row is always cleaned up via a try/finally so the DB is
    safe to use after a partial-failure run.

    Args:
        db_path: path to the SQLite DB. Must already have the FTS5 migration
            applied (``is_fts5_migration_applied`` returns True).

    Returns:
        True if all 5 stages pass; False if any stage fails. Prints a
        compact PASS / FAIL summary to stdout either way.
    """
    print(f"test_triggers_smoke: db_path={db_path}")
    conn = sqlite3.connect(db_path)
    failures: List[str] = []
    inserted_id: Optional[int] = None
    baseline_memories = 0
    baseline_fts = 0
    marker_v1 = f"trigger_smoke_marker_v1_{int(time.time() * 1000)}"
    marker_v2 = f"trigger_smoke_marker_v2_{int(time.time() * 1000)}"

    try:
        # ---- Stage 0: baseline ----
        if not is_fts5_migration_applied(conn):
            print("  FAIL (stage 0): memories_fts table missing — apply migration first")
            return False
        baseline_memories = conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        baseline_fts = conn.execute(
            "SELECT COUNT(*) FROM memories_fts"
        ).fetchone()[0]
        stage0_ok = baseline_memories == baseline_fts
        print(
            f"  stage 0 (baseline counts): memories={baseline_memories} "
            f"fts={baseline_fts} match={stage0_ok}"
        )
        if not stage0_ok:
            failures.append(
                f"baseline mismatch memories={baseline_memories} fts={baseline_fts}"
            )

        # ---- Stage 1: INSERT trigger fires ----
        # Use ALL the columns the production schema makes NOT-NULL to be safe;
        # in practice content + created_at + access_count are the load-bearing
        # ones and the rest default in the schema. This INSERT is rolled back
        # if anything goes wrong — see finally clause.
        cur = conn.execute(
            """
            INSERT INTO memories (
                content, summary, category, importance, source_project,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                marker_v1,
                "trigger smoke sentinel",
                "test",
                0.01,
                "ace-iter-2-trigger-smoke",
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        inserted_id = cur.lastrowid
        conn.commit()
        match_count_v1 = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
            (f"trigger_smoke_marker_v1_*",),
        ).fetchone()[0]
        post_insert_fts = conn.execute(
            "SELECT COUNT(*) FROM memories_fts"
        ).fetchone()[0]
        stage1_ok = (match_count_v1 == 1) and (post_insert_fts == baseline_fts + 1)
        print(
            f"  stage 1 (INSERT trigger): inserted_id={inserted_id} "
            f"marker_v1_matches={match_count_v1} fts_total={post_insert_fts} "
            f"ok={stage1_ok}"
        )
        if not stage1_ok:
            failures.append(
                f"INSERT: marker_matches={match_count_v1} (want 1), "
                f"fts_total={post_insert_fts} (want {baseline_fts + 1})"
            )

        # ---- Stage 2: UPDATE trigger fires ----
        conn.execute(
            "UPDATE memories SET content = ? WHERE id = ?",
            (marker_v2, inserted_id),
        )
        conn.commit()
        old_match = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
            ("trigger_smoke_marker_v1_*",),
        ).fetchone()[0]
        new_match = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
            ("trigger_smoke_marker_v2_*",),
        ).fetchone()[0]
        stage2_ok = old_match == 0 and new_match == 1
        print(
            f"  stage 2 (UPDATE trigger): old_marker_matches={old_match} "
            f"new_marker_matches={new_match} ok={stage2_ok}"
        )
        if not stage2_ok:
            failures.append(
                f"UPDATE: old_marker_matches={old_match} (want 0), "
                f"new_marker_matches={new_match} (want 1)"
            )

        # ---- Stage 3: DELETE trigger fires ----
        conn.execute("DELETE FROM memories WHERE id = ?", (inserted_id,))
        conn.commit()
        inserted_id = None  # already cleaned up; suppress duplicate cleanup
        post_delete_match = conn.execute(
            "SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
            ("trigger_smoke_marker_v2_*",),
        ).fetchone()[0]
        post_delete_fts = conn.execute(
            "SELECT COUNT(*) FROM memories_fts"
        ).fetchone()[0]
        stage3_ok = post_delete_match == 0 and post_delete_fts == baseline_fts
        print(
            f"  stage 3 (DELETE trigger): match_after_delete={post_delete_match} "
            f"fts_total={post_delete_fts} ok={stage3_ok}"
        )
        if not stage3_ok:
            failures.append(
                f"DELETE: match_after_delete={post_delete_match} (want 0), "
                f"fts_total={post_delete_fts} (want {baseline_fts})"
            )

        # ---- Stage 4: final reconciliation ----
        end_memories = conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]
        end_fts = conn.execute(
            "SELECT COUNT(*) FROM memories_fts"
        ).fetchone()[0]
        stage4_ok = (
            end_memories == baseline_memories
            and end_fts == baseline_fts
            and end_memories == end_fts
        )
        print(
            f"  stage 4 (reconcile): memories={end_memories} fts={end_fts} "
            f"baseline_memories={baseline_memories} baseline_fts={baseline_fts} "
            f"ok={stage4_ok}"
        )
        if not stage4_ok:
            failures.append(
                f"reconcile: end_memories={end_memories} end_fts={end_fts} "
                f"vs baseline_memories={baseline_memories} baseline_fts={baseline_fts}"
            )
    finally:
        # Best-effort cleanup if we crashed mid-test.
        if inserted_id is not None:
            try:
                conn.execute("DELETE FROM memories WHERE id = ?", (inserted_id,))
                conn.commit()
                print(f"  finally: cleaned up sentinel id={inserted_id}")
            except Exception as e:  # noqa: BLE001
                print(f"  finally: cleanup raised {e!r} (db may need manual fix)")
        conn.close()

    if failures:
        print(f"FAIL test_triggers_smoke ({len(failures)} stage failure(s)):")
        for f in failures:
            print(f"  - {f}")
        return False
    print("PASS test_triggers_smoke (5/5 stages)")
    return True


if __name__ == "__main__":
    # --- --unit-tests : run all parameter-free unit tests ---
    if len(sys.argv) >= 2 and sys.argv[1] == "--unit-tests":
        import json
        rrf_results = unit_test_rrf_fuse()
        san_results = unit_test_sanitize_fts_query()
        all_pass = bool(rrf_results.get("all_pass")) and bool(
            san_results.get("all_pass")
        )
        summary = {
            "rrf_fuse": rrf_results,
            "sanitize_fts_query": san_results,
            "overall_all_pass": all_pass,
        }
        print(json.dumps(summary, indent=2, default=str))
        sys.exit(0 if all_pass else 1)

    # --- --test-triggers DB_PATH : 5-stage INSERT/UPDATE/DELETE smoke ---
    if len(sys.argv) >= 2 and sys.argv[1] == "--test-triggers":
        if len(sys.argv) < 3:
            print("Usage: python3 hybrid_retrieval.py --test-triggers <db_path>")
            sys.exit(2)
        ok = test_triggers_smoke(sys.argv[2])
        sys.exit(0 if ok else 1)

    # --- positional usage: db_path query [mode] ---
    if len(sys.argv) < 3:
        print(
            "Usage:\n"
            "  python3 hybrid_retrieval.py <db_path> <query> [mode]\n"
            "  python3 hybrid_retrieval.py --unit-tests\n"
            "  python3 hybrid_retrieval.py --test-triggers <db_path>"
        )
        sys.exit(1)
    db_path_arg = sys.argv[1]
    query_arg = sys.argv[2]
    mode_arg = sys.argv[3] if len(sys.argv) > 3 else "hybrid"
    _cli_smoke(db_path_arg, query_arg, mode_arg)
