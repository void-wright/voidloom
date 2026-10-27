"""Voidloom L5-M4 — "The Assayer" (no-actuator dream-output reward scorer).

ONE LANE ONLY: dream-output priority. This is the single genuinely-blind spot in
the brain's heuristics. The recall/promotion heuristics score a memory by use
(``usage_count`` / ``access_count``), so a *fresh* dream output — ``usage_count
== 0``, the dream pool is **all** fresh outputs — scores 0.0 / insufficient_data.
The Assayer is a learned reward model that ranks the nightly dream DIGEST so the
most worth-reading dreams float to the top of what Drew sees. It touches NOTHING
else: trust, promotion, self-eval scoring all keep their interpretable
heuristics (the L5-I council CUT verdict HOLDS there).

THE NO-ACTUATOR FENCE (the heart — defuses reward-hacking ENTIRELY)
------------------------------------------------------------------
The Assayer writes ONLY to an advisory ``assayer_scores`` table
``(memory_id, score, model_version, scored_at, surprise_delta)``. That table is
read by EXACTLY TWO consumers:

  1. the dream-DIGEST sorter (``score_dream_digest`` here, surfaced through
     ``memory_dreaming.get_dream_digest_assayed``), and
  2. the self-eval ADVISORY display (read-only reporting).

NO prune / promote / archive / boost / importance-write verb may read it. The
score reaches no mutation path. No actuator -> no gradient -> the brain cannot
learn to "write for the Assayer." Goodhart has nothing to grip. This is proven
*structurally* by ``test_assayer.py`` (it scans the hot-path modules and asserts
``assayer_scores`` appears in only the two permitted readers).

THE FIVE DISCIPLINES (each is a fence, not a promise)
-----------------------------------------------------
  1. NO-ACTUATOR: advisory table only; score never read by a mutation path.
  2. CONTENT-TEXT-ONLY INPUT: the scorer sees ONLY the dream's synthesized text
     (``dream_content`` / the linked memory ``content``). It NEVER reads affect,
     importance, encoding_surprise, foundation_score, or any metadata — this
     preserves the L5-G firewall ("NEVER store arousal-surprise as salience";
     dream_pipeline.py "G is EPISTEMIC only").
  3. WILD LANE: the digest reserves a slice for LOW-score, HIGH-divergence dreams
     that the Assayer can NOT cull (quality is orthogonal to surprise). A clever
     mundane dream cannot starve a strange one out of Drew's view.
  4. MODEL-GATED + GRACEFUL-ABSENT: the scorer is ``nvidia/Qwen3-Nemotron-8B-BRRM``
     (a dedicated Bradley-Terry reward model; OML license; INT4 ~5GB). It is
     OFF BY DEFAULT (``ASSAYER_ENABLED`` is falsy). With the flag off, or with no
     model present, ``score_dream_digest`` is a CLEAN NO-OP and the existing
     heuristic digest sort is byte-unchanged. The heuristic floor ships and STAYS
     the default.
  5. SURPRISE AS REWARD-DELTA: ``Assayer-score − council-expected`` is a
     principled novelty byproduct (recorded, advisory). A dream the reward model
     loves but the council did not expect = a genuinely surprising win.

FALSIFIABLE BAKE-OFF (try-and-retire)
-------------------------------------
``run_bakeoff`` reuses ``monthly_self_eval._compute_dream_quality`` and asks: does
the Assayer-sorted digest surface MORE ``drew_flagged_interesting`` dreams in its
top-K than the heuristic-sorted digest? Keep the Assayer if it clears the bar;
retire it if not. Retire = delete THIS file + DROP the ``assayer_scores`` table.

PURITY: stdlib + sqlite3 only. ``model_garden`` and ``numpy`` are imported
*optionally* (inside functions, never at module top) so that importing this
module on a no-model host is free and never drags in torch. NO new pip dep.

CONFINEMENT NOTE: this is a workspace copy (ACE iteration 1). It lands by ``cp``
to ``<brain>/organs/assayer.py`` only after the Reflector clears it.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("assayer")

# ============================================================
# Constants — the gates and the lane geometry.
# ============================================================

# The model handle. The Assayer asks model_garden for THIS key (optionally — if
# model_garden or the model is absent, the Assayer is a clean no-op).
ASSAYER_MODEL_KEY = "assayer"
ASSAYER_MODEL_REPO = "nvidia/Qwen3-Nemotron-8B-BRRM"

# model_version stamp written alongside every score. The heuristic-only fallback
# NEVER writes a row (a no-op writes nothing), so this stamp always names a real
# reward-model inference. The version string lets a future re-bake distinguish
# model generations and lets the bake-off retire a stale generation cleanly.
MODEL_VERSION = "qwen3-nemotron-8b-brrm-int4-v1"

# THE OFF-BY-DEFAULT GATE. Truthiness mirrors claude_memory._env_flag exactly
# (1/true/yes/on). Anything else — unset, "0", "off", "" — is OFF. The heuristic
# floor is the default; the Assayer is an opt-in trial.
ASSAYER_ENABLE_ENV = "ASSAYER_ENABLED"

# WILD-LANE reservation. Of a digest of `limit` slots, at least this fraction is
# reserved for low-Assayer-score, high-divergence dreams the Assayer cannot cull.
# Quality (Assayer score) is orthogonal to surprise (cosine_distance); the wild
# lane guarantees the strange dreams are never starved out by the merely-good.
WILD_LANE_FRACTION = 0.30
# A dream must be at least this divergent (cosine distance) to be wild-lane
# eligible. Mirrors the L4/L5 divergent floor (distance_min=0.6, NEVER lowered).
WILD_LANE_MIN_DIVERGENCE = 0.6

# The advisory table this organ owns. Named here so the no-actuator test and the
# migration agree on one string.
ASSAYER_TABLE = "assayer_scores"


# ============================================================
# The off-by-default gate (graceful, never raises).
# ============================================================

def _env_flag(name: str) -> bool:
    """True when env var `name` is set truthy (1/true/yes/on).

    Byte-for-byte the same truthiness as claude_memory._env_flag so the Assayer's
    gate behaves identically to every other Voidloom kill-switch.
    """
    val = os.environ.get(name)
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "on")


def is_enabled() -> bool:
    """The single source of truth for "is the Assayer trial on?".

    OFF unless ``ASSAYER_ENABLED`` is explicitly truthy. This is the load-bearing
    default: with it off, ``score_dream_digest`` is a clean no-op and the existing
    heuristic digest is byte-unchanged.
    """
    return _env_flag(ASSAYER_ENABLE_ENV)


# ============================================================
# Model access — OPTIONAL, graceful-absent. Never raises, never imports torch
# at module scope.
# ============================================================

def _get_scorer() -> Tuple[Optional[Any], str]:
    """Return (reward_model_or_None, reason).

    Imports model_garden INSIDE the function so a no-model host pays nothing for
    importing this module. Returns (None, reason) — never raises — when:
      * model_garden is not importable (partial install), or
      * the 'assayer' key is not registered / not enabled / model not cached, or
      * the load fails.

    A None return is the graceful-absent path: the caller becomes a clean no-op.
    """
    try:
        import model_garden  # type: ignore
    except Exception as e:  # partial install — degrade, never crash dreaming.
        return None, f"model_garden unavailable: {type(e).__name__}: {e}"

    # is_available answers "could this be served at all" without loading. For an
    # un-provisioned reward model (default), this is False -> clean no-op.
    try:
        if not model_garden.is_available(ASSAYER_MODEL_KEY):
            reason = (model_garden.absence_reason(ASSAYER_MODEL_KEY)
                      or f"model {ASSAYER_MODEL_KEY!r} not available (not provisioned)")
            return None, reason
    except Exception as e:
        return None, f"model_garden.is_available raised: {type(e).__name__}: {e}"

    # A reward model is a GPU-burst class load. We ask the Garden's burst hook if
    # present, else the resident door, else degrade. Either way: None -> no-op.
    model = None
    try:
        getter = getattr(model_garden, "get_burst_model", None)
        if getter is not None:
            # gpu_guard-gated; profile=None lets the Garden pick a default burst
            # profile. If the burst hook is still deferred (M0b), it returns None.
            try:
                model = getter(ASSAYER_MODEL_KEY, None)
            except TypeError:
                model = None
        if model is None and hasattr(model_garden, "get_model"):
            model = model_garden.get_model(ASSAYER_MODEL_KEY)
    except Exception as e:
        return None, f"scorer load failed: {type(e).__name__}: {e}"

    if model is None:
        reason = None
        try:
            reason = model_garden.absence_reason(ASSAYER_MODEL_KEY)
        except Exception:
            reason = None
        return None, reason or f"{ASSAYER_MODEL_KEY!r} returned None (graceful-absent)"

    return model, "ok"


def _score_text(model: Any, text: str) -> Optional[float]:
    """Run the reward model on ONE dream's text and return a scalar in [0, 1].

    CONTENT-TEXT-ONLY: `text` is the dream's synthesized content. The model never
    sees metadata. A Bradley-Terry reward model emits a scalar reward; we map it
    to [0,1] with a logistic squash so scores are comparable across nights.

    Returns None on any per-item failure (the caller skips that item gracefully).
    The exact tokenizer/template lives with the model card; this wrapper is
    deliberately tolerant of a few reward-model call shapes so the seam works the
    day the model is provisioned without a code change here.
    """
    if not text or not text.strip():
        return None
    try:
        raw = None
        # Try the common reward-model entry points in order; each is optional.
        for attr in ("score", "reward", "predict", "__call__"):
            fn = getattr(model, attr, None)
            if fn is None:
                continue
            try:
                raw = fn(text)
            except TypeError:
                # Some reward models take (prompt, response); a dream is a
                # standalone artifact, so we pass an empty prompt context.
                try:
                    raw = fn("", text)
                except Exception:
                    raw = None
            if raw is not None:
                break
        if raw is None:
            return None
        val = _as_scalar(raw)
        if val is None:
            return None
        return _logistic_unit(val)
    except Exception as e:
        logger.debug("assayer._score_text item failure: %s", e)
        return None


def _as_scalar(raw: Any) -> Optional[float]:
    """Coerce a reward-model output (float / list / dict / tensor-ish) to a float."""
    try:
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, dict):
            for k in ("score", "reward", "logit", "value"):
                if k in raw:
                    return float(raw[k])
            return None
        if isinstance(raw, (list, tuple)) and raw:
            return _as_scalar(raw[0])
        # numpy / torch scalar-ish: .item()
        item = getattr(raw, "item", None)
        if callable(item):
            return float(item())
    except Exception:
        return None
    return None


def _logistic_unit(x: float) -> float:
    """Squash an unbounded reward logit into (0,1) so nights are comparable.

    If the model already emits a [0,1] probability, the logistic of a value in
    that range is monotonic and order-preserving, which is all the digest sorter
    needs (it ranks, it does not threshold on absolute magnitude).
    """
    import math
    try:
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        z = math.exp(x)
        return z / (1.0 + z)
    except OverflowError:
        return 0.0 if x < 0 else 1.0


# ============================================================
# Council-expected baseline — for surprise-as-reward-delta.
# ============================================================

def _council_expected_score(row: Dict[str, Any]) -> float:
    """The deterministic, content-free 'expected' reward the heuristic council
    would assign a dream BEFORE the reward model speaks.

    This is the baseline the Assayer's surprise is measured against:
        surprise_delta = assayer_score - council_expected
    A positive delta = the reward model values this dream MORE than the simple
    heuristic expected = a principled novelty signal (advisory only).

    Content-free: derived only from the dream's TYPE and its DIVERGENCE
    (cosine_distance), never from affect/importance/metadata. A divergent dream is
    'expected' to be a bit more of a gamble (lower baseline); a close-context
    dream is 'expected' to be a safer, mid baseline. Kept simple and bounded.
    """
    base = 0.5
    dtype = row.get("dream_type")
    dist = row.get("cosine_distance")
    if dtype == "divergent":
        # Far-flung pairs: the council expects a wider spread, lower central
        # expectation (more misses, occasional brilliant hit).
        base = 0.40
    elif dtype == "close_context":
        base = 0.55
    elif dtype == "persona":
        base = 0.50
    # A more divergent dream nudges the expectation down slightly (bigger gamble).
    if isinstance(dist, (int, float)):
        base -= 0.10 * max(0.0, min(1.0, float(dist)))
    return max(0.0, min(1.0, base))


# ============================================================
# Scoring entry — writes ONLY the advisory table. No actuator.
# ============================================================

def score_pending_dreams(
    conn: sqlite3.Connection,
    *,
    days: int = 7,
    max_items: int = 200,
) -> Dict[str, Any]:
    """Score recent, as-yet-unscored dreams and write rows to ``assayer_scores``.

    NO-OP unless ``is_enabled()`` AND a reward model is available. On the no-op
    path it writes NOTHING and returns ``{"status": "noop", ...}`` — the digest
    then falls through to the byte-unchanged heuristic sort.

    CONTENT-TEXT-ONLY: reads ``dream_content`` (and, only as a fallback, the
    linked ``memories.content``) — never any metadata column.

    Writes ``(memory_id, score, model_version, scored_at, surprise_delta)`` where
    ``memory_id`` is the dream's ``dream_memory_id`` (the stored reflection) when
    present, else a negative sentinel ``-dream_log.id`` so a dream that was not
    stored as a memory can still be advisory-scored without colliding with real
    memory ids. The advisory table is keyed so re-scoring REPLACES cleanly.
    """
    t0 = datetime.now()
    if not is_enabled():
        return {"status": "noop", "reason": f"{ASSAYER_ENABLE_ENV} is off", "scored": 0}

    if not _table_exists(conn, ASSAYER_TABLE):
        return {"status": "noop", "reason": f"{ASSAYER_TABLE} table absent (migration not applied)",
                "scored": 0}
    if not _table_exists(conn, "dream_log"):
        return {"status": "noop", "reason": "dream_log absent", "scored": 0}

    model, reason = _get_scorer()
    if model is None:
        return {"status": "noop", "reason": f"no scorer: {reason}", "scored": 0}

    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()

    rows = conn.execute(
        """SELECT dl.id, dl.dream_type, dl.cosine_distance, dl.dream_content,
                  dl.dream_memory_id
           FROM dream_log dl
           WHERE dl.is_meaningful = 1
             AND dl.created_at > ?
           ORDER BY dl.created_at DESC
           LIMIT ?""",
        (cutoff, int(max_items)),
    ).fetchall()

    scored = 0
    skipped = 0
    for r in rows:
        dream_id, dtype, dist, content, dmem = r[0], r[1], r[2], r[3], r[4]
        text = content
        if (not text) and dmem is not None:
            # Fallback to the stored reflection's CONTENT only — still text-only.
            mrow = conn.execute(
                "SELECT content FROM memories WHERE id = ?", (dmem,)
            ).fetchone()
            if mrow:
                text = mrow[0]
        s = _score_text(model, text or "")
        if s is None:
            skipped += 1
            continue
        expected = _council_expected_score(
            {"dream_type": dtype, "cosine_distance": dist}
        )
        delta = round(s - expected, 6)
        mem_key = dmem if dmem is not None else -int(dream_id)
        conn.execute(
            f"""INSERT INTO {ASSAYER_TABLE}
                    (memory_id, score, model_version, scored_at, surprise_delta)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(memory_id, model_version) DO UPDATE SET
                    score = excluded.score,
                    scored_at = excluded.scored_at,
                    surprise_delta = excluded.surprise_delta""",
            (mem_key, round(float(s), 6), MODEL_VERSION,
             datetime.now().isoformat(), delta),
        )
        scored += 1
    conn.commit()
    return {
        "status": "ok",
        "scored": scored,
        "skipped": skipped,
        "model_version": MODEL_VERSION,
        "duration_s": round((datetime.now() - t0).total_seconds(), 3),
    }


# ============================================================
# The digest sorter — the FIRST of the two permitted readers.
# ============================================================

def score_dream_digest(
    conn: sqlite3.Connection,
    digest: List[Dict[str, Any]],
    *,
    limit: Optional[int] = None,
    days: int = 7,
) -> List[Dict[str, Any]]:
    """Re-rank a heuristic digest by Assayer score, RESERVING the wild lane.
    Returns a NEW list; never mutates the caller's entries.

    GRACEFUL-ABSENT / OFF-BY-DEFAULT: if ``is_enabled()`` is False, OR the
    ``assayer_scores`` table is absent, OR not one candidate has a score row, this
    returns the digest UNCHANGED (same objects, same order). That is the clean
    no-op the heuristic floor relies on.

    WILD LANE (a FLOOR, not a filter): at least ``ceil(WILD_LANE_FRACTION * limit)``
    slots are reserved for the most-divergent dreams
    (cosine_distance >= WILD_LANE_MIN_DIVERGENCE). Because the heuristic digest
    handed in may already have dropped divergent dreams, the wild lane is filled
    from the FULL in-window dream pool (the digest sorter is a permitted reader of
    ``dream_log``), so a strange dream the quality sort would bury is still
    surfaced. The Assayer can NOT cull a wild-lane dream no matter how low it
    scores it (quality is orthogonal to surprise).

    NOTE: read-only on the DB (only SELECTs scores + dream rows). One of the two
    permitted readers of ``assayer_scores``; writes to NO table, triggers NO
    mutation. The score informs ORDER, never a keep/drop/boost.
    """
    if not digest:
        return digest
    n = limit if (limit is not None) else len(digest)
    if n <= 0:
        return digest

    if not is_enabled():
        return digest
    if not _table_exists(conn, ASSAYER_TABLE):
        return digest

    # ---- Build the candidate set: the passed digest UNION the in-window wild
    # pool (so the wild lane can surface a divergent dream the heuristic dropped).
    candidates = _augment_with_wild_pool(conn, digest, days=days)

    scores = _load_scores_for_digest(conn, candidates)
    if not scores:
        # No reward-model rows for ANY candidate -> the no-op path. Return the
        # ORIGINAL digest object unchanged (byte-for-byte heuristic order).
        return digest

    # Attach scores (advisory display fields) without losing entries that lack a
    # score (they sort to the bottom of the quality lane but can still be wild).
    annotated: List[Dict[str, Any]] = []
    for entry in candidates:
        e = dict(entry)
        key = _digest_key(entry)
        sc = scores.get(key)
        if sc is not None:
            e["assayer_score"] = sc["score"]
            e["assayer_surprise_delta"] = sc["surprise_delta"]
            e["assayer_model_version"] = sc["model_version"]
        else:
            e["assayer_score"] = None
            e["assayer_surprise_delta"] = None
            e["assayer_model_version"] = None
        annotated.append(e)

    # ---- Reserve the wild lane FIRST (protected from the quality sort). ----
    import math as _math
    wild_slots = int(_math.ceil(WILD_LANE_FRACTION * n)) if n > 0 else 0
    wild_slots = min(wild_slots, n)

    wild_eligible = [
        e for e in annotated
        if isinstance(e.get("cosine_distance"), (int, float))
        and float(e["cosine_distance"]) >= WILD_LANE_MIN_DIVERGENCE
    ]
    # Most-divergent first; the wild lane celebrates strangeness, not quality.
    wild_eligible.sort(key=lambda e: float(e["cosine_distance"]), reverse=True)

    wild_selected: List[Dict[str, Any]] = []
    wild_ids = set()
    for e in wild_eligible:
        if len(wild_selected) >= wild_slots:
            break
        e["wild_lane"] = True
        wild_selected.append(e)
        wild_ids.add(id(e))

    # ---- Quality lane: everything not already claimed by the wild lane, sorted
    # by Assayer score (desc). Unscored entries sink (None -> -inf) but are kept.
    quality_pool = [e for e in annotated if id(e) not in wild_ids]

    def _q(e: Dict[str, Any]) -> float:
        s = e.get("assayer_score")
        return float(s) if isinstance(s, (int, float)) else float("-inf")

    quality_pool.sort(key=_q, reverse=True)

    quality_slots = n - len(wild_selected)
    quality_selected = quality_pool[:max(0, quality_slots)]

    # Final order: top quality dreams, then the wild lane (so the strange dreams
    # are always visible, never buried below the cut). Stable within each lane.
    result = quality_selected + wild_selected
    # Trim to n while NEVER dropping a wild-lane entry (the wild lane is a floor).
    if len(result) > n:
        overflow = len(result) - n
        keep_quality = quality_selected[:max(0, len(quality_selected) - overflow)]
        result = keep_quality + wild_selected
    return result


def _augment_with_wild_pool(
    conn: sqlite3.Connection,
    digest: List[Dict[str, Any]],
    *,
    days: int,
    max_wild: int = 50,
) -> List[Dict[str, Any]]:
    """Return the digest plus any in-window high-divergence dreams not already in
    it, so the wild lane can be filled from the FULL dream pool (not just the
    heuristic top-N the caller handed in). De-duplicated by digest key. Read-only.

    This is what makes the wild lane a genuine FLOOR: a divergent dream the
    heuristic round-robin dropped is still eligible to be protected.
    """
    from datetime import timedelta
    if not _table_exists(conn, "dream_log"):
        return list(digest)
    have_keys = {_digest_key(e) for e in digest}
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT dl.id, dl.dream_type, dl.memory_a_id, dl.memory_b_id,
                  dl.cosine_distance, dl.dream_content, dl.dream_memory_id,
                  dl.drew_flagged_interesting, dl.created_at
           FROM dream_log dl
           WHERE dl.is_meaningful = 1
             AND dl.created_at > ?
             AND dl.cosine_distance >= ?
           ORDER BY dl.cosine_distance DESC
           LIMIT ?""",
        (cutoff, WILD_LANE_MIN_DIVERGENCE, int(max_wild)),
    ).fetchall()
    out = list(digest)
    for r in rows:
        entry = {
            "dream_id": r[0], "dream_type": r[1], "memory_a_id": r[2],
            "memory_b_id": r[3], "cosine_distance": r[4], "dream_content": r[5],
            "dream_memory_id": r[6], "flagged_interesting": bool(r[7]),
            "created_at": r[8],
        }
        if _digest_key(entry) not in have_keys:
            out.append(entry)
            have_keys.add(_digest_key(entry))
    return out


def _load_scores_for_digest(
    conn: sqlite3.Connection, digest: List[Dict[str, Any]]
) -> Dict[Any, Dict[str, Any]]:
    """Read advisory scores for the digest's entries. Read-only. Returns {} if no
    entry has a score (the no-op trigger)."""
    out: Dict[Any, Dict[str, Any]] = {}
    cols = _table_columns(conn, ASSAYER_TABLE)
    has_delta = "surprise_delta" in cols
    for entry in digest:
        key = _digest_key(entry)
        row = conn.execute(
            f"""SELECT score, model_version{', surprise_delta' if has_delta else ''}
                FROM {ASSAYER_TABLE}
                WHERE memory_id = ?
                ORDER BY scored_at DESC LIMIT 1""",
            (key,),
        ).fetchone()
        if row is not None:
            out[key] = {
                "score": row[0],
                "model_version": row[1],
                "surprise_delta": (row[2] if has_delta and len(row) > 2 else None),
            }
    return out


def _digest_key(entry: Dict[str, Any]) -> Any:
    """The advisory-table key for a digest entry: the stored reflection memory id
    when present, else the negative dream-id sentinel (matches score_pending)."""
    dmem = entry.get("dream_memory_id")
    if dmem is not None:
        return dmem
    did = entry.get("dream_id")
    return -int(did) if did is not None else None


# ============================================================
# The falsifiable bake-off (try-and-retire) — reuses _compute_dream_quality.
# ============================================================

def run_bakeoff(
    conn: sqlite3.Connection,
    *,
    month: Optional[str] = None,
    top_k: int = 5,
    days: int = 60,
) -> Dict[str, Any]:
    """Does the Assayer-sorted digest surface MORE ``drew_flagged_interesting``
    dreams in its top-K than the heuristic-sorted digest?

    Reuses ``monthly_self_eval._compute_dream_quality`` (imported optionally) for
    the corpus-level signal, and ALSO computes the head-to-head top-K flag count
    that is the actual decision criterion. Returns a verdict dict. Degenerate
    (0 flags) corpora return ``verdict='insufficient_signal'`` rather than a false
    PROMOTE — the bake-off refuses to claim a win it cannot measure.

    This harness is GPU-free: it does NOT require the reward model. If the Assayer
    has scored dreams (rows in ``assayer_scores``), it ranks by those; if not, the
    Assayer lane degenerates to the heuristic order and the verdict is honest
    about the absence of differentiation.
    """
    from datetime import timedelta

    result: Dict[str, Any] = {
        "status": "ok",
        "top_k": top_k,
        "month": month,
    }

    # ---- Corpus signal via the reused self-eval function (optional import). ----
    corpus = None
    if month is not None:
        try:
            import monthly_self_eval  # type: ignore
            corpus = monthly_self_eval._compute_dream_quality(conn, month)
        except Exception as e:
            corpus = {"status": f"unavailable: {type(e).__name__}: {e}"}
    result["corpus_dream_quality"] = corpus

    if not _table_exists(conn, "dream_log"):
        result["status"] = "noop"
        result["reason"] = "dream_log absent"
        result["verdict"] = "insufficient_signal"
        return result

    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    # The candidate pool: meaningful dreams in-window, with their flag + divergence.
    rows = conn.execute(
        """SELECT dl.id, dl.dream_type, dl.memory_a_id, dl.memory_b_id,
                  dl.cosine_distance, dl.dream_content, dl.dream_memory_id,
                  dl.drew_flagged_interesting, dl.created_at
           FROM dream_log dl
           WHERE dl.is_meaningful = 1
             AND dl.created_at > ?
           ORDER BY dl.created_at DESC""",
        (cutoff,),
    ).fetchall()

    pool: List[Dict[str, Any]] = [{
        "dream_id": r[0], "dream_type": r[1], "memory_a_id": r[2],
        "memory_b_id": r[3], "cosine_distance": r[4], "dream_content": r[5],
        "dream_memory_id": r[6], "flagged_interesting": bool(r[7]),
        "created_at": r[8],
    } for r in rows]

    total_flagged = sum(1 for p in pool if p["flagged_interesting"])
    result["pool_size"] = len(pool)
    result["total_flagged_in_pool"] = total_flagged

    if total_flagged == 0:
        # No ground truth -> the bake-off cannot adjudicate. This is the expected
        # state on a fresh corpus; it is NOT a failure and NOT a PROMOTE.
        result["verdict"] = "insufficient_signal"
        result["note"] = ("0 drew_flagged_interesting dreams in window; the "
                           "bake-off needs owner-flagged ground truth to adjudicate. "
                           "Keep the heuristic floor as default; revisit after flags accrue.")
        result["heuristic_topk_flagged"] = 0
        result["assayer_topk_flagged"] = 0
        return result

    # ---- Heuristic ranking: flagged-first then recency (mirrors get_dream_digest). ----
    heuristic_sorted = sorted(
        pool,
        key=lambda p: (1 if p["flagged_interesting"] else 0, p["created_at"]),
        reverse=True,
    )
    heuristic_topk = heuristic_sorted[:top_k]
    heuristic_flagged = sum(1 for p in heuristic_topk if p["flagged_interesting"])

    # ---- Assayer ranking: by advisory score when present, with the wild lane. ----
    assayer_sorted = score_dream_digest(conn, pool, limit=len(pool)) \
        if is_enabled() and _table_exists(conn, ASSAYER_TABLE) else heuristic_sorted
    # If the Assayer produced no differentiation (no scores), assayer_sorted ==
    # heuristic order; the bake-off will (correctly) show a tie.
    assayer_topk = assayer_sorted[:top_k]
    assayer_flagged = sum(1 for p in assayer_topk if p["flagged_interesting"])

    result["heuristic_topk_flagged"] = heuristic_flagged
    result["assayer_topk_flagged"] = assayer_flagged

    if assayer_flagged > heuristic_flagged:
        result["verdict"] = "assayer_wins"
        result["note"] = (f"Assayer top-{top_k} surfaced {assayer_flagged} flagged "
                          f"vs heuristic {heuristic_flagged}. Clears the bar — keep.")
    elif assayer_flagged < heuristic_flagged:
        result["verdict"] = "heuristic_wins"
        result["note"] = (f"Assayer top-{top_k} surfaced {assayer_flagged} flagged "
                          f"vs heuristic {heuristic_flagged}. Below the bar — retire "
                          f"(delete assayer.py + DROP {ASSAYER_TABLE}).")
    else:
        result["verdict"] = "tie"
        result["note"] = (f"Assayer and heuristic both surfaced {assayer_flagged} "
                          f"flagged in top-{top_k}. No advantage — keep heuristic default.")
    return result


# ============================================================
# Small DB helpers — read-only schema introspection (never mutate).
# ============================================================

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, name: str) -> List[str]:
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({name})").fetchall()]
    except sqlite3.Error:
        return []


# ============================================================
# Migration application (idempotent) — for tests + the land step.
# ============================================================

def apply_migration(conn: sqlite3.Connection, migration_sql_path: str) -> bool:
    """Apply the advisory-table migration idempotently. Returns True on success.

    The migration is pure DDL with IF NOT EXISTS; re-running is safe. This helper
    exists so the test harness and the land step share one application path.
    """
    with open(migration_sql_path, "r", encoding="utf-8") as fh:
        sql = fh.read()
    conn.executescript(sql)
    conn.commit()
    return _table_exists(conn, ASSAYER_TABLE)


def is_migration_applied(conn: sqlite3.Connection) -> bool:
    """True iff the advisory table exists (the migration's one structural effect)."""
    return _table_exists(conn, ASSAYER_TABLE)


if __name__ == "__main__":
    # A tiny CLI for the land-time smoke check. Read-only by default.
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(description="Voidloom L5-M4 Assayer (no-actuator dream scorer)")
    ap.add_argument("action", choices=["status", "bakeoff", "score"])
    ap.add_argument("--db", required=True, help="Path to claude_memory.db (or a temp copy)")
    ap.add_argument("--month", default=None, help="YYYY-MM for the bake-off corpus signal")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()

    _conn = sqlite3.connect(args.db, timeout=60.0)
    try:
        if args.action == "status":
            print(_json.dumps({
                "enabled": is_enabled(),
                "model_key": ASSAYER_MODEL_KEY,
                "model_repo": ASSAYER_MODEL_REPO,
                "model_version": MODEL_VERSION,
                "migration_applied": is_migration_applied(_conn),
                "scorer": _get_scorer()[1],
            }, indent=2))
        elif args.action == "score":
            print(_json.dumps(score_pending_dreams(_conn, days=args.days), indent=2))
        elif args.action == "bakeoff":
            print(_json.dumps(run_bakeoff(_conn, month=args.month, top_k=args.top_k,
                                          days=args.days), indent=2))
    finally:
        _conn.close()
