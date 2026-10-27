#!/usr/bin/env python3
"""qwen_reranker.py — Voidloom's instruction-following causal-LM reranker adapter
(L5-C1). The score-half of a shadow-pointer swap from ``BAAI/bge-reranker-v2-m3``
(a CrossEncoder) to ``Qwen/Qwen3-Reranker-0.6B`` (an *instruction-aware* causal
LM, Apache-2.0).

WHY a separate adapter (not a CrossEncoder drop-in):
  Qwen3-Reranker-0.6B is a ``Qwen3ForCausalLM`` — its relevance signal is NOT a
  regression head you read off ``CrossEncoder.predict()``. The official scoring
  is: format (instruction, query, document) into the model's judge chat template,
  run ONE forward pass, take the next-token logits at the FINAL position, read the
  logit of the affirmative token ("yes") vs the negative token ("no"), and
  log_softmax those two -> the probability the model would answer "yes". That
  probability in [0, 1] is the relevance score. A wrong template OR wrong token
  ids silently *inverts or garbles* ranking, so this module hard-codes the
  template + token ids VERIFIED against the downloaded model card and a live
  relevant>irrelevant sanity (see generation_report.md).

  Building it as a causal-LM adapter (rather than wrapping
  ``CrossEncoder("Qwen/Qwen3-Reranker-0.6B")``, which the model card also offers)
  is deliberate: it exposes the ``instruction=`` hook that the future
  persona-aware reranking ("rank as the persona would care") needs, and it does not
  depend on sentence-transformers' internal handling of this causal model.

SHADOW-POINTER DISCIPLINE:
  This module is ONE half. The other half is the integration in
  ``hybrid_retrieval.reranker_score`` (see INTEGRATION_hybrid_retrieval.md), which
  asks the reranker gate first (a non-CPU accelerator AND >= 15.0 GiB RAM; never on
  CPU-only), then tries this adapter -- THE canonical reranker -- unless
  VOIDLOOM_RERANKER=bge, and ALWAYS falls back to the bge CrossEncoder on any failure. This module NEVER raises into a caller: every
  load/score failure path returns ``None`` so the integration degrades to bge.
  The bge path is the guaranteed fallback and is never removed.

GRACEFUL-ABSENT (mirrors hybrid_retrieval._load_reranker + model_garden):
  * model not downloaded / package missing / load exception -> ``load_model()``
    returns ``None`` and CACHES the None (don't retry a known-absent model).
  * any per-call scoring exception -> ``score_pairs()`` returns ``None``.
  A half-provisioned machine still recalls (via bge); it never crashes.

CONSTRAINTS (Turing sm_75 RTX 2080 Ti):
  * transformers + torch + stdlib ONLY. NO new pip dependency.
  * FP16 (no FP8), NO flash_attention_2 (FA2 needs Ampere+). cuda:0-resident,
    loads directly (NOT a gpu_guard burst — ~1.2 GB fits beside the encoder pack).

This module is generated in an ACE workspace and is side-effect-free at import
(the only effect is a lazy model load -> VRAM, triggered explicitly by the first
``score_pairs``/``load_model`` call). The orchestrator applies it to the live tree
alongside hybrid_retrieval.py after the loop closes.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("voidloom.qwen_reranker")

# ============================================================
# Model identity + device (mirror hybrid_retrieval's reranker constants).
# ============================================================
QWEN_RERANKER_MODEL_NAME = "Qwen/Qwen3-Reranker-0.6B"
QWEN_RERANKER_DEVICE = "cuda:0"  # the CUDA case only, for diagnostics: the device is _torch_device()'s answer

# Per-(query, doc) max length fed to the model (the judge prompt prefix/suffix are
# reserved out of this budget). 8192 is the model card's documented working
# length. Voidloom memories, however, can be LONG (full execution logs run to
# thousands of tokens); the A/B harness proved that feeding whole bodies in a
# batch OOMs the 11 GB card (a batch of 8 long judge prompts × 28 layers builds
# multi-GiB attention activations). So a hard PER-DOCUMENT token cap (below) is
# the real OOM guard, with this as the absolute ceiling.
QWEN_RERANKER_MAX_LENGTH = 8192

# PER-DOCUMENT token cap — the OOM-safety knob. A reranker only needs the head of
# a passage to judge relevance (bge-reranker-v2-m3 caps at 512 tokens for exactly
# this reason — see hybrid_retrieval._RERANKER_MAX_LENGTH). We cap each document
# body to this many tokens BEFORE wrapping it in the judge template, so a 4000-
# token execution-log memory never blows up the batch activation. 512 mirrors bge
# (apples-to-apples in the A/B) and is ample for relevance scoring.
QWEN_DOC_MAX_TOKENS = 512

# Causal-LM forward batch size on cuda:0. With the per-doc cap above, 8 fits
# comfortably beside the encoder pack on the 11 GB card. Overridable per call.
QWEN_RERANK_BATCH_SIZE = 8

# ------------------------------------------------------------
# THE EXACT Qwen3-Reranker-0.6B judge template (VERIFIED against the model card
# README "Using Transformers" section + the downloaded tokenizer, 2026-06-11).
# DO NOT paraphrase: the system sentence, the <Instruct>/<Query>/<Document>
# field labels, and the <think></think> assistant scaffold are part of the
# trained format. A change here re-opens the silent-inversion risk.
# ------------------------------------------------------------
_PREFIX = (
    "<|im_start|>system\n"
    "Judge whether the Document meets the requirements based on the Query and the "
    'Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"

# The model card's default task instruction (the persona-aware instruction is a
# documented FUTURE enhancement — see DEFAULT vs the instruction= hook below).
DEFAULT_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)

# The affirmative / negative tokens whose final-position logits ARE the score.
# Resolved at load time via tokenizer.convert_tokens_to_ids("yes"/"no") — verified
# to be single tokens 9693 ("yes") / 2152 ("no") on this build; we resolve them
# from the loaded tokenizer rather than hard-coding the ints, so a tokenizer
# revision can't silently desync the adapter.
_TOKEN_TRUE = "yes"
_TOKEN_FALSE = "no"

# Operator kill switch (parity with model_garden's per-model kill switches). When
# set truthy, load_model() refuses -> the integration falls back to bge.
KILL_SWITCH_ENV = "VOIDLOOM_DISABLE_QWEN_RERANKER"


# ============================================================
# Process-level cache (mirrors hybrid_retrieval._reranker_cache).
# Cache value is a loaded bundle dict OR None (None == "load failed/absent, do
# not retry this process").
# ============================================================
_qwen_cache: Dict[str, Any] = {}
# Why the last None was cached (for doctor / caller diagnostics).
_absence_reason: Optional[str] = None


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


def _truthy_env(name: str) -> bool:
    val = os.environ.get(name)
    if val is None:
        return False
    return val.strip().lower() not in ("", "0", "false", "no", "off")


def absence_reason() -> Optional[str]:
    """Why is the adapter currently unavailable? Last logged reason, or None if
    loaded / never attempted. For doctor + integration diagnostics."""
    return _absence_reason


def _format_judge_input(instruction: Optional[str], query: str, doc: str) -> str:
    """Build the judge-template BODY (between _PREFIX and _SUFFIX) for one pair.

    Matches the model card's ``format_instruction`` exactly:
        "<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}"
    A ``None`` instruction falls back to DEFAULT_INSTRUCTION (so the MVP always
    sends the documented default; the instruction= hook only changes THIS string).
    """
    instr = DEFAULT_INSTRUCTION if instruction is None else instruction
    return f"<Instruct>: {instr}\n<Query>: {query}\n<Document>: {doc}"


def logits_to_keep_kwargs(model) -> Dict[str, int]:
    """``{"logits_to_keep": 1}`` (transformers >= 4.50), else ``{"num_logits_to_keep": 1}`` (the older spelling), else
    ``{}`` -- read from the model's forward() signature, never guessed. With it, the LM head runs on the FINAL position
    only: the scorer reads ``logits[:, -1, :]`` and nothing else, and the full-vocabulary head at every position was
    ~18% of a CPU rerank's samples (memory anchor vl_cpu_rerank_hotspot). Qwen3's forward also takes ``**kwargs``, so
    an unknown spelling would be passed on silently -- which is why the signature is read rather than tried."""
    import inspect
    try:
        params = inspect.signature(model.forward).parameters
    except (TypeError, ValueError, AttributeError):
        return {}
    for name in ("logits_to_keep", "num_logits_to_keep"):
        if name in params:
            return {name: 1}
    return {}


def load_model(force_reload: bool = False) -> Optional[Dict[str, Any]]:
    """Lazy-load + process-cache the Qwen3-Reranker-0.6B causal-LM bundle.

    Returns a bundle dict ``{model, tokenizer, prefix_tokens, suffix_tokens,
    token_true_id, token_false_id, device}`` on success, or ``None`` on ANY
    failure (kill switch / missing package / missing cache / load exception).
    The ``None`` is cached so subsequent calls don't retry the expensive load,
    mirroring ``hybrid_retrieval._load_reranker``'s contract. ``force_reload=True``
    retries (e.g. after the GPU comes back).

    GRACEFUL-ABSENT: never raises. A failure here means the integration falls
    back to bge.
    """
    global _absence_reason

    if not force_reload and "bundle" in _qwen_cache:
        return _qwen_cache["bundle"]

    # 0. Operator kill switch wins unconditionally (panic switch -> fall back to bge).
    if _truthy_env(KILL_SWITCH_ENV):
        _absence_reason = f"kill-switch env {KILL_SWITCH_ENV} is set"
        logger.info("qwen_reranker.load_model: %s -> None (fall back to bge)", _absence_reason)
        _qwen_cache["bundle"] = None
        return None

    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except Exception as e:  # transformers/torch absent -> graceful-absent.
        _absence_reason = f"import failed: {type(e).__name__}: {e}"
        logger.warning("qwen_reranker.load_model: %s -> None (fall back to bge)", _absence_reason)
        _qwen_cache["bundle"] = None
        return None

    try:
        # padding_side='left' is REQUIRED: with left padding the real final
        # position is logits[:, -1, :] for EVERY row in a batch (this is exactly
        # why the model card sets it). Right padding would read a PAD position.
        tokenizer = AutoTokenizer.from_pretrained(
            QWEN_RERANKER_MODEL_NAME, padding_side="left"
        )
        device = _torch_device(torch)
        # FP16 on every accelerator (CUDA/ROCm, MPS, XPU: halves memory vs fp32; Turing
        # needs no bf16 path). On CPU fall back to fp32 (fp16 on CPU is slow/buggy).
        dtype = torch.float16 if device != "cpu" else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            QWEN_RERANKER_MODEL_NAME,
            torch_dtype=dtype,
            # NO attn_implementation="flash_attention_2": FA2 needs Ampere+; this
            # is Turing sm_75. The default ("eager"/"sdpa") path is correct here.
        )
        model = model.to(device).eval()

        # Resolve the affirmative/negative token ids FROM the loaded tokenizer
        # (don't hard-code ints — a tokenizer revision must not silently desync).
        token_true_id = tokenizer.convert_tokens_to_ids(_TOKEN_TRUE)
        token_false_id = tokenizer.convert_tokens_to_ids(_TOKEN_FALSE)
        unk = getattr(tokenizer, "unk_token_id", None)
        if (
            token_true_id is None
            or token_false_id is None
            or token_true_id == token_false_id
            or (unk is not None and token_true_id == unk)
            or (unk is not None and token_false_id == unk)
        ):
            raise ValueError(
                f"yes/no token ids look wrong (yes={token_true_id}, no={token_false_id}); "
                "refusing to load a reranker that would garble ranking"
            )

        prefix_tokens = tokenizer.encode(_PREFIX, add_special_tokens=False)
        suffix_tokens = tokenizer.encode(_SUFFIX, add_special_tokens=False)

        bundle: Dict[str, Any] = {
            "model": model,
            "tokenizer": tokenizer,
            "prefix_tokens": prefix_tokens,
            "suffix_tokens": suffix_tokens,
            "token_true_id": token_true_id,
            "token_false_id": token_false_id,
            "device": device,
        }
        _qwen_cache["bundle"] = bundle
        _absence_reason = None
        logger.info(
            "qwen_reranker.load_model: loaded %s on %s (yes=%s no=%s)",
            QWEN_RERANKER_MODEL_NAME, device, token_true_id, token_false_id,
        )
        return bundle
    except Exception as e:  # graceful-absent: any load failure degrades to bge.
        _absence_reason = f"load failed: {type(e).__name__}: {e}"
        logger.warning(
            "qwen_reranker.load_model: %s -> None (fall back to bge for the rest of this process)",
            _absence_reason,
        )
        _qwen_cache["bundle"] = None
        return None


def score_pairs(
    query: str,
    docs: Sequence[str],
    instruction: Optional[str] = None,
    *,
    batch_size: int = QWEN_RERANK_BATCH_SIZE,
    _bundle: Optional[Dict[str, Any]] = None,
) -> Optional[List[float]]:
    """Score each (query, doc) pair with the Qwen3-Reranker judge head.

    Parameters
    ----------
    query : str
        The free-text query (the same query for every doc — this is a reranker:
        N documents against ONE query).
    docs : Sequence[str]
        The candidate document texts (summary+content), in caller order. The
        returned scores are aligned 1:1 with this list (NOT sorted — sorting is
        the caller's job, exactly like ``CrossEncoder.predict`` returns aligned
        scores and ``reranker_score`` sorts).
    instruction : Optional[str]
        Per-call task instruction. ``None`` -> DEFAULT_INSTRUCTION (the MVP path).
        A non-None instruction is the HOOK for the future persona-aware reranking
        ("rank as the persona would care") — that feature is NOT built here; this
        only proves the prompt changes when an instruction is supplied.
    batch_size : int
        Causal-LM forward batch size on cuda:0.
    _bundle : Optional[dict]
        TEST SEAM. Inject a pre-built bundle (e.g. with a mock model returning
        controlled logits) so the yes/no extraction is provable with no GPU /
        no real model. Production: None -> load_model().

    Returns
    -------
    Optional[list[float]]
        A list of relevance scores in [0, 1] aligned to ``docs`` (the model's
        P("yes")). Returns ``None`` on ANY failure (model absent OR a forward
        pass raises) so the caller falls back to bge. Returns ``[]`` for empty
        ``docs`` (a well-defined empty result, NOT a failure).

    GRACEFUL: never raises into the caller.
    """
    global _absence_reason

    docs = list(docs)
    if not docs:
        return []

    bundle = _bundle if _bundle is not None else load_model()
    if bundle is None:
        return None  # known-absent / load failed -> caller uses bge.

    try:
        import torch
    except Exception as e:
        _absence_reason = f"torch import failed at score time: {e}"
        logger.warning("qwen_reranker.score_pairs: %s -> None", _absence_reason)
        return None

    model = bundle["model"]
    tokenizer = bundle["tokenizer"]
    prefix_tokens = bundle["prefix_tokens"]
    suffix_tokens = bundle["suffix_tokens"]
    token_true_id = bundle["token_true_id"]
    token_false_id = bundle["token_false_id"]
    device = bundle.get("device", QWEN_RERANKER_DEVICE)
    if "logits_kwargs" not in bundle:
        bundle["logits_kwargs"] = logits_to_keep_kwargs(model)
    logits_kwargs = bundle["logits_kwargs"]

    def _free_cuda():
        """Drop the backend's cached allocator blocks between batches so fragmentation
        doesn't accumulate across a long rerank run (the A/B showed free VRAM marching
        to 0 without this on the 11 GB Turing card). Backend-aware: CUDA/ROCm, MPS, XPU."""
        if device != "cpu":
            try:
                from voidloom_device import empty_cache as _ec
                _ec(torch)
            except Exception:
                pass

    def _score_chunk(body_chunk: List[str]) -> List[float]:
        """Forward one chunk of bodies -> P("yes") per row. On a CUDA OOM, FREE
        the cache and SPLIT the chunk recursively (halving) down to single docs;
        a single doc that still OOMs after a per-doc token cap is a true failure
        and re-raises (caught by the outer handler -> bge fallback). Per-document
        bodies are capped to QWEN_DOC_MAX_TOKENS BEFORE wrapping (the real OOM
        guard: a reranker needs only the passage head — bge caps at 512 too)."""
        # Each doc body capped independently to QWEN_DOC_MAX_TOKENS (no
        # 'longest_first' across the batch — we want a fixed per-doc ceiling).
        enc = tokenizer(
            body_chunk,
            padding=False,
            truncation=True,
            return_attention_mask=False,
            max_length=QWEN_DOC_MAX_TOKENS,
        )
        wrapped_ids = [prefix_tokens + ids + suffix_tokens for ids in enc["input_ids"]]
        try:
            batch = tokenizer.pad(
                {"input_ids": wrapped_ids}, padding=True, return_tensors="pt"
            )
            batch = {k: v.to(device) for k, v in batch.items()}
            with torch.no_grad():
                # Next-token logits at the FINAL position for each row
                # (padding_side='left' makes -1 the true last position). logits_to_keep=1:
                # the LM head computes that position only (logits shape [B, 1, V]).
                last_logits = model(**batch, **logits_kwargs).logits[:, -1, :]
                true_logits = last_logits[:, token_true_id]
                false_logits = last_logits[:, token_false_id]
                # log_softmax over [no, yes] then exp the "yes" channel == P("yes").
                stacked = torch.stack([false_logits, true_logits], dim=1)
                probs = torch.nn.functional.log_softmax(stacked, dim=1)
                yes_prob = probs[:, 1].exp().clamp(0.0, 1.0)
                out = [float(x) for x in yes_prob.tolist()]
            del batch, last_logits, true_logits, false_logits, stacked, probs, yes_prob
            return out
        except RuntimeError as _oom:   # torch.cuda.OutOfMemoryError is one; MPS/XPU raise RuntimeError
            from voidloom_device import is_out_of_memory as _is_oom
            if not _is_oom(_oom):
                raise
            _free_cuda()
            if len(body_chunk) == 1:
                # A single (already token-capped) doc still OOMs — genuine failure.
                raise
            mid = len(body_chunk) // 2
            logger.info(
                "qwen_reranker: CUDA OOM on chunk of %d — splitting and retrying",
                len(body_chunk),
            )
            left = _score_chunk(body_chunk[:mid])
            _free_cuda()
            right = _score_chunk(body_chunk[mid:])
            return left + right

    try:
        bodies = [_format_judge_input(instruction, query, d) for d in docs]
        scores: List[float] = []
        for start in range(0, len(bodies), max(1, batch_size)):
            chunk = bodies[start : start + max(1, batch_size)]
            scores.extend(_score_chunk(chunk))
            _free_cuda()
        return scores
    except Exception as e:  # graceful: a forward pass failure degrades to bge.
        _free_cuda()
        _absence_reason = f"score forward failed: {type(e).__name__}: {e}"
        logger.warning(
            "qwen_reranker.score_pairs: %s -> None (fall back to bge)", _absence_reason
        )
        return None


def is_available() -> bool:
    """Cheap-ish availability check: True iff the model loads (process-cached).

    NOTE: unlike model_garden.is_available (which is non-loading), this triggers
    the lazy load on first call because the adapter has no separate cache-presence
    probe of its own — the integration wants the authoritative "can I actually
    score" answer, and the load is cached + None-cached either way. For a purely
    non-loading probe, callers should use model_garden.is_available('reranker_qwen')
    once C1 registers the slot, or check the HF cache directly.
    """
    return load_model() is not None


# ============================================================
# CLI — `python3 qwen_reranker.py sanity` : the relevant>irrelevant proof.
# Print-only; loads the real model (VRAM). Mirrors the model card's example.
# ============================================================

def _sanity() -> int:
    logging.basicConfig(level=logging.INFO)
    query = "What is the capital of China?"
    docs = [
        "The capital of China is Beijing.",
        "Gravity is a force that attracts two bodies towards each other. It gives "
        "weight to physical objects and is responsible for the movement of planets.",
    ]
    bundle = load_model()
    if bundle is None:
        print(f"qwen_reranker UNAVAILABLE: {absence_reason()}")
        return 1
    scores = score_pairs(query, docs)
    if scores is None:
        print(f"qwen_reranker SCORING FAILED: {absence_reason()}")
        return 1
    print(f"relevant   ('Beijing')  : {scores[0]:.6f}")
    print(f"irrelevant ('gravity')  : {scores[1]:.6f}")
    ok = scores[0] > scores[1] and all(0.0 <= s <= 1.0 for s in scores)
    print(f"relevant > irrelevant   : {ok}  (template + token ids correct)")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="qwen_reranker — Qwen3-Reranker-0.6B causal-LM reranker adapter."
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("sanity", help="Load the real model and prove relevant>irrelevant.")
    args = parser.parse_args(argv)
    if args.cmd == "sanity":
        return _sanity()
    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main())
