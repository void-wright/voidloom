#!/usr/bin/env python3
"""Regenerate organs/embedders.lock.json — the package's embedder catalogue.

EVERY `weights_hash` IN THE CATALOGUE IS COMPUTED HERE FROM THE SNAPSHOT'S BYTES --
AND FROM THE BYTES OF EVERY REPOSITORY THE MODEL'S config.json `auto_map` NAMES.
None is typed. The algorithm is `embedder_lock.compute_weights_hash`, and the
shell reproduces it without Python (M1-C1's executable check, Amendment 5 §2):

    ( cd <model snapshot> && sha256sum $(jq -r '.tiers.cpu.files[]' organs/embedders.lock.json);
      cd <code snapshot>  && sha256sum $(jq -r '.tiers.cpu.code.files[]' organs/embedders.lock.json) ) | sha256sum

nomic-embed-text-v1.5's forward pass is `nomic-ai/nomic-bert-2048` (two `.py`
files in a SECOND hub snapshot, reached through `auto_map`, which `revision=`
does not pin). The builder reads `auto_map` from the model's own config.json,
pins that repository as `code: {model_id, revision, files[]}` (its revision is
the snapshot the cache's refs/main resolves to at build time, recorded here so a
rebuild elsewhere hashes the same bytes), and folds its `sha256sum` lines into
the SAME hash. Qwen has no auto_map and no `code` member. The loader passes
`code_revision` so the code that runs is the code that was hashed.

WHAT IS SHIPPED, AND WHAT IS NOT
    cpu  nomic-ai/nomic-embed-text-v1.5   apache-2.0   768   memory_embeddings
    gpu  Qwen/Qwen3-Embedding-0.6B        apache-2.0  1024   memory_embeddings_v3

    The cc-by-nc-4.0 embedder this package once used has NO entry, by
    construction: a hash that is not in this file is refused at the loader and
    at the reader (embedder_lock.REFUSALS). Adding a tier is a catalogue rebuild
    on a host that holds the snapshot; the hash cannot be pasted in because the
    loader recomputes it from the files it loads and refuses a disagreement.

The revision for each tier is the commit the snapshot directory is named for.
It is the FETCH instruction, never the identity (Amendment 4).

Run from OUTSIDE the tree so no __pycache__ lands in what is measured:
    python3 /path/to/voidloom/tools/build_embedder_catalogue.py [--check]
--check recomputes and compares against the committed catalogue, writing nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "organs"))
import embedder_lock as el  # noqa: E402

# tier -> (model_id, revision, dim, vec_table, license)
# The revision is pinned here so a rebuild on another host hashes the SAME
# snapshot; a differing hash then means differing bytes, which is the point.
SHIPPED = {
    "cpu": ("nomic-ai/nomic-embed-text-v1.5", "e9b6763023c676ca8431644204f50c2b100d9aab",
            768, "memory_embeddings", "apache-2.0"),
    "gpu": ("Qwen/Qwen3-Embedding-0.6B", "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
            1024, "memory_embeddings_v3", "apache-2.0"),
}
# The remote-code repository each tier's auto_map names, pinned by commit. Derived
# from config.json at build time and CHECKED against this table: a tier whose
# auto_map names a repository absent here (or the reverse) fails the build, so a
# new remote-code dependency cannot enter the catalogue unpinned.
CODE_PINS = {
    "cpu": ("nomic-ai/nomic-bert-2048", "7710840340a098cfb869c4f65e87cf2b1b70caca"),
}
# name -> (model_id, revision, license, loader). M1 round 5 #3 (round-4 F4): the
# ring covers EVERY model the install fetches, not only embedders (Amendment 7).
# The default hybrid-recall reranker was loaded BY NAME at organs/qwen_reranker.py
# and landed 1.2 GB in a clean install's cache unpinned; it is now pinned here by
# weights_hash + hub revision and loaded from the VERIFIED snapshot only. The
# licence is named from the hub's metadata (`license:apache-2.0`, read
# 2026-09-20). A reranker writes no vector row, so it has no dim/vec_table.
RERANKERS = {
    "qwen3-reranker-0.6b": ("Qwen/Qwen3-Reranker-0.6B", "e61197ed45024b0ed8a2d74b80b4d909f1255473",
                            "apache-2.0", "organs/qwen_reranker.py"),
}


def build() -> dict:
    tiers = {}
    for tier, (model_id, revision, dim, vec_table, licence) in SHIPPED.items():
        snap = el.snapshot_dir_for(model_id, revision)
        if snap is None:
            raise SystemExit(f"{tier}: no snapshot for {model_id}@{revision} under {el.hf_hub_cache()}; "
                             "fetch it first (huggingface_hub.snapshot_download) -- a hash is never typed")
        files = el.output_determining_files(snap)
        if not files:
            raise SystemExit(f"{tier}: snapshot {snap} yields no output-determining files")
        repos = el.auto_map_repositories(snap)
        pin = CODE_PINS.get(tier)
        if repos and (pin is None or pin[0] not in repos or len(repos) != 1):
            raise SystemExit(f"{tier}: config.json auto_map names {repos} but CODE_PINS pins "
                             f"{pin}; a remote-code repository enters the catalogue pinned or not at all")
        if pin and not repos:
            raise SystemExit(f"{tier}: CODE_PINS pins {pin[0]} but config.json names no auto_map repository")
        code = None
        code_snap = None
        if pin:
            code_snap = el.snapshot_dir_for(pin[0], pin[1])
            if code_snap is None:
                raise SystemExit(f"{tier}: no snapshot for the remote-code repository {pin[0]}@{pin[1]} "
                                 f"under {el.hf_hub_cache()}; fetch it first -- a hash is never typed")
            code_files = [f for f in el.output_determining_files(code_snap) if f.endswith(".py")]
            if not code_files:
                raise SystemExit(f"{tier}: code snapshot {code_snap} holds no *.py to hash")
            code = {"model_id": pin[0], "revision": pin[1], "files": code_files}
        entry = {
            "model_id": model_id,
            "revision": revision,
            "dim": dim,
            "vec_table": vec_table,
            "license": licence,
            "status": "active",
            "files": files,
        }
        if code:
            entry["code"] = code
        entry["weights_hash"] = el.compute_entry_hash(entry, snapshot=snap, code_snapshot=code_snap)
        tiers[tier] = entry
    rerankers = {}
    for name, (model_id, revision, licence, loader) in RERANKERS.items():
        snap = el.snapshot_dir_for(model_id, revision)
        if snap is None:
            raise SystemExit(f"{name}: no snapshot for {model_id}@{revision} under {el.hf_hub_cache()}; "
                             "fetch it first (huggingface_hub.snapshot_download) -- a hash is never typed")
        files = el.output_determining_files(snap)
        if not files:
            raise SystemExit(f"{name}: snapshot {snap} yields no output-determining files")
        if el.auto_map_repositories(snap):
            raise SystemExit(f"{name}: config.json names a remote-code repository; a reranker with "
                             "remote code needs a `code` pin, which this table does not carry")
        entry = {
            "model_id": model_id,
            "revision": revision,
            "license": licence,
            "loader": loader,
            "status": "active",
            "files": files,
        }
        entry["weights_hash"] = el.compute_entry_hash(entry, snapshot=snap, code_snapshot=None)
        rerankers[name] = entry
    return {
        "schema": el.CATALOGUE_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generated_by": "tools/build_embedder_catalogue.py",
        "hash_algorithm": ("sha256 over the sha256sum lines ('<hex>  <path>\\n') of `files` in the "
                           "listed order, followed by the sha256sum lines of `code.files` in the "
                           "remote-code repository the model's config.json auto_map names (when it "
                           "names one); reproduce with: ( cd <model snapshot> && sha256sum "
                           "$(jq -r '.files[]' <entry>); cd <code snapshot> && sha256sum "
                           "$(jq -r '.code.files[]' <entry>) ) | sha256sum"),
        "default_tier": el.DEFAULT_TIER,
        "tiers": tiers,
        "rerankers": rerankers,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="recompute and compare; write nothing")
    ap.add_argument("--out", default=str(el.CATALOGUE_PATH))
    a = ap.parse_args()
    cat = build()
    out = Path(a.out)
    if a.check:
        if not out.exists():
            print(f"{out} absent", file=sys.stderr)
            return 2
        have = json.loads(out.read_text(encoding="utf-8"))
        bad = []
        for tier, entry in cat["tiers"].items():
            h = (have.get("tiers") or {}).get(tier) or {}
            if (h.get("weights_hash") != entry["weights_hash"] or h.get("files") != entry["files"]
                    or h.get("code") != entry.get("code")):
                bad.append(tier)
            print(f"{tier}: computed {entry['weights_hash']}  committed {h.get('weights_hash')}  "
                  f"{'MATCH' if tier not in bad else 'DIFFER'}")
        return 1 if bad else 0
    out.write_text(json.dumps(cat, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for tier, entry in cat["tiers"].items():
        snap = el.snapshot_dir_for(entry['model_id'], entry['revision'])
        code = entry.get("code")
        print(f"{tier}: {entry['model_id']}@{entry['revision'][:12]} -> {entry['weights_hash']} "
              f"({len(entry['files'])} files, hashed from {snap}"
              + (f"; + {code['model_id']}@{code['revision'][:12]} {code['files']}" if code else "") + ")")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
