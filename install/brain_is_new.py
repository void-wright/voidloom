#!/usr/bin/env python3
"""Print "yes" for a brain this run may embed whole, else "no". Both installers ask here, before they write to it.

A brain is new when there is no file yet, or it holds only the seed pack and no vector: an install that stopped at keyword
recall. Anything an owner has written makes it an existing brain, and an existing brain is never embedded unasked.
Read-only, standard library only: this runs before any environment is built. `--tier` prints the tier the brain declares.
"""
import json
import os
import sqlite3
import sys
import urllib.request


def is_new(db: str) -> bool:
    if not (os.path.isfile(db) and os.path.getsize(db)):
        return True
    # BRAIN_READ_Q: a snapshot is opened immutable, so reading it leaves no sidecar beside a file that is not ours
    c = sqlite3.connect("file:%s?mode=ro%s" % (urllib.request.pathname2url(os.path.abspath(db)),
                                               os.environ.get("BRAIN_READ_Q", "")), uri=True)
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(memories)")}
        if not cols:
            return True
        # a brain older than the `seed` column marks the pack by its tag
        own = c.execute("SELECT COUNT(*) FROM memories" + (" WHERE COALESCE(seed, 0) = 0" if "seed" in cols else
                        " WHERE id NOT IN (SELECT memory_id FROM memory_tags WHERE tag = 'seed-pack-agnostic')")).fetchone()[0]
        held = 0
        for t in ("memory_embeddings", "memory_embeddings_v2", "memory_embeddings_v3"):
            try:
                held += c.execute("SELECT COUNT(*) FROM %s_rowids" % t).fetchone()[0]
            except sqlite3.Error:
                pass
        return own == 0 and held == 0
    finally:
        c.close()


def declared_tier(db: str) -> str:
    """The tier this brain embeds under, as both installers read it: the lock beside it first, then its own metadata row.
    Empty when it declares none. Windows asks here with `--tier`; a brain that has a tier keeps it."""
    tiers = {"memory_embeddings_v3": "qwen", "memory_embeddings_v2": "jina", "memory_embeddings": "nomic"}
    lock = os.path.join(os.path.dirname(os.path.realpath(os.path.abspath(db))), "embedder.lock.json")
    try:
        with open(lock, encoding="utf-8") as f:
            return tiers.get(json.load(f).get("vec_table"), "")
    except (OSError, ValueError, AttributeError):
        pass
    c = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(db)), uri=True)
    try:
        row = c.execute("SELECT value FROM metadata WHERE key = 'active_embedding_table'").fetchone()
    finally:
        c.close()
    return tiers.get(row[0] if row else "", "")


if __name__ == "__main__":
    if sys.argv[2:] == ["--tier"]:
        try:
            print(declared_tier(sys.argv[1]))
        except (sqlite3.Error, OSError):
            print("")
        sys.exit(0)
    try:
        print("yes" if is_new(sys.argv[1]) else "no")
    except (sqlite3.Error, OSError, IndexError):
        print("no")                             # what cannot be read is treated as holding something
