#!/usr/bin/env python3
"""Claude Code Vectorized Memory System — Single-file CLI tool.

Usage: python3 website_memory.py <command> [args]

Commands:
    init        Create/verify database
    store       Store a new memory with embedding
    recall      Semantic search for relevant memories
    list        List memories with filters
    update      Update an existing memory
    delete      Soft-delete (archive) a memory
    link        Create relationship between memories
    stats       Database statistics dashboard
    maintain    Decay, deduplicate, prune
    migrate     Import from existing .md files
    context     Session-start context loading
"""

import argparse
import json
import os
import re
import sqlite3
import struct
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# rho-2: the pin is RESOLVED, never hardcoded. apply_cuda_pin() reads
# WIZARD_CUDA_DEVICE (and the declaring env-file/config) and falls back to
# PACKAGE_SAFE_DEFAULT = "0" -- correct on every machine but the author's,
# whose env file declares 1. It must run BEFORE torch is imported: CUDA reads
# CUDA_VISIBLE_DEVICES at initialisation and a later set fails SILENTLY on
# device 0. This import is deliberately NOT guarded: a missing resolver must
# not degrade to "no pin", because unset means torch takes device 0, which on
# a two-GPU workstation may be the display card, and that eviction is silent.
try:
    import voidloom_device
except ImportError as _e:  # pragma: no cover - an incomplete install
    raise ImportError(
        "voidloom_device is required before any GPU work and is missing (%s). "
        "Refusing to continue: without it CUDA_VISIBLE_DEVICES is unset and "
        "torch takes device 0, which on a two-GPU workstation may be the "
        "display card. Reinstall the package, or export WIZARD_CUDA_DEVICE "
        "explicitly." % (_e,)) from _e
voidloom_device.apply_cuda_pin()

DB_PATH = Path(__file__).parent.parent / "website_memory.db"
SCHEMA_PATH = Path(__file__).parent.parent / "migrations" / "schema.sql"
NOMIC_MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
NOMIC_DIM = 768

CATEGORIES = [
    "user_preference", "technical_decision", "project_knowledge",
    "error_pattern", "skill_lesson", "workflow_pattern",
    "people_context", "tool_optimization", "domain_knowledge",
    "campaign_state", "correction", "insight",
]

_model_cache = {}


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


def _output(data: dict):
    print(json.dumps(data, ensure_ascii=False))


def _error(msg: str):
    _output({"status": "error", "message": msg})
    sys.exit(1)


def serialize_f32(vec) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _load_model():
    if "model" in _model_cache:
        return _model_cache["model"]
    try:
        from sentence_transformers import SentenceTransformer
        import torch
        device = _torch_device(torch)
        model = SentenceTransformer(NOMIC_MODEL_NAME, revision="e9b6763023c676ca8431644204f50c2b100d9aab", trust_remote_code=True, device=device)
        _model_cache["model"] = model
        return model
    except Exception as e:
        _error(f"Failed to load embedding model: {e}")


def _encode(text: str, prefix: str = "search_document") -> bytes:
    model = _load_model()
    import numpy as np
    query = f"{prefix}: {text}"
    vec = model.encode(query, normalize_embeddings=True)
    return serialize_f32(vec[:NOMIC_DIM].astype(np.float32).tolist())


def _get_conn() -> sqlite3.Connection:
    if not DB_PATH.exists():
        _error(f"Database not found at {DB_PATH}. Run 'init' first.")
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("SELECT vec_version()")
    except sqlite3.OperationalError:
        import sqlite_vec
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    return conn


def cmd_init(args):
    t0 = time.time()
    conn = sqlite3.connect(str(DB_PATH), timeout=60.0)
    conn.enable_load_extension(True)
    import sqlite_vec
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)

    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_embeddings
        USING vec0(
            memory_id INTEGER PRIMARY KEY,
            embedding float[{NOMIC_DIM}]
        )
    """)

    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("schema_version", "1.0"),
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("created_at", datetime.now().isoformat()),
    )
    conn.commit()

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()]
    conn.close()

    _output({
        "status": "ok",
        "action": "init",
        "database": str(DB_PATH),
        "tables": tables,
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_store(args):
    t0 = time.time()
    conn = _get_conn()

    category = args.category
    if category not in CATEGORIES:
        _error(f"Invalid category '{category}'. Valid: {CATEGORIES}")

    summary = args.summary
    if not summary:
        summary = args.content[:100] + ("..." if len(args.content) > 100 else "")

    embedding = _encode(args.content, "search_document")

    cursor = conn.execute(
        """INSERT INTO memories (content, summary, category, importance, source_project, source_context)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (args.content, summary, category, args.importance, args.project, args.context),
    )
    memory_id = cursor.lastrowid

    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
        (memory_id, embedding),
    )

    if args.tags:
        for tag in args.tags.split(","):
            tag = tag.strip()
            if tag:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (memory_id, tag),
                )

    conn.commit()
    conn.close()

    _output({
        "status": "ok",
        "action": "store",
        "memory_id": memory_id,
        "category": category,
        "importance": args.importance,
        "tags": [t.strip() for t in args.tags.split(",")] if args.tags else [],
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_recall(args):
    t0 = time.time()
    conn = _get_conn()

    query_vec = _encode(args.query, "search_query")

    sql = """
        SELECT
            emb.memory_id,
            emb.distance,
            m.content,
            m.summary,
            m.category,
            m.importance,
            m.source_project,
            m.access_count,
            m.created_at,
            m.last_accessed
        FROM memory_embeddings AS emb
        JOIN memories AS m ON m.id = emb.memory_id
        WHERE emb.embedding MATCH ?
          AND k = ?
    """
    params = [query_vec, args.top_k * 3]

    rows = conn.execute(sql, params).fetchall()

    results = []
    for r in rows:
        mem_id, distance, content, summary, category, importance, project, access_count, created, accessed = r

        archived = conn.execute("SELECT is_archived FROM memories WHERE id = ?", (mem_id,)).fetchone()
        if archived and archived[0]:
            continue

        if args.category and category != args.category:
            continue
        if args.project and project and args.project not in (project or "") and args.project not in str(project):
            continue
        if args.threshold and distance > args.threshold:
            continue

        tags = [t[0] for t in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (mem_id,)
        ).fetchall()]

        results.append({
            "id": mem_id,
            "content": content,
            "summary": summary,
            "category": category,
            "importance": importance,
            "distance": round(distance, 4),
            "tags": tags,
            "source_project": project,
            "access_count": access_count,
            "created_at": created,
        })

        if len(results) >= args.top_k:
            break

    for r in results:
        conn.execute(
            "UPDATE memories SET last_accessed = datetime('now'), access_count = access_count + 1 WHERE id = ?",
            (r["id"],),
        )
        conn.execute(
            "INSERT INTO access_log (memory_id, access_type, query_text, relevance_score) VALUES (?, ?, ?, ?)",
            (r["id"], "recall", args.query, r["distance"]),
        )

    conn.commit()
    conn.close()

    _output({
        "status": "ok",
        "action": "recall",
        "query": args.query,
        "results": results,
        "result_count": len(results),
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_list(args):
    t0 = time.time()
    conn = _get_conn()

    clauses = ["is_archived = 0"]
    params = []

    if args.category:
        clauses.append("category = ?")
        params.append(args.category)
    if args.project:
        clauses.append("(source_project = ? OR source_project LIKE ?)")
        params.append(args.project)
        params.append(f"%{args.project}%")
    if args.tag:
        clauses.append("id IN (SELECT memory_id FROM memory_tags WHERE tag = ?)")
        params.append(args.tag)

    where = "WHERE " + " AND ".join(clauses)
    order = "ORDER BY created_at DESC"
    if args.sort == "importance":
        order = "ORDER BY importance DESC"
    elif args.sort == "accessed":
        order = "ORDER BY last_accessed DESC"
    elif args.sort == "access_count":
        order = "ORDER BY access_count DESC"

    limit = args.limit or 20
    params.append(limit)

    sql = f"SELECT id, content, summary, category, importance, source_project, access_count, created_at, last_accessed FROM memories {where} {order} LIMIT ?"
    rows = conn.execute(sql, params).fetchall()

    results = []
    for r in rows:
        tags = [t[0] for t in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (r[0],)
        ).fetchall()]
        results.append({
            "id": r[0],
            "summary": r[2] or r[1][:100],
            "category": r[3],
            "importance": r[4],
            "source_project": r[5],
            "access_count": r[6],
            "created_at": r[7],
            "last_accessed": r[8],
            "tags": tags,
        })

    conn.close()

    _output({
        "status": "ok",
        "action": "list",
        "results": results,
        "result_count": len(results),
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_update(args):
    t0 = time.time()
    conn = _get_conn()

    mem = conn.execute("SELECT id, content FROM memories WHERE id = ?", (args.id,)).fetchone()
    if not mem:
        conn.close()
        _error(f"Memory {args.id} not found")

    updates = []
    params = []

    if args.content:
        updates.append("content = ?")
        params.append(args.content)
        updates.append("version = version + 1")

    if args.summary:
        updates.append("summary = ?")
        params.append(args.summary)

    if args.importance is not None:
        updates.append("importance = ?")
        params.append(args.importance)

    if args.category:
        if args.category not in CATEGORIES:
            conn.close()
            _error(f"Invalid category '{args.category}'")
        updates.append("category = ?")
        params.append(args.category)

    if not updates:
        conn.close()
        _error("No updates specified")

    updates.append("updated_at = datetime('now')")
    params.append(args.id)

    conn.execute(f"UPDATE memories SET {', '.join(updates)} WHERE id = ?", params)

    if args.content:
        embedding = _encode(args.content, "search_document")
        conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (args.id,))
        conn.execute(
            "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
            (args.id, embedding),
        )

    if args.tags:
        conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (args.id,))
        for tag in args.tags.split(","):
            tag = tag.strip()
            if tag:
                conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (args.id, tag),
                )

    conn.commit()
    conn.close()

    _output({
        "status": "ok",
        "action": "update",
        "memory_id": args.id,
        "re_embedded": args.content is not None,
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_delete(args):
    t0 = time.time()
    conn = _get_conn()

    mem = conn.execute("SELECT id FROM memories WHERE id = ?", (args.id,)).fetchone()
    if not mem:
        conn.close()
        _error(f"Memory {args.id} not found")

    if args.hard:
        conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (args.id,))
        conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (args.id,))
        conn.execute("DELETE FROM memory_links WHERE source_id = ? OR target_id = ?", (args.id, args.id))
        conn.execute("DELETE FROM access_log WHERE memory_id = ?", (args.id,))
        conn.execute("DELETE FROM memories WHERE id = ?", (args.id,))
    else:
        conn.execute("UPDATE memories SET is_archived = 1, updated_at = datetime('now') WHERE id = ?", (args.id,))

    conn.commit()
    conn.close()

    _output({
        "status": "ok",
        "action": "delete",
        "memory_id": args.id,
        "hard_delete": args.hard,
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_link(args):
    t0 = time.time()
    conn = _get_conn()

    valid_types = ["related", "supersedes", "contradicts", "elaborates"]
    if args.type not in valid_types:
        conn.close()
        _error(f"Invalid link type '{args.type}'. Valid: {valid_types}")

    for mid in [args.source, args.target]:
        if not conn.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone():
            conn.close()
            _error(f"Memory {mid} not found")

    conn.execute(
        """INSERT OR REPLACE INTO memory_links (source_id, target_id, link_type, strength)
           VALUES (?, ?, ?, ?)""",
        (args.source, args.target, args.type, args.strength),
    )
    conn.commit()
    conn.close()

    _output({
        "status": "ok",
        "action": "link",
        "source_id": args.source,
        "target_id": args.target,
        "link_type": args.type,
        "strength": args.strength,
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_stats(args):
    t0 = time.time()
    conn = _get_conn()

    total = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    archived = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 1").fetchone()[0]
    tags_count = conn.execute("SELECT COUNT(DISTINCT tag) FROM memory_tags").fetchone()[0]
    links_count = conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]

    by_category = {}
    for row in conn.execute(
        "SELECT category, COUNT(*) FROM memories WHERE is_archived = 0 GROUP BY category ORDER BY COUNT(*) DESC"
    ).fetchall():
        by_category[row[0]] = row[1]

    by_project = {}
    for row in conn.execute(
        "SELECT COALESCE(source_project, 'untagged'), COUNT(*) FROM memories WHERE is_archived = 0 GROUP BY source_project ORDER BY COUNT(*) DESC"
    ).fetchall():
        by_project[row[0]] = row[1]

    top_tags = {}
    for row in conn.execute(
        "SELECT tag, COUNT(*) FROM memory_tags GROUP BY tag ORDER BY COUNT(*) DESC LIMIT 15"
    ).fetchall():
        top_tags[row[0]] = row[1]

    recent = conn.execute(
        "SELECT id, summary, category, importance, created_at FROM memories WHERE is_archived = 0 ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    most_accessed = conn.execute(
        "SELECT id, summary, category, access_count FROM memories WHERE is_archived = 0 ORDER BY access_count DESC LIMIT 5"
    ).fetchall()

    importance_dist = {}
    for row in conn.execute(
        """SELECT
            CASE
                WHEN importance >= 0.9 THEN 'critical (0.9-1.0)'
                WHEN importance >= 0.7 THEN 'important (0.7-0.8)'
                WHEN importance >= 0.5 THEN 'useful (0.5-0.6)'
                ELSE 'low (0.0-0.4)'
            END as bucket, COUNT(*)
           FROM memories WHERE is_archived = 0
           GROUP BY bucket ORDER BY bucket"""
    ).fetchall():
        importance_dist[row[0]] = row[1]

    embedding_count = 0
    try:
        embedding_count = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
    except Exception:
        pass

    conn.close()

    _output({
        "status": "ok",
        "action": "stats",
        "total_memories": total,
        "archived_memories": archived,
        "embedded_memories": embedding_count,
        "unique_tags": tags_count,
        "total_links": links_count,
        "by_category": by_category,
        "by_project": by_project,
        "top_tags": top_tags,
        "importance_distribution": importance_dist,
        "recent_memories": [
            {"id": r[0], "summary": r[1], "category": r[2], "importance": r[3], "created_at": r[4]}
            for r in recent
        ],
        "most_accessed": [
            {"id": r[0], "summary": r[1], "category": r[2], "access_count": r[3]}
            for r in most_accessed
        ],
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_maintain(args):
    t0 = time.time()
    conn = _get_conn()
    actions = []

    # 1. Decay: reduce importance based on time since last access
    now = datetime.now()
    memories = conn.execute(
        "SELECT id, importance, last_accessed, access_count FROM memories WHERE is_archived = 0"
    ).fetchall()

    decayed = 0
    archived_count = 0
    for mem_id, importance, last_accessed, access_count in memories:
        if last_accessed:
            try:
                la = datetime.fromisoformat(last_accessed)
            except ValueError:
                continue
            days = (now - la).days
            decay_factor = max(0.1, 1.0 - (days / 365) * (1.0 / max(access_count, 1)))
            effective = importance * decay_factor

            if effective < 0.05:
                conn.execute("UPDATE memories SET is_archived = 1, updated_at = datetime('now') WHERE id = ?", (mem_id,))
                archived_count += 1
            elif decay_factor < 0.9:
                decayed += 1

    if archived_count:
        actions.append(f"Archived {archived_count} memories below threshold")
    if decayed:
        actions.append(f"Identified {decayed} memories with decay")

    # 2. Deduplication: find near-identical memories in same category
    deduped = 0
    linked = 0
    if not args.skip_dedup:
        categories = [r[0] for r in conn.execute(
            "SELECT DISTINCT category FROM memories WHERE is_archived = 0"
        ).fetchall()]

        for cat in categories:
            mems = conn.execute(
                "SELECT id, content FROM memories WHERE category = ? AND is_archived = 0",
                (cat,),
            ).fetchall()

            if len(mems) < 2:
                continue

            embeddings = {}
            for mem_id, content in mems:
                row = conn.execute(
                    "SELECT embedding FROM memory_embeddings WHERE memory_id = ?", (mem_id,)
                ).fetchone()
                if row:
                    vec = struct.unpack(f"{NOMIC_DIM}f", row[0])
                    embeddings[mem_id] = vec

            ids = list(embeddings.keys())
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    v1 = embeddings[ids[i]]
                    v2 = embeddings[ids[j]]
                    dot = sum(a * b for a, b in zip(v1, v2))

                    distance = 1.0 - dot
                    if distance < 0.15:
                        imp_i = conn.execute("SELECT importance FROM memories WHERE id = ?", (ids[i],)).fetchone()[0]
                        imp_j = conn.execute("SELECT importance FROM memories WHERE id = ?", (ids[j],)).fetchone()[0]
                        keep, remove = (ids[i], ids[j]) if imp_i >= imp_j else (ids[j], ids[i])

                        tags_r = [t[0] for t in conn.execute(
                            "SELECT tag FROM memory_tags WHERE memory_id = ?", (remove,)
                        ).fetchall()]
                        for tag in tags_r:
                            conn.execute(
                                "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                                (keep, tag),
                            )

                        conn.execute("UPDATE memories SET is_archived = 1 WHERE id = ?", (remove,))
                        deduped += 1
                    elif distance < 0.30:
                        existing = conn.execute(
                            "SELECT id FROM memory_links WHERE source_id = ? AND target_id = ? AND link_type = 'related'",
                            (ids[i], ids[j]),
                        ).fetchone()
                        if not existing:
                            conn.execute(
                                "INSERT OR IGNORE INTO memory_links (source_id, target_id, link_type, strength) VALUES (?, ?, 'related', ?)",
                                (ids[i], ids[j], 1.0 - distance),
                            )
                            linked += 1

    if deduped:
        actions.append(f"Deduplicated {deduped} near-identical memories")
    if linked:
        actions.append(f"Created {linked} similarity links")

    conn.commit()
    conn.close()

    _output({
        "status": "ok",
        "action": "maintain",
        "archived": archived_count,
        "decayed": decayed,
        "deduplicated": deduped,
        "linked": linked,
        "actions": actions,
        "time_ms": int((time.time() - t0) * 1000),
    })


def _parse_markdown_memories(filepath: Path) -> List[Dict[str, Any]]:
    """Extract individual memories from a markdown file."""
    text = filepath.read_text(encoding="utf-8", errors="replace")
    filename = filepath.stem.lower().replace("-", "_")
    memories = []

    category_map = {
        "memory": "project_knowledge",
        "user_preferences": "user_preference",
        "user-preferences": "user_preference",
        "skill_and_agent_patterns": "skill_lesson",
        "skill-and-agent-patterns": "skill_lesson",
        "campaign_content_orchestration": "campaign_state",
        "campaign-content-orchestration": "campaign_state",
        "bitly_and_tracking": "workflow_pattern",
        "bitly-and-tracking": "workflow_pattern",
    }
    default_category = category_map.get(filename, "project_knowledge")

    sections = re.split(r'\n##\s+', text)
    for section in sections:
        section = section.strip()
        if not section or len(section) < 20:
            continue

        lines = section.split("\n")
        title = lines[0].strip().lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()

        if not body:
            body = title

        subsections = re.split(r'\n###\s+', body)
        if len(subsections) > 1:
            for sub in subsections:
                sub = sub.strip()
                if not sub or len(sub) < 15:
                    continue
                sub_lines = sub.split("\n")
                sub_title = sub_lines[0].strip()
                sub_body = "\n".join(sub_lines[1:]).strip()
                content = f"{title} — {sub_title}\n{sub_body}" if sub_body else f"{title} — {sub_title}"
                if len(content) > 30:
                    memories.append({
                        "content": content[:4000],
                        "summary": f"{title}: {sub_title}"[:200],
                        "category": default_category,
                        "importance": 0.6,
                        "tags": [filename, "migrated"],
                        "source_file": str(filepath),
                    })
        else:
            content = f"{title}\n{body}" if body != title else title
            if len(content) > 30:
                memories.append({
                    "content": content[:4000],
                    "summary": title[:200],
                    "category": default_category,
                    "importance": 0.6,
                    "tags": [filename, "migrated"],
                    "source_file": str(filepath),
                })

    return memories


def cmd_migrate(args):
    t0 = time.time()
    conn = _get_conn()

    source_paths = []
    if args.source:
        for pattern in args.source:
            from glob import glob
            source_paths.extend(glob(pattern, recursive=True))
    else:
        default_pattern = str(Path.home() / ".claude" / "projects" / "*" / "memory" / "*.md")
        from glob import glob
        source_paths = glob(default_pattern, recursive=True)

    md_files = [Path(p) for p in source_paths if p.endswith(".md") and Path(p).exists()]

    if not md_files:
        conn.close()
        _output({
            "status": "ok",
            "action": "migrate",
            "message": "No .md files found to migrate",
            "files_found": 0,
            "memories_imported": 0,
        })
        return

    total_imported = 0
    file_results = []

    for md_file in md_files:
        memories = _parse_markdown_memories(md_file)
        file_count = 0

        for mem in memories:
            embedding = _encode(mem["content"], "search_document")

            project = None
            parts = str(md_file).split("/")
            for i, part in enumerate(parts):
                if part == "projects" and i + 1 < len(parts):
                    project = parts[i + 1]
                    break

            cursor = conn.execute(
                """INSERT INTO memories (content, summary, category, importance, source_project, source_context)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (mem["content"], mem["summary"], mem["category"], mem["importance"],
                 project, f"Migrated from {md_file.name}"),
            )
            memory_id = cursor.lastrowid

            conn.execute(
                "INSERT INTO memory_embeddings (memory_id, embedding) VALUES (?, ?)",
                (memory_id, embedding),
            )

            for tag in mem.get("tags", []):
                conn.execute(
                    "INSERT OR IGNORE INTO memory_tags (memory_id, tag) VALUES (?, ?)",
                    (memory_id, tag),
                )

            file_count += 1
            total_imported += 1

        file_results.append({"file": str(md_file), "memories_extracted": file_count})
        conn.commit()

    conn.close()

    _output({
        "status": "ok",
        "action": "migrate",
        "files_processed": len(md_files),
        "memories_imported": total_imported,
        "file_details": file_results,
        "time_ms": int((time.time() - t0) * 1000),
    })


def cmd_context(args):
    t0 = time.time()
    conn = _get_conn()

    limit = args.limit or 15
    results = []

    # High-importance memories for this project
    clauses = ["is_archived = 0"]
    params = []
    if args.project:
        clauses.append("(source_project = ? OR source_project LIKE ? OR source_project IS NULL)")
        params.append(args.project)
        params.append(f"%{args.project}%")

    where = "WHERE " + " AND ".join(clauses)

    # Get top memories by combined importance and recency
    sql = f"""
        SELECT id, content, summary, category, importance, source_project, access_count, created_at, last_accessed
        FROM memories
        {where}
        ORDER BY importance DESC, last_accessed DESC
        LIMIT ?
    """
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()

    for r in rows:
        tags = [t[0] for t in conn.execute(
            "SELECT tag FROM memory_tags WHERE memory_id = ?", (r[0],)
        ).fetchall()]
        results.append({
            "id": r[0],
            "content": r[1],
            "summary": r[2],
            "category": r[3],
            "importance": r[4],
            "source_project": r[5],
            "access_count": r[6],
            "tags": tags,
        })

        conn.execute(
            "UPDATE memories SET last_accessed = datetime('now'), access_count = access_count + 1 WHERE id = ?",
            (r[0],),
        )

    # Stats summary
    total = conn.execute("SELECT COUNT(*) FROM memories WHERE is_archived = 0").fetchone()[0]
    categories = {}
    for row in conn.execute(
        "SELECT category, COUNT(*) FROM memories WHERE is_archived = 0 GROUP BY category"
    ).fetchall():
        categories[row[0]] = row[1]

    conn.commit()
    conn.close()

    _output({
        "status": "ok",
        "action": "context",
        "project": args.project,
        "memories_loaded": len(results),
        "total_memories": total,
        "categories": categories,
        "results": results,
        "time_ms": int((time.time() - t0) * 1000),
    })


def main():
    parser = argparse.ArgumentParser(description="Claude Code Vectorized Memory System")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # init
    subparsers.add_parser("init", help="Create/verify database")

    # store
    p_store = subparsers.add_parser("store", help="Store a new memory")
    p_store.add_argument("--content", "-c", required=True, help="Memory content")
    p_store.add_argument("--category", required=True, choices=CATEGORIES, help="Category")
    p_store.add_argument("--tags", "-t", default="", help="Comma-separated tags")
    p_store.add_argument("--importance", "-i", type=float, default=0.5, help="0.0-1.0")
    p_store.add_argument("--project", "-p", default=None, help="Source project")
    p_store.add_argument("--context", default=None, help="What triggered this memory")
    p_store.add_argument("--summary", "-s", default=None, help="One-line summary")

    # recall
    p_recall = subparsers.add_parser("recall", help="Semantic search")
    p_recall.add_argument("--query", "-q", required=True, help="Search query")
    p_recall.add_argument("--top_k", "-k", type=int, default=5, help="Max results")
    p_recall.add_argument("--threshold", type=float, default=None, help="Max distance threshold")
    p_recall.add_argument("--category", default=None, help="Filter by category")
    p_recall.add_argument("--project", default=None, help="Filter by project")

    # list
    p_list = subparsers.add_parser("list", help="List memories")
    p_list.add_argument("--category", default=None, help="Filter by category")
    p_list.add_argument("--project", default=None, help="Filter by project")
    p_list.add_argument("--tag", default=None, help="Filter by tag")
    p_list.add_argument("--limit", "-n", type=int, default=20, help="Max results")
    p_list.add_argument("--sort", default="created", choices=["created", "importance", "accessed", "access_count"])

    # update
    p_update = subparsers.add_parser("update", help="Update a memory")
    p_update.add_argument("--id", required=True, type=int, help="Memory ID")
    p_update.add_argument("--content", default=None, help="New content (re-embeds)")
    p_update.add_argument("--summary", default=None, help="New summary")
    p_update.add_argument("--importance", type=float, default=None, help="New importance")
    p_update.add_argument("--category", default=None, help="New category")
    p_update.add_argument("--tags", default=None, help="Replace tags (comma-separated)")

    # delete
    p_delete = subparsers.add_parser("delete", help="Archive/delete a memory")
    p_delete.add_argument("--id", required=True, type=int, help="Memory ID")
    p_delete.add_argument("--hard", action="store_true", help="Permanent delete")

    # link
    p_link = subparsers.add_parser("link", help="Link two memories")
    p_link.add_argument("--source", required=True, type=int, help="Source memory ID")
    p_link.add_argument("--target", required=True, type=int, help="Target memory ID")
    p_link.add_argument("--type", required=True, choices=["related", "supersedes", "contradicts", "elaborates"])
    p_link.add_argument("--strength", type=float, default=0.5, help="0.0-1.0")

    # stats
    subparsers.add_parser("stats", help="Database statistics")

    # maintain
    p_maintain = subparsers.add_parser("maintain", help="Decay, dedup, prune")
    p_maintain.add_argument("--skip-dedup", action="store_true", help="Skip deduplication")

    # migrate
    p_migrate = subparsers.add_parser("migrate", help="Import from .md files")
    p_migrate.add_argument("--source", nargs="*", help="Glob patterns for .md files")

    # context
    p_context = subparsers.add_parser("context", help="Session-start context load")
    p_context.add_argument("--project", "-p", default=None, help="Project name")
    p_context.add_argument("--limit", "-n", type=int, default=15, help="Max memories")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmd_map = {
        "init": cmd_init,
        "store": cmd_store,
        "recall": cmd_recall,
        "list": cmd_list,
        "update": cmd_update,
        "delete": cmd_delete,
        "link": cmd_link,
        "stats": cmd_stats,
        "maintain": cmd_maintain,
        "migrate": cmd_migrate,
        "context": cmd_context,
    }

    cmd_map[args.command](args)


if __name__ == "__main__":
    main()
