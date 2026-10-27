#!/usr/bin/env python3
"""Cognitive Map Dashboard — L3 Component 3.

Generates a self-contained HTML visualization of the memory landscape.
No external dependencies (CDN-free, inline CSS/JS, works offline).

Usage:
    python3 cognitive_map.py /path/to/claude_memory.db /path/to/output.html

Functions:
    get_map_data(db_path)      -> dict of all visualization data
    render_html(data)          -> str (complete HTML document)
    generate_map(db_path, output_path) -> dict (generation metadata)

The HTML contains 7 visualization sections:
  1. Kind Distribution (donut chart via SVG)
  2. Project Clusters (proportional bubble chart via SVG)
  3. Edge Graph (top-100 most-connected, force-directed via Canvas JS)
  4. Temporal Heatmap (weekly activity over 6 months)
  5. Foundation Score Histogram (high/medium/low bands)
  6. Gap Zones (projects/tags with sparse coverage)
  7. Staleness Indicator (30/60/90-day access recency)
"""

import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Section 1: Data Extraction (testable independently of rendering)
# ---------------------------------------------------------------------------

def _get_conn(db_path: str) -> sqlite3.Connection:
    """Open a read-only connection to the memory database."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def get_kind_distribution(conn: sqlite3.Connection) -> Dict[str, int]:
    """Count memories per kind (episodic/semantic/procedural/reflection)."""
    rows = conn.execute(
        "SELECT COALESCE(kind, 'unclassified') as k, COUNT(*) as cnt "
        "FROM memories WHERE is_archived = 0 GROUP BY k ORDER BY cnt DESC"
    ).fetchall()
    return {row["k"]: row["cnt"] for row in rows}


def get_project_clusters(conn: sqlite3.Connection) -> Dict[str, int]:
    """Count memories per source_project (all, not capped)."""
    rows = conn.execute(
        "SELECT COALESCE(source_project, 'untagged') as proj, COUNT(*) as cnt "
        "FROM memories WHERE is_archived = 0 GROUP BY proj ORDER BY cnt DESC"
    ).fetchall()
    return {row["proj"]: row["cnt"] for row in rows}


def get_edge_graph(conn: sqlite3.Connection, limit: int = 100) -> Dict[str, Any]:
    """Get top-N most-connected memories and their inter-edges for graph viz.

    Returns:
        {
            "nodes": [{"id": int, "summary": str, "kind": str, "degree": int}, ...],
            "edges": [{"source": int, "target": int, "predicate": str}, ...]
        }
    """
    # Find top-N memories by CURRENT edge degree.
    # L5-D reader-correctness fix: degree (node connectivity) must reflect only
    # edges that are still valid — an invalidated edge (valid_until set) is no
    # longer a live connection and must not inflate a node's degree. (The
    # downstream inter-edge query at this function's tail already filters
    # `valid_until IS NULL`; this makes the degree ranking consistent with it.)
    #
    # 2026-10-26: degree is counted in ONE grouped pass over the live edges. The
    # old per-memory subquery `(source_id = m.id OR target_id = m.id)` could use
    # neither index, so it scanned every live edge once per memory (24,000 x
    # 42,000 on the owner's brain: 143 s of the weekly job, which then hit its
    # 120 s limit). Same degrees; m.id breaks ties so the map is deterministic.
    # A self-loop (source = target) would count twice here and once before; the
    # graph writers never create one.
    rows = conn.execute("""
        WITH ends AS (
            SELECT source_id AS id FROM memory_edges WHERE valid_until IS NULL
            UNION ALL
            SELECT target_id AS id FROM memory_edges WHERE valid_until IS NULL
        ),
        deg AS (SELECT id, COUNT(*) AS c FROM ends GROUP BY id)
        SELECT m.id, m.summary, m.kind, COALESCE(deg.c, 0) AS degree
        FROM memories m
        LEFT JOIN deg ON deg.id = m.id
        WHERE m.is_archived = 0
        ORDER BY degree DESC, m.id
        LIMIT ?
    """, (limit,)).fetchall()

    node_ids = set()
    nodes = []
    for row in rows:
        node_ids.add(row["id"])
        nodes.append({
            "id": row["id"],
            "summary": (row["summary"] or "")[:60],
            "kind": row["kind"] or "episodic",
            "degree": row["degree"],
        })

    # Get edges between these top nodes only
    if not node_ids:
        return {"nodes": [], "edges": []}

    placeholders = ",".join("?" * len(node_ids))
    edge_rows = conn.execute(f"""
        SELECT source_id, target_id, predicate
        FROM memory_edges
        WHERE source_id IN ({placeholders}) AND target_id IN ({placeholders})
          AND valid_until IS NULL
    """, list(node_ids) + list(node_ids)).fetchall()

    edges = [
        {"source": row["source_id"], "target": row["target_id"], "predicate": row["predicate"]}
        for row in edge_rows
    ]

    return {"nodes": nodes, "edges": edges}


def get_temporal_heatmap(conn: sqlite3.Connection, weeks: int = 26) -> List[Dict[str, Any]]:
    """Get memory creation counts per ISO week for the last N weeks."""
    cutoff = (datetime.now() - timedelta(weeks=weeks)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT strftime('%Y', created_at) as yr,
               strftime('%W', created_at) as wk,
               COUNT(*) as cnt
        FROM memories
        WHERE created_at >= ? AND is_archived = 0
        GROUP BY yr, wk
        ORDER BY yr, wk
    """, (cutoff,)).fetchall()
    return [{"year": row["yr"], "week": row["wk"], "count": row["cnt"]} for row in rows]


def get_foundation_histogram(conn: sqlite3.Connection) -> Dict[str, int]:
    """Distribution of foundation_score across high/medium/low bands."""
    rows = conn.execute("""
        SELECT
            CASE
                WHEN foundation_score >= 0.7 THEN 'high'
                WHEN foundation_score >= 0.4 THEN 'medium'
                WHEN foundation_score IS NOT NULL THEN 'low'
                ELSE 'unscored'
            END as band, COUNT(*) as cnt
        FROM memories WHERE is_archived = 0
        GROUP BY band
    """).fetchall()
    return {row["band"]: row["cnt"] for row in rows}


def get_gap_zones(conn: sqlite3.Connection, threshold: int = 10) -> Dict[str, Any]:
    """Identify projects and tag clusters with very few memories.

    A 'gap' is any project or tag with fewer than `threshold` memories.
    """
    # Sparse projects
    sparse_projects = conn.execute("""
        SELECT COALESCE(source_project, 'untagged') as proj, COUNT(*) as cnt
        FROM memories WHERE is_archived = 0
        GROUP BY proj
        HAVING cnt <= ?
        ORDER BY cnt ASC
    """, (threshold,)).fetchall()

    # Sparse tags (excluding singleton tags which are just unique identifiers)
    sparse_tags = conn.execute("""
        SELECT tag, COUNT(*) as cnt
        FROM memory_tags
        GROUP BY tag
        HAVING cnt > 1 AND cnt <= ?
        ORDER BY cnt ASC
        LIMIT 30
    """, (threshold,)).fetchall()

    return {
        "sparse_projects": [{"name": r["proj"], "count": r["cnt"]} for r in sparse_projects],
        "sparse_tags": [{"name": r["tag"], "count": r["cnt"]} for r in sparse_tags],
    }


def get_staleness_indicator(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Percentage of memories not accessed in 30/60/90 days."""
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN last_accessed < datetime('now', '-30 days') THEN 1 ELSE 0 END) as stale_30,
            SUM(CASE WHEN last_accessed < datetime('now', '-60 days') THEN 1 ELSE 0 END) as stale_60,
            SUM(CASE WHEN last_accessed < datetime('now', '-90 days') THEN 1 ELSE 0 END) as stale_90
        FROM memories WHERE is_archived = 0
    """).fetchone()

    # CRUCIBLE-3 D2-F3: this was `row["total"] or 1  # avoid division by zero`.
    # The guard is correct in INTENT and was then SHIPPED AS A MEASUREMENT: a brain
    # with zero live memories published `total: 1`, every consumer rendered `0/1`
    # and `0.0%` staleness, and an empty brain became indistinguishable from a
    # healthy one with nothing stale. The package already owns the right pattern
    # two files away -- voidloom_hooks_status.py:501-503 returns `entry_count: None`
    # with the comment "UNKNOWN, NOT ZERO. A count over a file that would not parse
    # is not a count of anything." A percentage of nothing is not 0.0%; it does
    # not exist, and None is how this tree says so.
    total = row["total"] or 0
    def _pct(n: int) -> Optional[float]:
        return round((n / total) * 100, 1) if total else None
    return {
        "total": total or None,
        "stale_30_count": row["stale_30"] or 0,
        "stale_60_count": row["stale_60"] or 0,
        "stale_90_count": row["stale_90"] or 0,
        "stale_30_pct": _pct(row["stale_30"] or 0),
        "stale_60_pct": _pct(row["stale_60"] or 0),
        "stale_90_pct": _pct(row["stale_90"] or 0),
    }


def get_summary_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    """High-level summary numbers for the header."""
    total = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    archived = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 1").fetchone()[0]
    # L5-D reader-correctness fix: the dashboard's "total edges" is a snapshot of
    # the CURRENT graph state, so it must exclude invalidated edges. We also
    # surface the invalidated count separately so the bi-temporal retirement is
    # visible (the belief engine + remediation set valid_until on retired edges).
    edges = conn.execute(
        "SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NULL").fetchone()[0]
    invalidated_edges = conn.execute(
        "SELECT COUNT(*) FROM memory_edges WHERE valid_until IS NOT NULL").fetchone()[0]
    tags = conn.execute("SELECT COUNT(DISTINCT tag) FROM memory_tags").fetchone()[0]
    schema_row = conn.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
    schema_version = schema_row[0] if schema_row else "unknown"
    return {
        "total_active": total,
        "archived": archived,
        "total_edges": edges,
        "invalidated_edges": invalidated_edges,
        "unique_tags": tags,
        "schema_version": schema_version,
    }


def get_predicate_distribution(conn: sqlite3.Connection) -> Dict[str, int]:
    """CURRENT edge predicate counts for edge-type breakdown.

    L5-D reader-correctness fix: the predicate breakdown is a view of the live
    graph, so it must exclude invalidated edges (``valid_until IS NULL``).
    Without this, the dashboard would still show the 416 retired `contradicts`
    edges after the belief-engine remediation invalidated them.
    """
    rows = conn.execute(
        "SELECT predicate, COUNT(*) as cnt FROM memory_edges "
        "WHERE valid_until IS NULL GROUP BY predicate ORDER BY cnt DESC"
    ).fetchall()
    return {row["predicate"]: row["cnt"] for row in rows}


def get_map_data(db_path: str) -> Dict[str, Any]:
    """Extract all visualization data from the memory database.

    This is the single entry point for data extraction.
    Returns a dictionary consumable by render_html().
    """
    conn = _get_conn(db_path)
    try:
        data = {
            "generated_at": datetime.now().isoformat(),
            "summary": get_summary_stats(conn),
            "kind_distribution": get_kind_distribution(conn),
            "project_clusters": get_project_clusters(conn),
            "edge_graph": get_edge_graph(conn, limit=100),
            "temporal_heatmap": get_temporal_heatmap(conn, weeks=26),
            "foundation_histogram": get_foundation_histogram(conn),
            "gap_zones": get_gap_zones(conn, threshold=10),
            "staleness": get_staleness_indicator(conn),
            "predicate_distribution": get_predicate_distribution(conn),
        }
    finally:
        conn.close()
    return data


# ---------------------------------------------------------------------------
# Section 2: HTML Rendering (pure function: data -> HTML string)
# ---------------------------------------------------------------------------

_KIND_COLORS = {
    "episodic": "#6366f1",    # indigo
    "semantic": "#10b981",    # emerald
    "procedural": "#f59e0b",  # amber
    "reflection": "#ec4899",  # pink
    "unclassified": "#94a3b8", # slate
}

_PREDICATE_COLORS = {
    "references": "#6366f1",
    "refines": "#10b981",
    "supersedes": "#f59e0b",
    "contradicts": "#ef4444",
    "depends_on": "#8b5cf6",
    "causes": "#06b6d4",
    "generalizes": "#84cc16",
    "instantiates": "#ec4899",
}


def _svg_donut(data: Dict[str, int], colors: Dict[str, str], size: int = 200) -> str:
    """Generate an SVG donut chart."""
    total = sum(data.values())
    if total == 0:
        return '<svg width="200" height="200"><text x="100" y="100" text-anchor="middle">No data</text></svg>'

    cx, cy, r = size // 2, size // 2, size // 2 - 20
    inner_r = r * 0.6
    paths = []
    start_angle = -90  # start at top

    for label, count in data.items():
        if count == 0:
            continue
        sweep = (count / total) * 360
        end_angle = start_angle + sweep

        # SVG arc path
        start_rad = math.radians(start_angle)
        end_rad = math.radians(end_angle)

        x1_outer = cx + r * math.cos(start_rad)
        y1_outer = cy + r * math.sin(start_rad)
        x2_outer = cx + r * math.cos(end_rad)
        y2_outer = cy + r * math.sin(end_rad)
        x1_inner = cx + inner_r * math.cos(end_rad)
        y1_inner = cy + inner_r * math.sin(end_rad)
        x2_inner = cx + inner_r * math.cos(start_rad)
        y2_inner = cy + inner_r * math.sin(start_rad)

        large_arc = 1 if sweep > 180 else 0
        color = colors.get(label, "#94a3b8")

        path = (
            f'<path d="M {x1_outer:.1f} {y1_outer:.1f} '
            f'A {r} {r} 0 {large_arc} 1 {x2_outer:.1f} {y2_outer:.1f} '
            f'L {x1_inner:.1f} {y1_inner:.1f} '
            f'A {inner_r} {inner_r} 0 {large_arc} 0 {x2_inner:.1f} {y2_inner:.1f} Z" '
            f'fill="{color}" stroke="white" stroke-width="2">'
            f'<title>{label}: {count} ({count/total*100:.1f}%)</title></path>'
        )
        paths.append(path)
        start_angle = end_angle

    # Center text
    center_text = f'<text x="{cx}" y="{cy}" text-anchor="middle" dominant-baseline="middle" font-size="24" font-weight="bold" fill="#1e293b">{total}</text>'
    center_label = f'<text x="{cx}" y="{cy + 18}" text-anchor="middle" font-size="11" fill="#64748b">memories</text>'

    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        + "".join(paths) + center_text + center_label + '</svg>'
    )


def _svg_bubble_chart(data: Dict[str, int], max_bubbles: int = 15) -> str:
    """Generate an SVG bubble chart for project clusters."""
    # Take top N projects
    items = sorted(data.items(), key=lambda x: x[1], reverse=True)[:max_bubbles]
    if not items:
        return '<svg width="600" height="300"><text x="300" y="150" text-anchor="middle">No data</text></svg>'

    max_count = items[0][1]
    width, height = 700, 350

    # Simple circle-packing: arrange in rows
    bubbles = []
    colors = ["#6366f1", "#10b981", "#f59e0b", "#ec4899", "#8b5cf6",
              "#06b6d4", "#84cc16", "#ef4444", "#14b8a6", "#f97316",
              "#a855f7", "#3b82f6", "#22c55e", "#eab308", "#64748b"]

    # Calculate radii (proportional to sqrt of count)
    min_r, max_r = 20, 60
    radii = []
    for _, count in items:
        ratio = math.sqrt(count) / math.sqrt(max_count)
        radii.append(min_r + ratio * (max_r - min_r))

    # Place bubbles using simple grid layout
    x_cursor, y_cursor = 70, 80
    row_height = 0
    for i, ((label, count), radius) in enumerate(zip(items, radii)):
        if x_cursor + radius * 2 > width - 30:
            x_cursor = 70
            y_cursor += row_height + 20
            row_height = 0

        cx = x_cursor + radius
        cy = y_cursor + radius
        row_height = max(row_height, radius * 2)

        color = colors[i % len(colors)]
        short_label = label[:12] + "..." if len(label) > 12 else label

        bubbles.append(
            f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{radius:.0f}" '
            f'fill="{color}" fill-opacity="0.75" stroke="{color}" stroke-width="2">'
            f'<title>{label}: {count} memories</title></circle>'
            f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle" '
            f'dominant-baseline="middle" font-size="{max(9, min(12, int(radius/4)))}" '
            f'fill="white" font-weight="600">{short_label}</text>'
            f'<text x="{cx:.0f}" y="{cy + 12:.0f}" text-anchor="middle" '
            f'font-size="9" fill="white">{count}</text>'
        )
        x_cursor += radius * 2 + 15

    actual_height = max(height, y_cursor + max_r + 40)
    return (
        f'<svg width="{width}" height="{actual_height}" viewBox="0 0 {width} {actual_height}">'
        + "".join(bubbles) + '</svg>'
    )


def _render_temporal_heatmap(data: List[Dict[str, Any]]) -> str:
    """Render temporal heatmap as an SVG bar chart."""
    if not data:
        return '<p class="empty">No temporal data available.</p>'

    max_count = max(d["count"] for d in data) if data else 1
    bar_width = max(4, min(20, 700 // max(len(data), 1)))
    chart_height = 160
    width = bar_width * len(data) + 60

    bars = []
    for i, d in enumerate(data):
        h = (d["count"] / max_count) * (chart_height - 30)
        x = 40 + i * bar_width
        y = chart_height - h - 10
        intensity = min(1.0, d["count"] / max_count)
        # Gradient from light blue to deep indigo
        r = int(99 - intensity * 60)
        g = int(102 - intensity * 50)
        b = int(241)
        color = f"rgb({r},{g},{b})"

        label = f"W{d['week']}: {d['count']}"
        bars.append(
            f'<rect x="{x}" y="{y:.0f}" width="{bar_width - 1}" height="{h:.0f}" '
            f'fill="{color}" rx="2"><title>{d["year"]}-W{d["week"]}: {d["count"]} memories</title></rect>'
        )

    # Y-axis labels
    y_labels = (
        f'<text x="35" y="15" text-anchor="end" font-size="9" fill="#64748b">{max_count}</text>'
        f'<text x="35" y="{chart_height - 10}" text-anchor="end" font-size="9" fill="#64748b">0</text>'
    )

    return (
        f'<svg width="{width}" height="{chart_height + 20}" viewBox="0 0 {width} {chart_height + 20}">'
        + y_labels + "".join(bars) + '</svg>'
    )


def _render_foundation_histogram(data: Dict[str, int]) -> str:
    """Render foundation score histogram as horizontal bars."""
    band_colors = {"high": "#10b981", "medium": "#f59e0b", "low": "#ef4444", "unscored": "#94a3b8"}
    band_labels = {"high": "High (>=0.7)", "medium": "Medium (0.4-0.7)", "low": "Low (<0.4)", "unscored": "Unscored"}
    total = sum(data.values()) or 1

    bars_html = []
    for band in ["high", "medium", "low", "unscored"]:
        count = data.get(band, 0)
        pct = (count / total) * 100
        color = band_colors[band]
        label = band_labels[band]
        bars_html.append(
            f'<div class="hist-row">'
            f'<span class="hist-label">{label}</span>'
            f'<div class="hist-bar-bg"><div class="hist-bar" style="width:{pct:.1f}%;background:{color}"></div></div>'
            f'<span class="hist-value">{count} ({pct:.1f}%)</span>'
            f'</div>'
        )
    return "\n".join(bars_html)


def _render_gap_zones(data: Dict[str, Any]) -> str:
    """Render gap zone tables."""
    html_parts = []

    # Sparse projects
    projects = data.get("sparse_projects", [])
    if projects:
        rows = "".join(
            f'<tr><td>{p["name"]}</td><td class="num">{p["count"]}</td></tr>'
            for p in projects[:15]
        )
        html_parts.append(
            f'<div class="gap-section">'
            f'<h4>Sparse Projects (<=10 memories)</h4>'
            f'<table class="gap-table"><thead><tr><th>Project</th><th>Count</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'<p class="gap-note">{len(projects)} projects with <=10 memories</p></div>'
        )
    else:
        html_parts.append('<p class="empty">No sparse projects detected.</p>')

    # Sparse tags
    tags = data.get("sparse_tags", [])
    if tags:
        rows = "".join(
            f'<tr><td>{t["name"]}</td><td class="num">{t["count"]}</td></tr>'
            for t in tags[:15]
        )
        html_parts.append(
            f'<div class="gap-section">'
            f'<h4>Sparse Tag Clusters (2-10 memories)</h4>'
            f'<table class="gap-table"><thead><tr><th>Tag</th><th>Count</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>'
            f'<p class="gap-note">{len(tags)} tag clusters with <=10 memories</p></div>'
        )

    return "\n".join(html_parts)


def _render_staleness(data: Dict[str, Any]) -> str:
    """Render staleness indicator as concentric rings."""
    total = data["total"]
    svg_parts = []
    cx, cy = 120, 120
    rings = [
        ("90+ days", data["stale_90_pct"], data["stale_90_count"], "#ef4444", 100),
        ("60+ days", data["stale_60_pct"], data["stale_60_count"], "#f59e0b", 75),
        ("30+ days", data["stale_30_pct"], data["stale_30_count"], "#6366f1", 50),
    ]

    for label, pct, count, color, radius in rings:
        circumference = 2 * math.pi * radius
        dash = (pct / 100) * circumference
        svg_parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
            f'stroke="#e2e8f0" stroke-width="18" />'
            f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" '
            f'stroke="{color}" stroke-width="18" '
            f'stroke-dasharray="{dash:.1f} {circumference:.1f}" '
            f'stroke-linecap="round" transform="rotate(-90 {cx} {cy})">'
            f'<title>{label}: {count}/{total} ({pct}%)</title></circle>'
        )

    svg = (
        f'<svg width="240" height="240" viewBox="0 0 240 240">'
        + "".join(svg_parts)
        + f'<text x="{cx}" y="{cx}" text-anchor="middle" dominant-baseline="middle" '
        f'font-size="16" font-weight="bold" fill="#1e293b">{total}</text>'
        f'<text x="{cx}" y="{cx+16}" text-anchor="middle" font-size="10" fill="#64748b">total</text>'
        + '</svg>'
    )

    legend = (
        f'<div class="stale-legend">'
        f'<div><span class="dot" style="background:#6366f1"></span>30+ days: {data["stale_30_count"]} ({data["stale_30_pct"]}%)</div>'
        f'<div><span class="dot" style="background:#f59e0b"></span>60+ days: {data["stale_60_count"]} ({data["stale_60_pct"]}%)</div>'
        f'<div><span class="dot" style="background:#ef4444"></span>90+ days: {data["stale_90_count"]} ({data["stale_90_pct"]}%)</div>'
        f'</div>'
    )
    return svg + legend


def _render_edge_legend() -> str:
    """Render legend for edge predicates."""
    items = "".join(
        f'<span class="legend-item"><span class="dot" style="background:{color}"></span>{pred}</span>'
        for pred, color in _PREDICATE_COLORS.items()
    )
    return f'<div class="edge-legend">{items}</div>'


def _render_kind_legend(data: Dict[str, int]) -> str:
    """Render legend below the kind donut."""
    total = sum(data.values()) or 1
    items = "".join(
        f'<div class="legend-row">'
        f'<span class="dot" style="background:{_KIND_COLORS.get(k, "#94a3b8")}"></span>'
        f'<span class="legend-label">{k}</span>'
        f'<span class="legend-value">{v} ({v/total*100:.1f}%)</span>'
        f'</div>'
        for k, v in data.items()
    )
    return f'<div class="kind-legend">{items}</div>'


def render_html(data: Dict[str, Any]) -> str:
    """Render the complete self-contained HTML dashboard from extracted data."""
    summary = data["summary"]
    kind_dist = data["kind_distribution"]
    project_clusters = data["project_clusters"]
    edge_graph = data["edge_graph"]
    temporal = data["temporal_heatmap"]
    foundation = data["foundation_histogram"]
    gaps = data["gap_zones"]
    staleness = data["staleness"]
    predicates = data["predicate_distribution"]

    # SVG components
    kind_donut = _svg_donut(kind_dist, _KIND_COLORS)
    kind_legend = _render_kind_legend(kind_dist)
    bubble_chart = _svg_bubble_chart(project_clusters)
    temporal_chart = _render_temporal_heatmap(temporal)
    foundation_bars = _render_foundation_histogram(foundation)
    gap_html = _render_gap_zones(gaps)
    staleness_html = _render_staleness(staleness)
    edge_legend = _render_edge_legend()

    # Edge graph data for JS force-directed layout
    graph_json = json.dumps(edge_graph, ensure_ascii=False)

    # Predicate summary
    pred_html = "".join(
        f'<span class="pred-chip" style="border-color:{_PREDICATE_COLORS.get(p, "#94a3b8")}">'
        f'{p}: {c}</span>'
        for p, c in predicates.items()
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cognitive Map — Memory Landscape</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #f8fafc; color: #1e293b; line-height: 1.5; padding: 24px; }}
.container {{ max-width: 1200px; margin: 0 auto; }}
h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 4px; }}
.subtitle {{ color: #64748b; font-size: 14px; margin-bottom: 24px; }}
.summary-bar {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 32px;
               background: white; border-radius: 12px; padding: 16px 24px;
               box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.stat-card {{ text-align: center; min-width: 100px; }}
.stat-value {{ font-size: 24px; font-weight: 700; color: #6366f1; }}
.stat-label {{ font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 24px; }}
.card {{ background: white; border-radius: 12px; padding: 24px;
         box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.card h3 {{ font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #334155; }}
.card.full-width {{ grid-column: 1 / -1; }}
.kind-legend {{ margin-top: 12px; }}
.legend-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }}
.legend-label {{ flex: 1; font-size: 13px; }}
.legend-value {{ font-size: 13px; color: #64748b; }}
.dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }}
.stale-legend {{ margin-top: 12px; }}
.stale-legend div {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 13px; }}
.edge-legend {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
.legend-item {{ display: flex; align-items: center; gap: 4px; font-size: 11px; color: #64748b; }}
.pred-chip {{ display: inline-block; padding: 2px 8px; border: 1px solid; border-radius: 12px;
              font-size: 11px; margin: 2px; color: #475569; }}
.hist-row {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
.hist-label {{ width: 130px; font-size: 12px; color: #475569; flex-shrink: 0; }}
.hist-bar-bg {{ flex: 1; height: 20px; background: #f1f5f9; border-radius: 4px; overflow: hidden; }}
.hist-bar {{ height: 100%; border-radius: 4px; transition: width 0.3s; }}
.hist-value {{ width: 90px; font-size: 12px; color: #64748b; text-align: right; flex-shrink: 0; }}
.gap-section {{ margin-bottom: 16px; }}
.gap-section h4 {{ font-size: 13px; font-weight: 600; margin-bottom: 8px; color: #475569; }}
.gap-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.gap-table th {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #e2e8f0; color: #64748b; font-weight: 500; }}
.gap-table td {{ padding: 4px 8px; border-bottom: 1px solid #f1f5f9; }}
.gap-table .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.gap-note {{ font-size: 11px; color: #94a3b8; margin-top: 4px; }}
.empty {{ color: #94a3b8; font-size: 13px; font-style: italic; }}
#graph-canvas {{ width: 100%; height: 400px; border-radius: 8px; background: #f8fafc; cursor: grab; }}
.footer {{ text-align: center; margin-top: 32px; font-size: 11px; color: #94a3b8; }}
@media (max-width: 768px) {{
    .grid {{ grid-template-columns: 1fr; }}
    .summary-bar {{ flex-direction: column; align-items: flex-start; }}
}}
</style>
</head>
<body>
<div class="container">
    <h1>Cognitive Map</h1>
    <p class="subtitle">Memory landscape visualization — generated {data["generated_at"][:19]}</p>

    <div class="summary-bar">
        <div class="stat-card"><div class="stat-value">{summary["total_active"]}</div><div class="stat-label">Active Memories</div></div>
        <div class="stat-card"><div class="stat-value">{summary["total_edges"]}</div><div class="stat-label">Edges</div></div>
        <div class="stat-card"><div class="stat-value">{summary["unique_tags"]}</div><div class="stat-label">Unique Tags</div></div>
        <div class="stat-card"><div class="stat-value">{summary["archived"]}</div><div class="stat-label">Archived</div></div>
        <div class="stat-card"><div class="stat-value">{summary["schema_version"]}</div><div class="stat-label">Schema</div></div>
    </div>

    <div class="grid">
        <!-- S1: Kind Distribution -->
        <div class="card">
            <h3>Kind Distribution</h3>
            <div style="display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;">
                {kind_donut}
                {kind_legend}
            </div>
        </div>

        <!-- S7: Staleness Indicator -->
        <div class="card">
            <h3>Staleness Indicator</h3>
            <div style="display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;">
                {staleness_html}
            </div>
        </div>

        <!-- S5: Foundation Score Histogram -->
        <div class="card">
            <h3>Foundation Score Distribution</h3>
            {foundation_bars}
        </div>

        <!-- S6: Gap Zones -->
        <div class="card">
            <h3>Gap Zones</h3>
            {gap_html}
        </div>

        <!-- S2: Project Clusters -->
        <div class="card full-width">
            <h3>Project Clusters</h3>
            <div style="overflow-x:auto;">
                {bubble_chart}
            </div>
        </div>

        <!-- S4: Temporal Heatmap -->
        <div class="card full-width">
            <h3>Temporal Activity (last 26 weeks)</h3>
            <div style="overflow-x:auto;">
                {temporal_chart}
            </div>
        </div>

        <!-- S3: Edge Graph -->
        <div class="card full-width">
            <h3>Edge Graph (top 100 most-connected memories)</h3>
            {edge_legend}
            <div style="margin-bottom:8px;">{pred_html}</div>
            <canvas id="graph-canvas" width="1100" height="400"></canvas>
        </div>
    </div>

    <p class="footer">Wizard Library L3 — Cognitive Map Dashboard — schema {summary["schema_version"]}</p>
</div>

<script>
// Force-directed graph layout (self-contained, no dependencies)
(function() {{
    const graphData = {graph_json};
    const canvas = document.getElementById('graph-canvas');
    if (!canvas || !graphData.nodes.length) return;

    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;

    const kindColors = {json.dumps(_KIND_COLORS)};
    const predColors = {json.dumps(_PREDICATE_COLORS)};

    // Initialize node positions randomly
    const nodes = graphData.nodes.map((n, i) => ({{
        ...n,
        x: W/2 + (Math.random() - 0.5) * W * 0.6,
        y: H/2 + (Math.random() - 0.5) * H * 0.6,
        vx: 0, vy: 0,
        radius: Math.max(4, Math.min(16, Math.sqrt(n.degree) * 1.5))
    }}));

    const nodeMap = {{}};
    nodes.forEach((n, i) => {{ nodeMap[n.id] = i; }});

    const edges = graphData.edges.filter(e => nodeMap[e.source] !== undefined && nodeMap[e.target] !== undefined);

    // Force simulation parameters
    const ITERATIONS = 120;
    const REPULSION = 800;
    const ATTRACTION = 0.005;
    const DAMPING = 0.85;
    const CENTER_GRAVITY = 0.01;

    // Run simulation
    for (let iter = 0; iter < ITERATIONS; iter++) {{
        // Repulsion between all nodes
        for (let i = 0; i < nodes.length; i++) {{
            for (let j = i + 1; j < nodes.length; j++) {{
                let dx = nodes[j].x - nodes[i].x;
                let dy = nodes[j].y - nodes[i].y;
                let dist = Math.sqrt(dx * dx + dy * dy) || 1;
                let force = REPULSION / (dist * dist);
                let fx = (dx / dist) * force;
                let fy = (dy / dist) * force;
                nodes[i].vx -= fx;
                nodes[i].vy -= fy;
                nodes[j].vx += fx;
                nodes[j].vy += fy;
            }}
        }}

        // Attraction along edges
        for (const e of edges) {{
            const si = nodeMap[e.source];
            const ti = nodeMap[e.target];
            let dx = nodes[ti].x - nodes[si].x;
            let dy = nodes[ti].y - nodes[si].y;
            let dist = Math.sqrt(dx * dx + dy * dy) || 1;
            let force = dist * ATTRACTION;
            let fx = (dx / dist) * force;
            let fy = (dy / dist) * force;
            nodes[si].vx += fx;
            nodes[si].vy += fy;
            nodes[ti].vx -= fx;
            nodes[ti].vy -= fy;
        }}

        // Center gravity
        for (const n of nodes) {{
            n.vx += (W/2 - n.x) * CENTER_GRAVITY;
            n.vy += (H/2 - n.y) * CENTER_GRAVITY;
        }}

        // Apply velocities with damping
        for (const n of nodes) {{
            n.vx *= DAMPING;
            n.vy *= DAMPING;
            n.x += n.vx;
            n.y += n.vy;
            // Boundary constraint
            n.x = Math.max(n.radius + 5, Math.min(W - n.radius - 5, n.x));
            n.y = Math.max(n.radius + 5, Math.min(H - n.radius - 5, n.y));
        }}
    }}

    // Draw
    function draw() {{
        ctx.clearRect(0, 0, W, H);

        // Draw edges
        ctx.globalAlpha = 0.3;
        for (const e of edges) {{
            const si = nodeMap[e.source];
            const ti = nodeMap[e.target];
            ctx.beginPath();
            ctx.moveTo(nodes[si].x, nodes[si].y);
            ctx.lineTo(nodes[ti].x, nodes[ti].y);
            ctx.strokeStyle = predColors[e.predicate] || '#94a3b8';
            ctx.lineWidth = 0.5;
            ctx.stroke();
        }}

        // Draw nodes
        ctx.globalAlpha = 1.0;
        for (const n of nodes) {{
            ctx.beginPath();
            ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);
            ctx.fillStyle = kindColors[n.kind] || '#94a3b8';
            ctx.fill();
            ctx.strokeStyle = 'white';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        }}
    }}

    draw();

    // Tooltip on hover
    canvas.addEventListener('mousemove', function(evt) {{
        const rect = canvas.getBoundingClientRect();
        const mx = (evt.clientX - rect.left) * (W / rect.width);
        const my = (evt.clientY - rect.top) * (H / rect.height);
        let hit = null;
        for (const n of nodes) {{
            const dx = mx - n.x, dy = my - n.y;
            if (dx*dx + dy*dy < n.radius * n.radius * 4) {{
                hit = n;
                break;
            }}
        }}
        canvas.title = hit ? `#${{hit.id}}: ${{hit.summary}} (${{hit.kind}}, ${{hit.degree}} edges)` : '';
        canvas.style.cursor = hit ? 'pointer' : 'grab';
    }});
}})();
</script>
</body>
</html>"""
    return html


# ---------------------------------------------------------------------------
# Section 3: Generation Pipeline
# ---------------------------------------------------------------------------

def generate_map(db_path: str, output_path: str, trigger: str = "manual") -> Dict[str, Any]:
    """Generate the cognitive map HTML file.

    Args:
        db_path: Path to the claude_memory.db file.
        output_path: Where to write the HTML file.
        trigger: What triggered this generation (manual/cron/cmd_map).

    Returns:
        Generation metadata dict (suitable for map_generation_log insert).
    """
    t0 = time.time()
    errors = []

    try:
        data = get_map_data(db_path)
    except Exception as e:
        errors.append(f"Data extraction failed: {e}")
        raise

    try:
        html = render_html(data)
    except Exception as e:
        errors.append(f"HTML rendering failed: {e}")
        raise

    # Write output
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html, encoding="utf-8")

    elapsed_ms = int((time.time() - t0) * 1000)

    # Count sections rendered
    sections_rendered = sum(1 for section in [
        data.get("kind_distribution"),
        data.get("project_clusters"),
        data.get("edge_graph", {}).get("nodes"),
        data.get("temporal_heatmap"),
        data.get("foundation_histogram"),
        data.get("gap_zones"),
        data.get("staleness"),
    ] if section)

    metadata = {
        "output_path": str(output_path),
        "total_memories": data["summary"]["total_active"],
        "total_edges": data["summary"]["total_edges"],
        "sections_rendered": sections_rendered,
        "generation_time_ms": elapsed_ms,
        "trigger": trigger,
        "error_log": "; ".join(errors) if errors else None,
    }

    # Log to map_generation_log if table exists
    try:
        conn = sqlite3.connect(db_path, timeout=60.0)
        conn.execute("""
            INSERT INTO map_generation_log
            (output_path, total_memories, total_edges, sections_rendered, generation_time_ms, trigger, error_log)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            metadata["output_path"],
            metadata["total_memories"],
            metadata["total_edges"],
            metadata["sections_rendered"],
            metadata["generation_time_ms"],
            metadata["trigger"],
            metadata["error_log"],
        ))
        conn.commit()
        conn.close()
    except sqlite3.OperationalError:
        # Table doesn't exist yet (migration not applied) — skip logging
        pass

    return metadata


# ---------------------------------------------------------------------------
# Section 4: CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    """CLI: python3 cognitive_map.py <db_path> [output_path] [--trigger manual|cron|cmd_map]"""
    import argparse
    parser = argparse.ArgumentParser(description="Generate cognitive map HTML dashboard")
    parser.add_argument("db_path", help="Path to claude_memory.db")
    parser.add_argument("output_path", nargs="?", default="cognitive_map.html",
                        help="Output HTML path (default: ./cognitive_map.html)")
    parser.add_argument("--trigger", choices=["manual", "cron", "cmd_map"], default="manual",
                        help="Trigger source recorded in map_generation_log (default: manual)")
    args = parser.parse_args()

    if not Path(args.db_path).exists():
        print(f"Error: Database not found at {args.db_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Generating cognitive map from: {args.db_path} (trigger={args.trigger})")
    result = generate_map(args.db_path, args.output_path, trigger=args.trigger)
    print(f"Done in {result['generation_time_ms']}ms")
    print(f"  Memories: {result['total_memories']}")
    print(f"  Edges: {result['total_edges']}")
    print(f"  Sections: {result['sections_rendered']}")
    print(f"  Output: {result['output_path']}")


if __name__ == "__main__":
    main()
