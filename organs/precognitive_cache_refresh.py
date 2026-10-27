#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""precognitive_cache_refresh.py — the pre-cognitive async-cache cron entry point.

This is the ONLY place the embedding model loads for pre-cognitive recall.
It runs `claude_memory.py anticipate --log`, which:
  (1) collects cheap signals, computes a focus vector (LOADS THE MODEL),
  (2) runs anticipatory recall, and
  (3) writes a row into `pre_cognitive_recall_log` (so the table stops being
      empty — the core Loop D problem).
It then captures the anticipate JSON envelope and writes a SMALL cache file
(`pre_cognitive_cache.json`) holding the top anticipated memory ids+summaries.
The session-start hook reads ONLY this cache (no model) to paint the banner's
"anticipated" line.

WHY a wrapper instead of pointing ExecStart straight at `anticipate --log`:
  - We need BOTH side effects: the DB log row AND the cache file. `anticipate`
    emits the JSON to stdout but does not persist a cache; this wrapper does
    the persistence (atomic write) and adds the cache metadata (generated_at,
    ttl_hours) the reader's freshness check expects.
  - It keeps the systemd unit a single clean ExecStart and gives a clear,
    debuggable journal line.

The wrapper invokes claude_memory.py as a SUBPROCESS (not import) so the cron's
model load lives in a child process and cannot leak heavy modules back into any
caller — and so a model-load failure degrades to "no fresh cache" rather than
crashing the timer. Exit code is ALWAYS 0 on a handled failure (the timer must
not enter a failed state for a transient model/disk hiccup); the cache simply
stays stale and the banner omits the anticipated line.

USAGE (what the systemd ExecStart runs):
    precognitive_cache_refresh.py <db_path> [--cache <path>] [--top-k N]
        [--cli <claude_memory.py>] [--python <interpreter>]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TTL_HOURS = 24
DEFAULT_TOP_K = 15
DEFAULT_CACHE_NAME = "pre_cognitive_cache.json"
# How many anticipated memories to keep in the cache. The banner shows ~2, but
# we keep a few more so other cheap surfaces can use them without a re-run.
CACHE_KEEP = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, obj: dict) -> None:
    """Write JSON atomically (tmp file + os.replace) so a reader never sees a
    half-written cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pcc_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _write_stale_marker(cache: Path, reason: str, ttl_hours: int) -> None:
    """On a handled failure, refresh only the cache's diagnostics WITHOUT
    presenting stale guesses as fresh. We deliberately do NOT bump generated_at
    of any real data; instead we write an empty-memories cache so the reader's
    TTL/empty checks naturally suppress the anticipated line."""
    obj = {
        "generated_at": _now_iso(),
        "ttl_hours": ttl_hours,
        "source": "wizard-library-pre-cognitive (refresh failed-soft)",
        "ok": False,
        "error": reason[:300],
        "memories_loaded": 0,
        "memories": [],
    }
    try:
        _atomic_write_json(cache, obj)
    except Exception:
        pass  # even the marker is best-effort


def main(argv) -> int:
    ap = argparse.ArgumentParser(description="Pre-cognitive async cache refresh")
    ap.add_argument("db", help="Path to claude_memory.db")
    ap.add_argument("--cache", default=None,
                    help="Cache file path (default: <db dir>/pre_cognitive_cache.json "
                         "or $VOIDLOOM_PRECOG_CACHE)")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K,
                    help=f"anticipate --top-k (default {DEFAULT_TOP_K})")
    ap.add_argument("--ttl-hours", type=int, default=DEFAULT_TTL_HOURS,
                    help=f"cache freshness TTL the reader honors (default {DEFAULT_TTL_HOURS})")
    ap.add_argument("--cli", default=None,
                    help="Path to claude_memory.py (default: alongside this script)")
    ap.add_argument("--python", default=None,
                    help="Interpreter to run the CLI (default: this interpreter)")
    ap.add_argument("--session-id", default=None,
                    help="Optional session id stamped into the anticipation log row")
    args = ap.parse_args(argv)

    here = Path(__file__).resolve().parent
    db = Path(args.db)
    cli = Path(args.cli) if args.cli else (here / "claude_memory.py")
    py = args.python or sys.executable
    cache = Path(
        args.cache
        or os.environ.get("VOIDLOOM_PRECOG_CACHE")
        or (db.parent / DEFAULT_CACHE_NAME)
    )

    if not db.exists():
        _write_stale_marker(cache, f"db not found: {db}", args.ttl_hours)
        print(f"[pre-cognitive] db not found: {db} — wrote empty cache, exiting 0",
              file=sys.stderr)
        return 0
    if not cli.exists():
        _write_stale_marker(cache, f"cli not found: {cli}", args.ttl_hours)
        print(f"[pre-cognitive] cli not found: {cli} — wrote empty cache, exiting 0",
              file=sys.stderr)
        return 0

    cmd = [
        str(py), "-u", str(cli), "anticipate",
        "--log",                      # <-- persists a pre_cognitive_recall_log row
        "--top-k", str(args.top_k),
    ]
    if args.session_id:
        cmd += ["--session-id", args.session_id]

    # The CLI resolves its DB relative to its own location / env. We export the
    # env the CLI honors so it targets the intended brain explicitly.
    env = dict(os.environ)
    env["WIZARD_LIBRARY_DB"] = str(db)
    env.setdefault("WIZARD_LIBRARY_HOME", str(cli.parent))

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, env=env,
            cwd=str(cli.parent), timeout=600,
        )
    except subprocess.TimeoutExpired:
        _write_stale_marker(cache, "anticipate timed out (600s)", args.ttl_hours)
        print("[pre-cognitive] anticipate timed out — wrote empty cache, exiting 0",
              file=sys.stderr)
        return 0
    except Exception as exc:
        _write_stale_marker(cache, f"anticipate spawn failed: {exc}", args.ttl_hours)
        print(f"[pre-cognitive] spawn failed: {exc} — wrote empty cache, exiting 0",
              file=sys.stderr)
        return 0

    if proc.returncode != 0:
        _write_stale_marker(
            cache,
            f"anticipate rc={proc.returncode}: {(proc.stderr or '')[:200]}",
            args.ttl_hours,
        )
        print(f"[pre-cognitive] anticipate rc={proc.returncode}; stderr:\n{proc.stderr}",
              file=sys.stderr)
        return 0

    # Parse the JSON envelope from stdout. `_output` prints a single JSON object;
    # be tolerant of any leading banner noise by locating the last top-level {...}.
    payload = _extract_json(proc.stdout)
    if payload is None or payload.get("status") != "ok":
        _write_stale_marker(cache, "anticipate produced no parseable JSON ok-envelope",
                            args.ttl_hours)
        print("[pre-cognitive] could not parse anticipate JSON — wrote empty cache",
              file=sys.stderr)
        print(f"[pre-cognitive] raw stdout head:\n{(proc.stdout or '')[:400]}",
              file=sys.stderr)
        return 0

    mems = payload.get("memories", []) or []
    slim = [
        {
            "id": m.get("id"),
            "tier": m.get("tier", 3),
            "summary": (m.get("summary") or "")[:200],
            "category": m.get("category", ""),
        }
        for m in mems[:CACHE_KEEP]
        if isinstance(m, dict) and m.get("id") is not None
    ]

    obj = {
        "generated_at": _now_iso(),
        "ttl_hours": args.ttl_hours,
        "source": "wizard-library-pre-cognitive cron (anticipate --log)",
        "ok": True,
        "log_id": payload.get("log_id"),
        "ambiguous": bool(payload.get("ambiguous")),
        "anticipate_time_ms": payload.get("time_ms"),
        "memories_loaded": payload.get("memories_loaded", len(slim)),
        "memories": slim,
    }
    _atomic_write_json(cache, obj)
    print(f"[pre-cognitive] cache refreshed: {len(slim)} anticipated, "
          f"log_id={payload.get('log_id')}, -> {cache}", file=sys.stderr)
    return 0


def _extract_json(text: str):
    """Return the last top-level JSON object found in text, or None."""
    if not text:
        return None
    # Fast path: whole thing is JSON.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Otherwise scan for the last balanced {...}.
    depth = 0
    start = None
    last = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    last = text[start:i + 1]
    if last:
        try:
            return json.loads(last)
        except Exception:
            return None
    return None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
