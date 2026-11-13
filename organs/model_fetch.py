#!/usr/bin/env python3
"""model_fetch — THE INSTALL'S FETCH: every model the memory system needs, at its
catalogue pin, bounded to the files the pin names.

    python3 organs/model_fetch.py --db <brain>/claude_memory.db [--json] [--plan] [--no-fetch]

Drew's ruling (2026-09-20, Amendment 8): *"everything that matters to get the full
memory system officially working is considered part of the install."* Until this
verb existed the install stopped at `init`: it wrote a lock naming
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
     "present" means "the bytes the catalogue promised". A model whose bytes fail
     its pin (a damaged file) is fetched again at that pin, over what is on disk,
     and hashed again; a second failure refuses, WEIGHTS_MISMATCH. Without
     fetching (`--no-fetch`, HF-offline) it refuses at once.
  4. Prints a census per repository: files on disk, files named, unnamed files
     and their bytes (the install's promise: unnamed_bytes_total == 0).

WHAT IT REFUSES, TYPED: `MODELS_ABSENT` (rc 3) when any needed model is absent and
cannot be fetched -- the process is HF-offline, `--no-fetch` was given, or the hub
could not be reached. The refusal names every absent repository, the files each pin
names, the bytes on disk, and the retry verb. A caller that gets this refusal reports
no working memory: a lock naming absent weights is not an install. When the only
models absent are ones this machine's reranker gate does not load, the refusal says so:
recall already works there.

A brain with no lock is served by the shipped catalogue entry for the tier it embeds
under. A brain on a tier the catalogue does not ship (jina) is fetched at the revision
the engine pins in code, and no checksum vouches for those bytes: the output says so.

WHAT IT NEVER DOES: instantiate a model (the fetch is snapshot_download + sha256;
`model_loads: 0` by construction), guess a tier, or fetch anything nothing pins.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


class _TierUnread(Exception):
    """`tier_set`'s own typed refusal, passed through: it is not a lock refusal, so it is not one of the lock's codes."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _lock_for(el, db: str) -> dict:
    """The brain's lock; for a brain that predates it, the shipped entry for the tier it embeds under."""
    try:
        return el.resolve(db)
    except el.EmbedderLockError as e:
        if e.code != "LOCK_MISSING" or e.detail.get("was_locked"):
            raise
    import embedding_upgrade
    import tier_set
    try:
        tier = tier_set._prelock_engine_tier(Path(db))
    except tier_set.TierRefused as e:
        raise _TierUnread(e.code, e.message)
    if tier is None:
        raise el.EmbedderLockError("LOCK_NOT_SHIPPED", "this brain embeds under a tier the package catalogue does not "
                                   "ship, so there is no catalogue pin to fetch at; the engine pins it in code",
                                   engine_pin=True)
    return embedding_upgrade.brain_lock(db, tier=tier)


def _fetch_engine_pin(el, a, emit) -> int:
    """A tier the catalogue does not ship (jina) has no catalogue pin, and the engine loads it at the revision it pins in
    code. The bytes nothing hashes here are fetched at that same revision, so the fix a recall prints works on it too."""
    import embedding_upgrade as eu
    model, revision = eu.JINA_MODEL_NAME, eu.JINA_REVISION
    doc = {"status": "ok", "action": "model-fetch", "model": model, "revision": revision,
           "basis": "the engine's pin: this tier is not in the catalogue, so its bytes are not checksummed",
           "hf_hub_cache": str(el.hf_hub_cache())}
    present = eu.jina_snapshot_whole()
    if a.plan:
        return emit({**doc, "mode": "plan", "present": present}, 0)
    if not present and (a.no_fetch or el.hf_offline()):
        return emit({"status": "error", "error": "MODELS_ABSENT", "action": "model-fetch", "model": model,
                     "message": f"{model}@{revision[:12]} is not on this disk, and this run may not fetch it"}, 3)
    eu.fetch_jina_at_pin()
    return emit({**doc, "mode": "present" if present else "fetched", "present": True}, 0)


def _reranker_gate_on() -> bool:
    try:
        import hybrid_retrieval
        return bool(hybrid_retrieval.reranker_gate()["enabled"])
    except Exception:                       # noqa: BLE001 -- a gate that cannot be read makes no claim
        return True


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
        lock = _lock_for(el, a.db)
    except el.EmbedderLockError as e:
        if e.detail.get("engine_pin"):
            return _fetch_engine_pin(el, a, emit)
        return emit({**e.as_dict(), "action": "model-fetch"}, 2)
    except _TierUnread as e:
        return emit({"status": "error", "error": e.code, "action": "model-fetch", "message": e.message}, 2)
    organs_dir = Path(__file__).resolve().parent
    gate_off = not _reranker_gate_on()
    if a.plan:
        plan = el.models_needed(lock, organs_dir=organs_dir)
        for m in plan["needed"]:
            if m["what"] == "reranker" and gate_off:
                m["gate"] = "off on this machine: fetched for the install, not loaded by recall here"
        return emit({"status": "ok", "action": "model-fetch", "mode": "plan", "hf_hub_cache": str(el.hf_hub_cache()),
                     **plan}, 0)
    try:
        res = el.fetch_models_needed(lock, organs_dir=organs_dir, allow_fetch=not a.no_fetch)
    except el.EmbedderLockError as e:
        doc = {**e.as_dict(), "action": "model-fetch"}
        missing = [m.get("what") for m in (e.detail.get("models") or []) if m.get("status") == "absent"]
        if gate_off and missing and set(missing) <= {"reranker"}:
            doc["recall_unaffected"] = ("only the reranker is absent, and this machine's gate does not load it: "
                                        "recall works here without it")
        if e.code == "WEIGHTS_MISMATCH" and (a.no_fetch or el.hf_offline()) and not doc.get("retry"):
            doc["retry"] = [el.fetch_command(a.db)]
            doc["fix"] = ("this run may not fetch; with the network and without --no-fetch, the same command fetches "
                          "the damaged files again at their pins: " + doc["retry"][0])
        return emit(doc, 3 if e.code == "MODELS_ABSENT" else 1)
    res = {"action": "model-fetch", **res}
    if not a.json:
        print(f"  needed {res['needed']}: fetched {res['fetched']}, present before {res['present_before']}, "
              f"verified {res['verified']} of {res['verified_of']}, replaced {res['replaced']}; "
              f"named bytes {res['bytes_named_total']:,}; "
              f"unnamed files {res['unnamed_files_total']} ({res['unnamed_bytes_total']:,} B) under {res['hf_hub_cache']}",
              file=sys.stderr)
    return emit(res, 0)


if __name__ == "__main__":
    # No device pin: this process downloads and hashes files and never imports a
    # model runtime. Device choice belongs to voidloom_device in the processes that load.
    sys.exit(main())
