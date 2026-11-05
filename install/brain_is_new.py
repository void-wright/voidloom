#!/usr/bin/env python3
"""Print "yes" for a brain this run may embed whole, else "no". Both installers ask here, before they write to it.

A brain is new when there is no file yet, or it holds only the seed pack and no vector: an install that stopped at keyword
recall. Anything an owner has written makes it an existing brain, and an existing brain is never embedded unasked.
Read-only, standard library only: this runs before any environment is built.
"""
import os
import sqlite3
import sys
import urllib.request


def is_new(db: str) -> bool:
    if not (os.path.isfile(db) and os.path.getsize(db)):
        return True
    c = sqlite3.connect("file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(db)), uri=True)
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


if __name__ == "__main__":
    try:
        print("yes" if is_new(sys.argv[1]) else "no")
    except (sqlite3.Error, OSError, IndexError):
        print("no")                             # what cannot be read is treated as holding something
