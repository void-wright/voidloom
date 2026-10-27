#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recall_explain.py — L5-N-β: Explainable Recall ("I surfaced this because…").

A thin, RENDER-ONLY consumer of the provenance the recall pipeline already
writes. It is the answer to a single question, per result: *why did this memory
surface?*

CARDINAL RULE — IT NEVER RECOMPUTES A SCORE.
  Every producer organ (L5-A PPR, L5-B diversity, L5-I trust, L5-E community,
  L5-DR seed_bias, the persona lens, the anti-recency foundation boost, the
  hybrid reranker) already STAMPS its sub-score onto each result dict as it runs
  inside ``cmd_recall``. This module only READS those fields, assembles them into
  ONE canonical "recall explanation object", and renders a calm one-liner. It
  imports no model, opens no DB, and performs no arithmetic on the scores beyond
  formatting them for display. (Council ruling #5826: "a thin render-only
  CONSUMER … NEVER recomputes a score; renders '—' for absent producers; gains
  richness as L5-B/A/I/E/DR land.")

DEGRADE-GRACEFUL BY CONSTRUCTION.
  A producer that has not run (or not yet been built) simply did not stamp its
  field. ``build_explanation`` records that factor as ABSENT, and
  ``render_explanation`` shows ``—`` for it. As each organ lands, the same object
  gains a real factor with zero changes here — the explanation grows richer on
  its own.

THE ONE OBJECT, FOUR SURFACES (council #5826).
  v1 ships the canonical object + the PRIMARY surface (``recall --explain``).
  The other three surfaces — the cognitive-map "why-surfaced" dashboard panel,
  the markdown-mirror archival projection, and the ambient banner line — consume
  this *same* object later (see the deferred note in the generation report).

DEPENDENCIES: Python standard library + ``void_render`` (itself stdlib-only).
No torch, no embedding model, no network, no DB handle. Safe to import on the
recall hot path; in fact the default (non-``--explain``) recall path never calls
this module at all.

PUBLIC API:
  build_explanation(result_row: dict) -> dict
      Assemble the canonical explanation object from the fields already on a
      recall result dict. Pure read; never recomputes; absent producer -> a
      factor with present=False.
  render_explanation(obj: dict, mode: str = None, *, renderer=None) -> str
      Render the object as a calm one-liner via void_render. ``mode`` is one of
      'truecolor' / 'ansi256' / 'none' (None -> auto-detect on stderr). Absent
      factors render '—'. 'none' mode emits ZERO ANSI escapes.
  explain_line(result_row: dict, mode: str = None) -> str
      Convenience: build_explanation -> render_explanation in one call.
  iter_explanations(results: list, mode: str = None) -> Iterator[str]
      Yield a ``"#<id>  <line>"`` string per result, for the CLI surface.
"""
from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional

# ---------------------------------------------------------------------------
# void_render is the rendering SSOT. Resolve it with the same graceful-degrade
# dual-path used across the wizard library, and fall back to a tiny no-color
# shim if it is somehow unavailable, so the explainer can NEVER break recall.
# ---------------------------------------------------------------------------
try:  # package context
    from . import void_render as _vr  # type: ignore
except (ImportError, ValueError):
    try:  # flat-dir / sys.path context
        import void_render as _vr  # type: ignore
    except ImportError:  # pragma: no cover - exercised only on a broken install
        _vr = None  # type: ignore


# The absence glyph. A single em-dash: calm, unambiguous, "this producer said
# nothing". Used everywhere a factor is not present.
ABSENT = "—"  # —

# Separator between factors in the rendered one-liner.
_SEP = " · "


# ---------------------------------------------------------------------------
# Factor catalogue — the canonical, ordered set of provenance signals.
#
# Each entry declares HOW to read one producer's already-written field off a
# result dict and HOW to label/format it. The order here is the display order
# (strongest-relevance signals first, provenance last). Adding a future organ =
# one row here; nothing else changes.
#
# A factor descriptor is a dict:
#   key        : stable machine id of the factor (object key)
#   label      : short human label for the one-liner
#   producer   : which organ writes it (for the structured object / dashboard)
#   read(row)  : pure reader -> the raw value, or None if the producer is absent
#   fmt(val)   : format the raw value for display (only called when present)
#   note(row)  : optional extra qualifier string (e.g. "boosted", "#2"), or None
#
# NONE of these do arithmetic on the score. They read and format. A value that
# is literally 0.0 (e.g. ppr_score=0.0, "PPR ran but this row got no mass") is
# PRESENT — only a missing key / None is ABSENT. That distinction is the whole
# point of degrade-graceful: "the organ ran and found nothing" differs from "the
# organ never ran".
# ---------------------------------------------------------------------------


def _f(v: Any) -> Optional[float]:
    """Coerce to float for *formatting only*. None/garbage -> None (absent)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_score(v: Any, places: int = 2) -> str:
    f = _f(v)
    if f is None:
        return ABSENT
    return f"{f:.{places}f}"


# -- per-producer readers (pure; None == producer absent) -------------------

def _read_path(row: Dict[str, Any]) -> Optional[str]:
    p = row.get("retrieval_path")
    return p if isinstance(p, str) and p else None


def _read_rerank_score(row: Dict[str, Any]) -> Optional[float]:
    # The reranker's blended relevance score (may have been augmented by later
    # organs; we report it verbatim, never recompute). 'score' is the canonical
    # field; 'relevance_score' is the legacy alias.
    if "score" in row and row["score"] is not None:
        return _f(row["score"])
    if "relevance_score" in row and row["relevance_score"] is not None:
        return _f(row["relevance_score"])
    return None


def _read_ppr(row: Dict[str, Any]) -> Optional[float]:
    # L5-A. ppr_score is stamped (even as 0.0) only when PPR actually ran.
    return _f(row["ppr_score"]) if "ppr_score" in row else None


def _read_lens(row: Dict[str, Any]) -> Optional[float]:
    return _f(row["lens_score"]) if "lens_score" in row else None


def _read_foundation(row: Dict[str, Any]) -> Optional[float]:
    # anti_recency stamps foundation_score (possibly None for an unscored row).
    return _f(row["foundation_score"]) if "foundation_score" in row else None


def _read_trust(row: Dict[str, Any]) -> Optional[float]:
    # L5-I. Absent today on recall result dicts (the producer has not landed on
    # this surface yet); reads several plausible key names so it lights up
    # automatically when trust starts stamping the result. NO recompute.
    for k in ("trust_score", "trust", "trust_tier_score"):
        if k in row and row[k] is not None:
            return _f(row[k])
    return None


def _read_community(row: Dict[str, Any]) -> Optional[Any]:
    # L5-E. Absent today; community_id (and optional label) light up when E-COMM
    # stamps the recall result.
    for k in ("community_id", "community"):
        if k in row and row[k] is not None:
            return row[k]
    return None


def _read_seed_bias(row: Dict[str, Any]) -> Optional[float]:
    # L5-DR / L5-L. Absent today; lights up when the dream/quickening seed-bias
    # is carried onto the recall result.
    for k in ("seed_bias", "dream_seed_bias"):
        if k in row and row[k] is not None:
            return _f(row[k])
    return None


# -- per-producer display notes (qualifiers; pure reads) --------------------

def _note_ppr(row: Dict[str, Any]) -> Optional[str]:
    return "boosted" if row.get("ppr_boosted") else None


def _note_foundation(row: Dict[str, Any]) -> Optional[str]:
    return "boosted" if row.get("foundation_boosted") else None


def _note_rank(row: Dict[str, Any]) -> Optional[str]:
    # The diversity organ stamps diversity_rank == final 0-based position.
    dr = row.get("diversity_rank")
    if isinstance(dr, int):
        return f"#{dr + 1}"
    return None


def _note_tier(row: Dict[str, Any]) -> Optional[str]:
    t = row.get("tier")
    return str(t) if t else None


def _fmt_path(v: str) -> str:
    # Humanize the retrieval_path token into a phrase.
    return {
        "hybrid": "hybrid match",
        "vector-only": "vector match",
        "vector-only-legacy": "vector match",
        "bm25-only": "keyword match",
        "no-rerank": "RRF match",
        "graph_expanded": "graph expansion",
    }.get(v, v.replace("_", " ").replace("-", " "))


def _fmt_community(v: Any) -> str:
    return f"#{v}" if isinstance(v, (int,)) else str(v)


# The ordered catalogue. retrieval_path leads (how it was found), then the
# relevance/association/persona/foundation sub-scores, then trust/community/
# seed_bias (the L5-I/E/DR signals that light up as they land).
_FACTORS: List[Dict[str, Any]] = [
    {
        "key": "retrieval_path", "label": "via", "producer": "hybrid_retrieval",
        "read": _read_path, "fmt": _fmt_path, "note": _note_tier,
    },
    {
        "key": "rerank", "label": "rerank", "producer": "hybrid_retrieval/reranker",
        "read": _read_rerank_score, "fmt": _fmt_score, "note": _note_rank,
    },
    {
        "key": "ppr", "label": "PPR", "producer": "L5-A ppr_recall",
        "read": _read_ppr, "fmt": _fmt_score, "note": _note_ppr,
    },
    {
        "key": "lens", "label": "lens", "producer": "persona_lenses",
        "read": _read_lens, "fmt": _fmt_score, "note": None,
    },
    {
        "key": "foundation", "label": "foundation", "producer": "anti_recency",
        "read": _read_foundation, "fmt": _fmt_score, "note": _note_foundation,
    },
    {
        "key": "trust", "label": "trust", "producer": "L5-I trust_tier",
        "read": _read_trust, "fmt": _fmt_score, "note": None,
    },
    {
        "key": "community", "label": "community", "producer": "L5-E communities",
        "read": _read_community, "fmt": _fmt_community, "note": None,
    },
    {
        "key": "seed_bias", "label": "seed-bias", "producer": "L5-DR/L5-L",
        "read": _read_seed_bias, "fmt": _fmt_score, "note": None,
    },
]


# ---------------------------------------------------------------------------
# build_explanation — assemble the canonical object (PURE READ, NO RECOMPUTE).
# ---------------------------------------------------------------------------


def build_explanation(result_row: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble the canonical recall-explanation object from a result dict.

    READS the provenance fields the producers already stamped; it does NOT
    recompute any score and does NOT open a DB or load a model. A producer whose
    field is missing (or None) is recorded as ``present=False`` (renders '—').

    The returned object::

        {
          "id": <memory id or None>,
          "factors": [
            {"key","label","producer","present","value","display","note"}, ...
          ],
          "present_keys": ["retrieval_path","rerank","ppr", ...],   # present only
          "absent_keys":  ["trust","community","seed_bias", ...],   # absent only
        }

    Every entry in ``factors`` is in canonical display order. ``value`` is the
    raw read (the producer's own number/string, unchanged); ``display`` is its
    formatted form (or '—' when absent).
    """
    row = result_row if isinstance(result_row, dict) else {}
    factors: List[Dict[str, Any]] = []
    present_keys: List[str] = []
    absent_keys: List[str] = []

    for spec in _FACTORS:
        raw = spec["read"](row)           # pure read -> value or None (absent)
        present = raw is not None
        if present:
            display = spec["fmt"](raw)
            note = spec["note"](row) if spec["note"] else None
            present_keys.append(spec["key"])
        else:
            display = ABSENT
            note = None
            absent_keys.append(spec["key"])
        factors.append({
            "key": spec["key"],
            "label": spec["label"],
            "producer": spec["producer"],
            "present": present,
            "value": raw,            # the producer's own value, verbatim
            "display": display,      # formatted-for-humans (or '—')
            "note": note,            # qualifier ("boosted" / "#2" / tier), or None
        })

    return {
        "id": row.get("id"),
        "factors": factors,
        "present_keys": present_keys,
        "absent_keys": absent_keys,
    }


# ---------------------------------------------------------------------------
# render_explanation — the calm one-liner, via void_render (degrade-graceful).
# ---------------------------------------------------------------------------


def _make_renderer(mode: Optional[str], renderer):
    """Resolve a void_render Renderer (or a no-color shim) for the given mode.

    NEVER raises. If void_render is unavailable, returns a tiny shim whose
    methods return text unchanged (so 'none'-equivalent zero-escape output).
    """
    if renderer is not None:
        return renderer
    if _vr is not None:
        try:
            import sys
            if mode in ("truecolor", "ansi256", "none"):
                return _vr.Renderer(force_mode=mode)
            # Auto-detect against stderr (the channel --explain prints to), so a
            # piped stdout never bleeds color from the explanation lines.
            return _vr.Renderer(stream=sys.stderr)
        except Exception:  # pragma: no cover - defensive only
            pass

    class _Shim:  # pragma: no cover - only when void_render is missing
        mode = "none"

        def paint(self, text, color=None, *, bold=False, dim=False):
            return text

        def accent(self, text, *, bold=False):
            return text

        def violet(self, text, *, bold=False):
            return text

        def dim(self, text):
            return text

    return _Shim()


def _safe(text: object) -> str:
    """Run a value through void_render.sanitize_for_terminal when available.

    Result fields like ``tier`` or a community label are memory-derived and may
    (in principle) carry terminal-control sequences; neutralize them before they
    reach the terminal. Numbers/static labels are inert but this is cheap and
    uniform. NEVER raises.
    """
    if _vr is not None:
        try:
            return _vr.sanitize_for_terminal(text)
        except Exception:  # pragma: no cover
            pass
    return "" if text is None else str(text)


def render_explanation(obj: Dict[str, Any], mode: Optional[str] = None,
                       *, renderer=None) -> str:
    """Render the explanation object as a single calm one-liner.

    ``mode`` is 'truecolor' / 'ansi256' / 'none' (None -> auto-detect on stderr).
    Pass ``renderer`` to share an already-built void_render Renderer. Absent
    factors render '—'. In 'none' mode the output carries ZERO ANSI escapes
    (mono path), matching void_render's plain contract. NEVER raises.

    Shape (present factors painted, absent shown dim as '<label> —')::

        via hybrid match · rerank 0.81 #2 · PPR 0.18 boosted · trust — · …

    Rendering is delegated to ``void_render.render_explanation`` (the rendering
    SSOT) so all ANSI/color logic lives in one place. The local painting path is
    used ONLY as a graceful fallback when void_render is unavailable.
    """
    if not isinstance(obj, dict):
        return ""
    factors = obj.get("factors", [])

    # Preferred path: the void_render SSOT renderer (mode-routed, sanitizing,
    # zero-escape mono). Available on every real install.
    if _vr is not None and hasattr(_vr, "render_explanation"):
        try:
            return _vr.render_explanation(factors, mode=mode, renderer=renderer)
        except Exception:  # pragma: no cover - SSOT renderer never raises anyway
            pass

    # Fallback (only if void_render is missing the primitive): paint locally with
    # whatever renderer/shim we can resolve, preserving the identical shape.
    r = _make_renderer(mode, renderer)
    parts: List[str] = []
    for fac in factors:
        if not isinstance(fac, dict):
            continue
        label = _safe(fac.get("label", ""))
        present = bool(fac.get("present"))
        display = _safe(fac.get("display", ABSENT))
        note = fac.get("note")
        if present:
            seg = r.dim(label) + " " + r.accent(display)
            if note:
                seg += " " + r.dim(_safe(note))
        else:
            seg = r.dim(label + " " + ABSENT)
        parts.append(seg)
    return _SEP.join(parts)


def explain_line(result_row: Dict[str, Any], mode: Optional[str] = None) -> str:
    """Build + render in one call. Convenience for callers that don't need the
    intermediate object."""
    return render_explanation(build_explanation(result_row), mode)


def iter_explanations(results: List[Dict[str, Any]],
                      mode: Optional[str] = None) -> Iterator[str]:
    """Yield one ``"#<id>  <line>"`` string per result, in input order.

    This is the shape the ``recall --explain`` CLI surface prints (to stderr, so
    the stdout JSON stays byte-identical). The id is rendered in starlight-gold
    accent when color is on; absent id -> '#?'. NEVER raises per-row: a row that
    somehow fails to render yields a minimal '#<id>  —' instead of aborting the
    whole batch.
    """
    r = _make_renderer(mode, None)
    for row in results or []:
        try:
            obj = build_explanation(row if isinstance(row, dict) else {})
            line = render_explanation(obj, renderer=r)
            mid = obj.get("id")
            tag = r.accent(f"#{mid}") if mid is not None else r.dim("#?")
            yield f"{tag}  {line}"
        except Exception:  # never let one bad row abort the explain batch
            mid = row.get("id") if isinstance(row, dict) else None
            yield f"#{mid if mid is not None else '?'}  {ABSENT}"


# ---------------------------------------------------------------------------
# Self-test — `python3 recall_explain.py --self-test` (no GPU, no DB, no net).
# ---------------------------------------------------------------------------


def _self_test() -> int:
    failures: List[str] = []

    def check(cond: bool, msg: str) -> None:
        if cond:
            print(f"  ok   {msg}")
        else:
            print(f"  FAIL {msg}")
            failures.append(msg)

    print("[1] build_explanation reads, never recomputes")
    # A fully-populated synthetic row (all current producers present).
    full = {
        "id": 412,
        "retrieval_path": "hybrid",
        "score": 0.8123,
        "ppr_score": 0.18,
        "ppr_boosted": True,
        "lens_score": 0.77,
        "foundation_score": 0.91,
        "foundation_boosted": True,
        "diversity_rank": 1,
    }
    obj = build_explanation(full)
    check(obj["id"] == 412, "id carried through")
    fac = {f["key"]: f for f in obj["factors"]}
    # value is the producer's OWN number, verbatim (proves no recompute).
    check(fac["rerank"]["value"] == 0.8123, "rerank value verbatim (0.8123)")
    check(fac["ppr"]["value"] == 0.18, "ppr value verbatim (0.18)")
    check(fac["foundation"]["value"] == 0.91, "foundation value verbatim (0.91)")
    check(fac["rerank"]["note"] == "#2", "diversity_rank -> '#2' (0-based+1)")
    check(fac["ppr"]["note"] == "boosted", "ppr_boosted -> 'boosted'")
    check(fac["foundation"]["note"] == "boosted", "foundation_boosted -> 'boosted'")
    # display is FORMAT of the same number, not a new computation.
    check(fac["rerank"]["display"] == "0.81", "rerank display = formatted 0.81")
    check(fac["ppr"]["display"] == "0.18", "ppr display = formatted 0.18")

    # PROOF of read-only: the only keys consulted are ones present in the row.
    # We assert that a value identical to the input is preserved (a recompute
    # would change it). Mutating an unrelated field must not change any factor.
    full2 = dict(full)
    full2["content"] = "x" * 5000  # heavy unrelated field
    obj2 = build_explanation(full2)
    check([f["value"] for f in obj2["factors"]] == [f["value"] for f in obj["factors"]],
          "unrelated field does not change any factor value")

    print("[2] absent producers -> present=False, display '—'")
    sparse = {"id": 7, "retrieval_path": "bm25-only", "score": 0.3}
    so = build_explanation(sparse)
    sf = {f["key"]: f for f in so["factors"]}
    check(sf["ppr"]["present"] is False, "ppr absent -> present False")
    check(sf["ppr"]["display"] == ABSENT, "ppr absent -> display '—'")
    check(sf["trust"]["present"] is False, "trust absent today -> present False")
    check(sf["community"]["display"] == ABSENT, "community absent -> '—'")
    check(sf["seed_bias"]["display"] == ABSENT, "seed_bias absent -> '—'")
    check("ppr" in so["absent_keys"] and "trust" in so["absent_keys"],
          "absent_keys lists the missing producers")
    check("retrieval_path" in so["present_keys"] and "rerank" in so["present_keys"],
          "present_keys lists the present producers")

    print("[3] 0.0 is PRESENT (organ ran, found nothing) — not absent")
    zero = {"id": 9, "retrieval_path": "hybrid", "score": 0.5,
            "ppr_score": 0.0, "ppr_boosted": False}
    zo = build_explanation(zero)
    zf = {f["key"]: f for f in zo["factors"]}
    check(zf["ppr"]["present"] is True, "ppr_score=0.0 is PRESENT (ran, no mass)")
    check(zf["ppr"]["display"] == "0.00", "ppr_score=0.0 -> '0.00' (not '—')")
    check(zf["ppr"]["note"] is None, "ppr_boosted False -> no 'boosted' note")

    print("[4] render_explanation: truecolor has escapes, mono has ZERO")
    line_color = render_explanation(obj, mode="truecolor")
    line_mono = render_explanation(obj, mode="none")
    check("\x1b" in line_color, "truecolor render contains ANSI escape")
    check("\x1b" not in line_mono, "mono ('none') render has ZERO '\\x1b'")
    # the mono line still carries the values + the absent dashes.
    check("0.81" in line_mono and "0.18" in line_mono, "mono shows the scores")
    check(ABSENT in line_mono, "mono shows '—' for absent producers")
    check("via" in line_mono and "hybrid match" in line_mono,
          "mono humanizes retrieval_path -> 'hybrid match'")

    print("[5] mono render of a SPARSE row shows '—' for every absent organ")
    sline = render_explanation(so, mode="none")
    check("\x1b" not in sline, "sparse mono has ZERO escapes")
    check(sline.count(ABSENT) >= 5, "sparse mono shows >=5 '—' (ppr/lens/found/trust/community/seed)")

    print("[6] iter_explanations yields '#id  line' per result, never raises")
    rows = [full, sparse, {"id": None}, "not-a-dict"]
    lines = list(iter_explanations(rows, mode="none"))
    check(len(lines) == 4, "one line per input row (incl. malformed)")
    check(lines[0].startswith("#412"), "first line tagged '#412'")
    check(lines[2].startswith("#?") or "#None" not in lines[2], "None id -> '#?'")
    check(all("\x1b" not in ln for ln in lines), "all mono lines zero-escape")

    print("[7] sanitizer neutralizes a hostile memory-derived field (tier)")
    hostile = {"id": 1, "retrieval_path": "hybrid", "score": 0.5,
               "tier": "eddie\x1b]52;c;BAD\x07-windows"}
    hline = render_explanation(build_explanation(hostile), mode="none")
    check("\x1b" not in hline, "OSC-52 in tier neutralized (no ESC survives)")
    check("]52;c" not in hline, "no OSC-52 payload survives")

    print("[8] empty / None inputs do not raise")
    check(build_explanation({}) is not None, "empty dict builds an object")
    check(build_explanation(None)["factors"], "None input -> object with absent factors")
    check(render_explanation({}) == "", "empty object renders ''")
    check(render_explanation(None) == "", "None object renders ''")

    print("=" * 60)
    if failures:
        print(f"RESULT: {len(failures)} FAILURE(S)")
        return 1
    print("RESULT: ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv or "--test" in sys.argv:
        raise SystemExit(_self_test())
    # Default: render the canonical demo object in all three modes.
    demo = {
        "id": 412, "retrieval_path": "hybrid", "score": 0.8123,
        "ppr_score": 0.18, "ppr_boosted": True, "lens_score": 0.77,
        "foundation_score": 0.91, "foundation_boosted": True, "diversity_rank": 1,
    }
    for m in ("truecolor", "ansi256", "none"):
        print(f"--- mode: {m} ---")
        print("  " + explain_line(demo, mode=m))
    print()
