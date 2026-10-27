#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_self_aware.py — the M0<->M3 self-awareness projection (Voidloom L5-M3).

Reads the ``organs`` registry from ``voidloom_manifest.json`` (the SSOT, owned
and written by M0/the installer) and renders the TWO projections the council
ruled (#5837):

  * ``render_compact_block(...)``  — the SessionStart-hook projection: a
    tier-filtered, CAPPED block of ``verb — WHEN to reach for it`` one-liners
    plus a pointer to ``voidloom capabilities``. THE WHEN-TRIGGER IS THE
    LOAD-BEARING HALF — it turns *knowing* an organ exists into *using* it.
  * ``render_full_projection(...)`` — the ``voidloom capabilities`` projection:
    the full prose+examples view of the SAME organs.

DESIGN INVARIANTS (council-ruled, do NOT re-litigate):
  * MANIFEST IS SSOT; THIS MODULE (and the hook) READS ONLY. There is no
    hand-maintained organ list anywhere in the hook or this renderer — drift is
    structurally impossible. The only literal organ data lives in the manifest.
  * TIER-FILTERING IS THE BLOAT GOVERNOR: an organ renders iff
    ``organ.tier <= host_tier``. A cold/Tier-0 seed shows ZERO extra lines, so
    the genericized public seed stays minimal + honest.
  * CAPPED: the compact block shows at most ``cap`` organs (~10) so the context
    cost paid every session forever stays bounded; lower ``order`` wins.
  * GENERICIZED/PERSONA-FREE BY SCHEMA (defect #114): the renderer only ever
    emits the manifest's own ``verb``/``when``/``desc`` strings + static
    scaffolding. No persona names, no filesystem paths, no client identifiers.
  * FAIL-GRACEFUL: manifest absent / unreadable / malformed / no ``organs``
    section -> returns ``None`` (the hook then emits NO block, and session start
    is unaffected — the D1+D2 never-break guarantee).

STDLIB-ONLY. No embedding model, no network — a tiny ``json.loads`` of a small
file, the same cost class as the pre-cognitive cache read (the <2s budget holds).

The M0<->M3 contract (the manifest ``organs`` section):

    "organs": {
      "schema_version": 1,
      "host_tier": <int>,            # the host's capability tier (0..N)
      "tier_names": {<int>: <str>},  # human labels for tiers (optional)
      "capabilities_verb": "voidloom capabilities",
      "organs": [
        {
          "verb":  <str>,   # the invocation, e.g. "recall --explain"
          "when":  <str>,   # the <=10-word reaching-trigger (LOAD-BEARING)
          "tier":  <int>,   # min host tier to surface this organ
          "desc":  <str>,   # one-line prose for the full projection
          "order": <int>,   # stable sort key for the cap (lower = earlier)
          "example": <str>  # OPTIONAL; a concrete example for the full view
        }, ...
      ]
    }

``host_tier`` may be overridden at render time (the hook derives it from the
manifest's ``hardware_tier``); the in-manifest value is the default.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# The compact block is capped so the per-session context cost stays bounded.
DEFAULT_CAP = 10

# Map M0's coarse ``hardware_tier`` label -> the integer capability tier the
# organ-filter compares against. Unknown labels fall back to the broadest
# *generic* floor (0) so an unrecognized host shows only the universal organs
# (honest + minimal), never accidentally over-surfacing GPU-only capability.
#
# Tier ladder (matches the engine's two-tier embedding doctrine, #4258):
#   0  generic / cold seed / stdlib floor  -> universal organs only
#   1  cpu-only (nomic 768-dim) host
#   2  gpu-capable (jina 1024-dim) host
_HARDWARE_TIER_TO_INT = {
    "cold": 0,
    "seed": 0,
    "generic": 0,
    "stdlib": 0,
    "cpu": 1,
    "cpu-only": 1,
    "nomic": 1,
    "gpu": 2,
    "gpu-low": 2,
    "gpu-mid": 2,
    "gpu-high": 2,
    "gpu-capable": 2,
    "jina": 2,
}


def _coerce_host_tier(value: Any) -> Optional[int]:
    """Coerce a host-tier hint (int OR an M0 ``hardware_tier`` label) to an int.

    Returns ``None`` only when the hint is missing/unusable AND the caller did
    not supply one; callers treat ``None`` as 'fall back to the manifest value'.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is an int subclass
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _HARDWARE_TIER_TO_INT:
            return _HARDWARE_TIER_TO_INT[key]
        # Unknown string label -> safe generic floor.
        return 0
    return None


def load_organs(manifest_path: str) -> Optional[Dict[str, Any]]:
    """Read + validate the ``organs`` section from the manifest.

    Returns the normalized organs-section dict (with an int ``host_tier`` and a
    cleaned, sorted ``organs`` list) or ``None`` on ANY problem (absent file,
    unreadable, malformed JSON, missing/empty ``organs`` section, no valid
    organ entries). NEVER raises — the caller relies on None to skip the block.
    """
    try:
        p = Path(manifest_path)
        if not p.exists():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(raw, dict):
        return None
    section = raw.get("organs")
    if not isinstance(section, dict):
        return None
    entries = section.get("organs")
    if not isinstance(entries, list) or not entries:
        return None

    # host_tier: prefer the explicit organs-section value; else derive from the
    # manifest's coarse hardware_tier; else the generic floor (0).
    host_tier = _coerce_host_tier(section.get("host_tier"))
    if host_tier is None:
        host_tier = _coerce_host_tier(raw.get("hardware_tier"))
    if host_tier is None:
        host_tier = 0

    cleaned: List[Dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        verb = e.get("verb")
        when = e.get("when")
        if not isinstance(verb, str) or not verb.strip():
            continue
        if not isinstance(when, str) or not when.strip():
            continue
        tier = e.get("tier", 0)
        if isinstance(tier, bool) or not isinstance(tier, int):
            tier = 0
        order = e.get("order", 1000)
        if isinstance(order, bool) or not isinstance(order, int):
            order = 1000
        desc = e.get("desc")
        desc = desc.strip() if isinstance(desc, str) else ""
        example = e.get("example")
        example = example.strip() if isinstance(example, str) else ""
        cleaned.append({
            "verb": verb.strip(),
            "when": when.strip(),
            "tier": tier,
            "order": order,
            "desc": desc,
            "example": example,
        })

    if not cleaned:
        return None

    # Stable sort by (order, verb) so the cap is deterministic.
    cleaned.sort(key=lambda o: (o["order"], o["verb"]))

    return {
        "schema_version": section.get("schema_version"),
        "host_tier": host_tier,
        "tier_names": section.get("tier_names")
                      if isinstance(section.get("tier_names"), dict) else {},
        "capabilities_verb": (section.get("capabilities_verb")
                              if isinstance(section.get("capabilities_verb"), str)
                              and section.get("capabilities_verb").strip()
                              else "voidloom capabilities"),
        "organs": cleaned,
    }


def _filter_for_tier(organs: List[Dict[str, Any]],
                     host_tier: int) -> List[Dict[str, Any]]:
    """Tier-filter: keep only organs whose ``tier <= host_tier``.

    This is THE bloat governor — a cold/Tier-0 host keeps only the universal
    (tier-0) organs, so the genericized seed shows ZERO extra lines.
    """
    return [o for o in organs if int(o.get("tier", 0)) <= int(host_tier)]


def render_compact_block(manifest_path: str,
                         host_tier: Any = None,
                         cap: int = DEFAULT_CAP) -> Optional[str]:
    """The SessionStart-hook projection: a tier-filtered, capped block of
    ``verb — WHEN`` one-liners + a pointer to the full ``capabilities`` verb.

    Returns the block string, or ``None`` when there is nothing to show (no
    manifest / no organs / none pass the tier filter) — the hook then emits no
    block and session start is unaffected.

    ``host_tier`` (int OR an M0 ``hardware_tier`` label) overrides the manifest's
    value when provided; otherwise the manifest value is used.
    """
    section = load_organs(manifest_path)
    if section is None:
        return None

    effective_tier = _coerce_host_tier(host_tier)
    if effective_tier is None:
        effective_tier = section["host_tier"]

    visible = _filter_for_tier(section["organs"], effective_tier)
    if not visible:
        return None

    capped = visible[: max(0, int(cap))]
    if not capped:
        return None

    cap_verb = section["capabilities_verb"]
    # Width-align the verbs so the WHEN-triggers form a readable column.
    width = max(len(o["verb"]) for o in capped)

    lines: List[str] = []
    lines.append("L5 ORGANS — the brain has grown new capabilities. Reach for "
                 "them WHEN:")
    for o in capped:
        lines.append(f"  {o['verb'].ljust(width)}  — {o['when']}")
    more = len(visible) - len(capped)
    tail = (f"  Full prose + examples + every organ: `{cap_verb}`"
            + (f" ({more} more not shown here)." if more > 0 else "."))
    lines.append(tail)
    return "\n".join(lines)


def render_precompact_line(manifest_path: str,
                           host_tier: Any = None) -> Optional[str]:
    """ONE calm capability re-anchor line for the PreCompact hook.

    Compaction wipes learned context, so the brain re-states — in a single line
    — that the L5 organs exist + where their full description lives. Returns the
    line, or ``None`` (no manifest / no organs / none pass the filter) so the
    PreCompact hook simply omits it.
    """
    section = load_organs(manifest_path)
    if section is None:
        return None

    effective_tier = _coerce_host_tier(host_tier)
    if effective_tier is None:
        effective_tier = section["host_tier"]

    visible = _filter_for_tier(section["organs"], effective_tier)
    if not visible:
        return None

    cap_verb = section["capabilities_verb"]
    return (f"void-anchor: {len(visible)} L5 organs remain available across the "
            f"boundary — re-list them anytime with `{cap_verb}`.")


def render_full_projection(manifest_path: str,
                           host_tier: Any = None,
                           all_tiers: bool = True) -> Optional[Dict[str, Any]]:
    """The ``voidloom capabilities`` projection: the FULL prose+examples view of
    the SAME organs (one manifest, two projections, zero duplication).

    Returns a structured dict (for the CLI's ``--json`` envelope) carrying every
    organ with its ``verb``/``when``/``desc``/``example``/``tier`` and an
    ``available`` flag (true iff it passes the host-tier filter). ``all_tiers``
    True lists every organ (marking which are available on this host); False
    lists only the available ones.

    Returns ``None`` only when there is no usable ``organs`` section.
    """
    section = load_organs(manifest_path)
    if section is None:
        return None

    effective_tier = _coerce_host_tier(host_tier)
    if effective_tier is None:
        effective_tier = section["host_tier"]

    rows: List[Dict[str, Any]] = []
    for o in section["organs"]:
        available = int(o.get("tier", 0)) <= int(effective_tier)
        if not all_tiers and not available:
            continue
        rows.append({
            "verb": o["verb"],
            "when": o["when"],
            "desc": o["desc"],
            "example": o["example"],
            "tier": o["tier"],
            "available": available,
        })

    return {
        "schema_version": section["schema_version"],
        "host_tier": effective_tier,
        "tier_names": section["tier_names"],
        "capabilities_verb": section["capabilities_verb"],
        "count": len(rows),
        "available_count": sum(1 for r in rows if r["available"]),
        "organs": rows,
    }


def render_full_text(projection: Dict[str, Any]) -> str:
    """A calm human rendering of the full projection (the non-``--json`` view of
    ``voidloom capabilities``)."""
    lines: List[str] = []
    lines.append("Voidloom CAPABILITIES — the brain's L5 organs")
    lines.append("=" * 52)
    tn = projection.get("tier_names") or {}
    ht = projection.get("host_tier")
    ht_label = tn.get(str(ht)) or tn.get(ht) or f"tier {ht}"
    lines.append(f"Host tier: {ht_label}  "
                 f"({projection.get('available_count', 0)}/"
                 f"{projection.get('count', 0)} organs available here)")
    lines.append("")
    for o in projection.get("organs", []):
        mark = "" if o.get("available") else "  [needs a higher tier]"
        # Verbs are heterogeneous: some are literal CLI invocations
        # (``recall --explain``), some are conceptual organs (``communities``).
        # Render the verb verbatim — never blindly prefix ``voidloom``.
        lines.append(f"{o['verb']}{mark}")
        if o.get("desc"):
            lines.append(f"    {o['desc']}")
        lines.append(f"    when: {o['when']}")
        if o.get("example"):
            lines.append(f"    e.g.  {o['example']}")
        lines.append("")
    lines.append("This view and the compact session-start block are TWO "
                 "projections of ONE manifest registry (no drift).")
    return "\n".join(lines).rstrip() + "\n"
