"""_batch.py — single-model-load batch recall/store over the facade.

THE MECHANIC (and the whole reason this module exists): ``_facade._import_engine``
imports the canonical engine ONCE per process, and the engine caches the heavy
embedder + reranker as process-globals. So looping the existing facade
``recall()`` / ``store_full()`` over N items *inside one process* loads the
embedding model exactly ONCE and then runs N warm embeds — the genuine batch
speed-up — for free, with NO new model-loading code. (A shell ``for``-loop that
re-invokes ``voidloom recall`` N times pays the multi-second model load N times;
this does not.)

Design rules:
  * **Per-item isolation.** Each item is run under its own ``try/except``. One bad
    item yields ``{"index": i, "status": "error", "message": ...}`` and the batch
    CONTINUES — a single malformed query/memory never aborts the run.
  * **Agnostic + portable.** The DB is resolved via the single package resolver
    (``_paths.resolve_db``) — never hardcoded. No owner / persona / machine refs.
    The exact same module ships to every install (primary, mirror, laptop, and
    the genericized public seed) unchanged.
  * **Stdlib-cheap import.** Importing this module pulls only stdlib + the
    stdlib-pure ``_paths``; the heavy facade (and through it the engine + torch)
    is imported LAZILY on the first item, inside the loop.
  * **Thin over the facade.** No engine logic is re-implemented here; this is a
    loop + result-shaping layer over the same facade calls the single verbs use,
    so it can never drift from engine behaviour.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union

from . import _paths

# A batch-recall element is either a bare query string or a dict carrying the
# query plus an optional per-item top_k override.
RecallItem = Union[str, Dict[str, Any]]
# A batch-store element is a memory dict (content + category required).
StoreItem = Dict[str, Any]

# The store fields a memory dict may carry, mirroring ``_vl_batch_store.py`` v2
# (the L5-organ-aware store path: kind taxonomy, auto_edges graph-weave,
# source_user actor-stamp). content + category are REQUIRED; the rest optional.
_STORE_REQUIRED = ("content", "category")
_STORE_OPTIONAL = (
    "tags", "kind", "importance", "summary", "project", "context",
    "source_user", "auto_edges", "answers",
)


def _coerce_recall_item(item: RecallItem, default_top_k: int):
    """Normalise a recall element into ``(query, top_k)``.

    Accepts a bare string (uses the batch-wide ``default_top_k``) or a dict with
    a required ``query`` and an optional ``top_k`` override. Raises ``ValueError``
    with a precise message for anything malformed — the loop catches it and
    records a per-item error without aborting the batch."""
    if isinstance(item, str):
        q = item.strip()
        if not q:
            raise ValueError("empty query string")
        return q, default_top_k
    if isinstance(item, dict):
        q = item.get("query")
        if not isinstance(q, str) or not q.strip():
            raise ValueError("recall item dict missing a non-empty 'query'")
        tk = item.get("top_k", default_top_k)
        try:
            tk = int(tk)
        except (TypeError, ValueError):
            raise ValueError(f"top_k {tk!r} is not an integer")
        if tk <= 0:
            raise ValueError(f"top_k {tk} must be positive")
        return q.strip(), tk
    raise ValueError(
        f"recall item must be a string or a dict, got {type(item).__name__}")


def channel_keys(res: Dict[str, Any]) -> List[str]:
    """The side-channel keys an engine recall dict carries (the dream fix, A3): every channel the engine ran is
    named under ``rerank.channels`` and present at the top level as a list. Derived from the result, never a
    hard-coded list here, so a new channel passes through with no change to this file."""
    diag = ((res.get("rerank") or {}).get("channels") or {}) if isinstance(res, dict) else {}
    return [k for k, v in diag.items() if isinstance(v, dict) and isinstance(res.get(k), list)]


def recall_batch(queries: Sequence[RecallItem], *, top_k: int = 5,
                 db_path: Optional[str] = None, leads: Optional[int] = None,
                 explore: bool = False) -> List[Dict[str, Any]]:
    """Run N recalls in ONE process (single model load). Returns a per-item list.

    COLLAPSED-SHAPE contract (H6): each per-query record is the FLATTENED shape
    ``{"index": i, "query": q, "top_k": k, "status": "ok", "results":
    [<memory>...]}`` on success — the matched memories live DIRECTLY on the record
    (the inner ``result.`` engine-dict wrapper is gone). A malformed item / engine
    failure yields ``{"index": i, "query": q, "top_k": k, "status": "error",
    "message": ...}``. Per-item isolation: one bad item never aborts the rest.

    Why flatten: the single ``recall`` verb surfaces the engine dict's own
    ``.results`` (the memory list) directly under ``data.results`` in its
    envelope. The cli folds THIS list straight into ``data["results"]``, so a
    matched memory must sit at ``.data.results[].results[]`` — one level shallower
    than the old ``.data.results[].result.results[]``. We lift the engine dict's
    ``results`` (the memory list) up to the record here; the cli passes the list
    through verbatim, so the human card and the ``--json`` path agree on ONE shape.
    The full engine scoring dict is still available per-result inside that list
    (each memory carries its sub-scores) — only the redundant outer envelope layer
    is removed.

    The DB is resolved ONCE up front via the single resolver (so a set
    ``WIZARD_LIBRARY_DB`` / ``CLAUDE_MEMORY_DB`` is honoured, else relative-self)
    and reused for every item — the same DB the single ``recall`` verb would open.
    An explicit ``db_path`` (e.g. a temp copy) overrides resolution."""
    resolved = db_path if db_path is not None else _paths.resolve_db()
    results: List[Dict[str, Any]] = []
    # Lazy heavy import — happens once, on the first call below, then warm.
    from . import _facade
    for i, item in enumerate(queries):
        try:
            q, k = _coerce_recall_item(item, top_k)
            res = _facade.recall(q, top_k=k, db_path=resolved, leads=leads, explore=explore)
            if isinstance(res, dict) and res.get("status") == "ok":
                # Lift the engine dict's own memory list up to the record so the
                # collapsed shape exposes memories at `.results[]` (no inner
                # `result.` layer). The engine names its hit list `results`;
                # default to [] so a 0-hit OK recall still has a list there.
                hits = res.get("results")
                if not isinstance(hits, list):
                    hits = []
                rec = {"index": i, "query": q, "top_k": k, "status": "ok", "results": hits}
                # The side channels ride beside `results` (memories only); the batch-recall default verb used to
                # lift `results` alone and would have silently dropped them (VAEL).
                for ck in channel_keys(res):
                    rec[ck] = res[ck]
                results.append(rec)
            else:
                msg = (res.get("message") or res.get("error")
                       if isinstance(res, dict) else None) or \
                      "engine reported a non-ok recall result"
                results.append({"index": i, "query": q, "top_k": k,
                                "status": "error", "message": str(msg)})
        except Exception as e:  # noqa: BLE001 — per-item isolation is the contract
            # `q`/`k` may be UNBOUND here (``_coerce_recall_item`` is the first
            # statement and can raise before they are assigned), so this record
            # carries only the safe fields. The cli's human renderer reads
            # ``rec.get("query", "")`` and tolerates the absence.
            results.append({"index": i, "status": "error",
                            "message": f"{type(e).__name__}: {e}"})
    return results


def _validate_store_item(item: StoreItem) -> Dict[str, Any]:
    """Validate + normalise a memory dict into the kwargs ``store_full`` expects.

    Raises ``ValueError`` for a non-dict, a missing required field, an importance
    out of range, or an unknown field — caught by the loop as a per-item error.
    Returns a clean kwargs dict (content, category, tags, importance, kind,
    project, context, summary, source_user, auto_edges)."""
    if not isinstance(item, dict):
        raise ValueError(
            f"store item must be a dict, got {type(item).__name__}")
    allowed = set(_STORE_REQUIRED) | set(_STORE_OPTIONAL)
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise ValueError(f"unknown store field(s): {', '.join(unknown)}")
    for req in _STORE_REQUIRED:
        v = item.get(req)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"store item missing required non-empty {req!r}")
    importance = item.get("importance", 0.5)
    try:
        importance = float(importance)
    except (TypeError, ValueError):
        raise ValueError(f"importance {importance!r} is not a number")
    if not (0.0 <= importance <= 1.0):
        raise ValueError(f"importance {importance} out of range [0.0, 1.0]")
    tags = item.get("tags", "")
    if isinstance(tags, (list, tuple)):
        tags = ",".join(str(t) for t in tags)
    elif tags is None:
        tags = ""
    elif not isinstance(tags, str):
        raise ValueError(f"tags must be a string or list, got {type(tags).__name__}")
    return {
        "content": item["content"],
        "category": item["category"],
        "tags": tags,
        "importance": importance,
        "kind": item.get("kind", "episodic") or "episodic",
        "project": item.get("project"),
        "context": item.get("context"),
        "summary": item.get("summary"),
        "source_user": item.get("source_user"),
        "auto_edges": bool(item.get("auto_edges", False)),
        "answers": _answers_arg(item.get("answers")),
    }


def _answers_arg(v: Any) -> Optional[str]:
    """A batch item's ``answers`` (the dream id(s) it settles): a list of ints or a comma string -> the engine's
    comma string; None stays None (R1-M6)."""
    if v is None or v == "" or v == []:
        return None
    if isinstance(v, (list, tuple)):
        return ",".join(str(int(x)) for x in v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return v
    raise ValueError(f"answers must be a dream id, a list of ids or a comma string, got {type(v).__name__}")


def store_batch(items: Sequence[StoreItem], *,
                db_path: Optional[str] = None,
                valid_categories: Optional[Sequence[str]] = None,
                ) -> List[Dict[str, Any]]:
    """Store N memories in ONE process (single model load). Returns a per-item list.

    Each result is ``{"index": i, "status": "ok", "memory_id": <id>, "result":
    <engine dict>}`` on success, or ``{"index": i, "status": "error", "message":
    ...}`` on a malformed item / invalid category / engine failure. Per-item
    isolation: one bad memory never aborts the rest.

    Category-enum validation runs BEFORE the engine call when ``valid_categories``
    is supplied (the cli passes the engine's live enum), so an invalid-category
    item errors cleanly without consuming the engine — but the embedding model is
    already loaded by the first valid item, so this costs nothing extra. Drives
    the richer ``store_full`` facade path so a memory dict's kind / auto_edges /
    source_user / summary / project / context all reach the engine.

    The DB is resolved ONCE up front via the single resolver. An explicit
    ``db_path`` (a temp copy for tests / a non-live target) overrides it — and
    SHOULD be used for any batch that must not touch the live brain."""
    resolved = db_path if db_path is not None else _paths.resolve_db()
    cats = set(valid_categories) if valid_categories else None
    results: List[Dict[str, Any]] = []
    from . import _facade  # lazy heavy import — once, then warm.
    for i, item in enumerate(items):
        try:
            kw = _validate_store_item(item)
            if cats is not None and kw["category"] not in cats:
                results.append({
                    "index": i, "status": "error",
                    "message": (f"category {kw['category']!r} is not valid"),
                    "valid": sorted(cats)})
                continue
            res = _facade.store_full(db_path=resolved, **kw)
            if isinstance(res, dict) and res.get("status") == "ok":
                results.append({"index": i, "status": "ok",
                                "memory_id": res.get("memory_id"),
                                "result": res})
            else:
                msg = (res.get("message") or res.get("error")
                       if isinstance(res, dict) else None) or \
                      "engine reported a non-ok store result"
                results.append({"index": i, "status": "error",
                                "message": str(msg), "result": res})
        except Exception as e:  # noqa: BLE001 — per-item isolation is the contract
            results.append({"index": i, "status": "error",
                            "message": f"{type(e).__name__}: {e}"})
    return results


def summarize(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Count succeeded / failed across a per-item result list.

    Returns ``{"count": N, "succeeded": S, "failed": F}`` — the headline the cli
    folds into the batch envelope so a caller can assert on counts, never on
    substring text."""
    count = len(results)
    succeeded = sum(1 for r in results if r.get("status") == "ok")
    return {"count": count, "succeeded": succeeded,
            "failed": count - succeeded}
