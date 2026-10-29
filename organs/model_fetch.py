#!/usr/bin/env python3
"""model_fetch — THE INSTALL'S FETCH: every model the memory system needs, at its
catalogue pin, bounded to the files the pin names (Voidstead M1 round 6, item #1).

    python3 organs/model_fetch.py --db <brain>/claude_memory.db [--json] [--plan] [--no-fetch]

Drew's ruling (2026-09-20, Amendment 8): *"everything that matters to get the full
memory system officially working is considered part of the install."* Until this
verb existed `voidstead memory install` stopped at `init`: it wrote a lock naming
exact bytes, reported rc 0, and the customer's FIRST `store` then downloaded 4.4 GB
the install never mentioned -- or, offline, failed with rc 1 (`SNAPSHOT_ABSENT`,
executed by the round-5 curator on the referent). The check column and the words
of M1-C0 disagreed on an offline host; this module is the product that makes them
agree.

WHAT IT DOES (embedder_lock.fetch_models_needed, the one lane):
  1. DERIVES the set, and prints the derivation: the lock's embedder
     (tiers.<tier>), the remote-code repository its `code` pin names, and every
     catalogue `rerankers` entry whose `loader` organ is laid beside this file
     (the shipped reranker today). Nothing is fetched by bare name.
  2. FETCHES each absent snapshot AT ITS PIN with `allow_patterns` = the pin's
     `files[]` (CUR-M1R5-F3: 61.2 % of the bytes a clean install fetched were
     files no pin named -- the number this bounds to 0), through the same
     `_fetch_pinned` the loader uses.
  3. HASHES every present model against its pin (the loader's own check), so
     "present" means "the bytes the catalogue promised".
  4. Prints a census per repository: files on disk, files named, unnamed files
     and their bytes (the install's promise: unnamed_bytes_total == 0).

WHAT IT REFUSES, TYPED: `MODELS_ABSENT` (rc 3) when any needed model is absent and
cannot be fetched -- the process is HF-offline, `--no-fetch` was given, or the hub
could not be reached. The refusal names every absent repository, the files each pin
names, the bytes on disk, and the retry verb. The install that calls this then
removes the lock it wrote and reports NO working memory (DESSA, Council 5): a lock
naming absent weights is not an install.

WHAT IT NEVER DOES: instantiate a model (the fetch is snapshot_download + sha256;
`model_loads: 0` by construction), guess a tier, or fetch anything the catalogue
does not pin.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="the brain database; the lock is read beside it")
    ap.add_argument("--json", action="store_true", help="envelope only")
    ap.add_argument("--plan", action="store_true", help="derive and print the set; fetch nothing")
    ap.add_argument("--no-fetch", action="store_true",
                    help="verify what is present; an absent model is MODELS_ABSENT (a self-test, never a download)")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import embedder_lock as el

    def emit(doc, rc):
        print(json.dumps(doc, indent=(None if a.json else 2), sort_keys=False, default=str))
        return rc

    try:
        lock = el.resolve(a.db)
    except el.EmbedderLockError as e:
        return emit({**e.as_dict(), "action": "model-fetch"}, 2)
    organs_dir = Path(__file__).resolve().parent
    if a.plan:
        plan = el.models_needed(lock, organs_dir=organs_dir)
        return emit({"status": "ok", "action": "model-fetch", "mode": "plan", "hf_hub_cache": str(el.hf_hub_cache()),
                     **plan}, 0)
    try:
        res = el.fetch_models_needed(lock, organs_dir=organs_dir, allow_fetch=not a.no_fetch)
    except el.EmbedderLockError as e:
        doc = {**e.as_dict(), "action": "model-fetch"}
        return emit(doc, 3 if e.code == "MODELS_ABSENT" else 1)
    res = {"action": "model-fetch", **res}
    if not a.json:
        print(f"  needed {res['needed']}: fetched {res['fetched']}, present before {res['present_before']}, "
              f"verified {res['verified']} of {res['verified_of']}; named bytes {res['bytes_named_total']:,}; "
              f"unnamed files {res['unnamed_files_total']} ({res['unnamed_bytes_total']:,} B) under {res['hf_hub_cache']}",
              file=sys.stderr)
    return emit(res, 0)


if __name__ == "__main__":
    # No device pin: this process downloads and hashes files and never imports a
    # model runtime. Device choice belongs to voidloom_device in the processes that load.
    sys.exit(main())
