#!/usr/bin/env python3
"""embedder_lock.py — the catalogue, the install lock, and the checksum that binds them.

The design rule it implements (2026-09-17):

    THE DERIVATION SOURCE MUST NAME THE EMBEDDER BY THE SAME CONTENT IDENTITY THAT
    EVERY VECTOR ROW CARRIES AS A WRITTEN FACT, SO THAT THE ONLY ACT CAPABLE OF
    MAKING THE SOURCE AND THE ROWS AGREE IS EMBEDDING ITSELF.

TWO ARTIFACTS, ONE HONEST FACT EACH
    1. THE PACKAGE CATALOGUE  `organs/embedders.lock.json` (CATALOGUE_PATH).
       One entry per shipped tier: {model_id, revision, weights_hash, dim,
       vec_table, files[]}. Answers "what may this package embed with". Generated
       by `tools/build_embedder_catalogue.py` from the model snapshots -- every
       hash in it is COMPUTED, never typed. It lives in organs/ and not seed/
       because docs/voidstead_tiers.json puts seed/ in Tier 3 ("a birth-time act")
       and this file is read EVERY TIME THE EMBEDDER LOADS; the tiers file's own
       discriminator puts it in the organism.
    2. THE INSTALL LOCK  `<data-dir>/embedder.lock.json` (LOCK_NAME), a sibling of
       the SQLite file. Exactly one catalogue entry copied out plus `tier`,
       `locked_at`, `catalogue_hash`. Answers "what THIS install promised".
       Written by `init` (temp -> fsync -> rename; a receipt line is appended to
       `embedder.lock.history.jsonl` on every write). THIS is the file the
       criteria mean by "the lockfile".

`weights_hash`
    sha256 over the `sha256sum`-format lines ("<hex>  <relative path>\\n") of the
    snapshot's output-determining files, in the order `files[]` lists them
    (sorted), FOLLOWED BY the same lines for every repository the model's
    `config.json` `auto_map` names (Amendment 5 §2: nomic-embed-text-v1.5's
    forward-pass code lives in a SECOND hub repository, nomic-ai/nomic-bert-2048,
    reached only through auto_map; `revision=` does not pin it and a hash over
    the model snapshot alone was green on a red property). That repository is
    pinned in the entry as `code: {model_id, revision, files[]}` and loaded with
    `code_revision`. Reproducible from the shell with no Python at all:

        ( cd <model snapshot> && sha256sum $(jq -r '.files[]' <lock>);
          cd <code snapshot>  && sha256sum $(jq -r '.code.files[]' <lock>) ) | sha256sum

    A tier with no `auto_map` (Qwen) has no `code` member and the first half is
    the whole hash. Two repositories, one identity: a change to EITHER changes
    the hash, so a code change makes vectors disagree under a changed hash.

    `revision` (the hub commit) is KEPT as the fetch instruction and is never the
    identity: it is not recomputable from local bytes and was already degrading to
    'unresolved' for the tier with no code pin. The sidecar column that carries the
    revision keeps its round-1 name, `model_ref` (ROUND_2_SCOPE.md IN-1).

WHO CHECKS WHAT, AND IN WHICH ORDER  (facts before policy)
    The LOADER (embedding_upgrade.get_locked_embedder): LOCK_MISSING ->
    LOCK_MALFORMED -> [no snapshot on disk, model OR code: LOCK_NOT_SHIPPED before
    any fetch] -> WEIGHTS_MISMATCH (computed checksum over BOTH repositories !=
    lock) -> LOCK_NOT_SHIPPED. A lock
    re-pointed to forty f's therefore refuses as WEIGHTS_MISMATCH on a host that
    holds the weights (M1-C2's executable check), and a real-but-unshipped hash
    refuses as LOCK_NOT_SHIPPED without a byte leaving the machine.

    The READER (memory_write.provenance_coverage): LOCK_MISSING -> LOCK_MALFORMED
    -> [empty brain: LOCK_NOT_SHIPPED, else 0/0 coverage] -> NO_ROWS_FOR_LOCK
    (with the histogram of hashes actually present) -> LOCK_NOT_SHIPPED ->
    STAMP_INCONSISTENT. Forty f's on a stamped brain is NO_ROWS_FOR_LOCK, as the
    criterion's check states; rows that DO carry an unshipped hash are refused as
    LOCK_NOT_SHIPPED, which is the encumbered model's absence made structural.

    The RESOLVER (embed_resolver.resolve -> `resolve()` here) answers "(table,
    dim) for this connection" from the lock ALONE. It does not consult the
    catalogue, so that the reader, not the resolver, decides between the two
    refusals above; and it never reads `metadata`, which is the sibling trap by
    name.

THE PROCESS REGISTER
    `register_loaded` records the ONE embedder resident in this process with the
    checksum the loader COMPUTED from the bytes it loaded. `require_loaded_hash`
    is what the stamp reads: the stamp is a fact about what ran, never a copy of
    the lock's string. Nothing loaded -> no stamp -> no vector (memory_write
    refuses). A second, different embedder in the same process is refused; one
    model resident at a time is a rule of this host as much as of this module.

    Tests that must not load a model call `register_loaded` themselves with the
    catalogue's hash (the positive control) or a foreign one (the saboteur). That
    seam is a function call, not a flag: there is no environment variable that
    turns a refusal off.

STDLIB ONLY at module scope. huggingface_hub is imported inside the one function
that may fetch, and only when the snapshot is absent.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

CATALOGUE_PATH = Path(__file__).resolve().parent / "embedders.lock.json"
LOCK_NAME = "embedder.lock.json"
LOCK_HISTORY_NAME = "embedder.lock.history.jsonl"
CATALOGUE_SCHEMA = "voidloom.embedder-catalogue/1"
LOCK_SCHEMA = "voidloom.embedder-lock/1"
DEFAULT_TIER = "cpu"

REFUSALS = ("LOCK_MISSING", "LOCK_MALFORMED", "LOCK_NOT_SHIPPED",
            "NO_ROWS_FOR_LOCK", "WEIGHTS_MISMATCH", "STAMP_INCONSISTENT",
            "SNAPSHOT_ABSENT",     # M1 round 5 #6: the pin is not on disk and fetching is not allowed
            "MODELS_ABSENT")       # M1 round 6 #1: the INSTALL could not fetch what the memory needs
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_LOCK_REQUIRED = ("tier", "model_id", "revision", "weights_hash", "dim", "vec_table", "files")
_ENTRY_REQUIRED = ("model_id", "revision", "weights_hash", "dim", "vec_table", "files")
_CODE_REQUIRED = ("model_id", "revision", "files")     # the auto_map repository pin (optional member)
# A catalogue `rerankers` entry (M1 round 5 #3 / round-4 F4): the same pin shape
# as a tier -- model_id, hub revision, files[] hashed in order, weights_hash --
# minus dim/vec_table (a reranker writes no vector row), plus the licence NAMED.
_RERANKER_REQUIRED = ("model_id", "revision", "weights_hash", "files", "license", "loader")

# The snapshot files whose bytes determine the vectors. Weight shards, the model
# config, every tokenizer artefact, the sentence-transformers module chain
# (pooling and prompts change the output), and any remote code in the snapshot.
# README, licences, .gitattributes and generation_config.json are excluded: they
# do not touch an embedding. The set is applied by the catalogue BUILDER; the
# result is written out explicitly as `files[]`, and the loader hashes exactly
# that list, so the rule cannot drift between build and load.
_OUTPUT_DETERMINING_NAMES = {
    "config.json", "config_sentence_transformers.json", "sentence_bert_config.json",
    "modules.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
    "vocab.txt", "vocab.json", "merges.txt", "spiece.model", "sentencepiece.bpe.model",
    "tokenizer.model", "added_tokens.json", "model.safetensors.index.json",
    "pytorch_model.bin.index.json",
}
_OUTPUT_DETERMINING_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".gguf", ".py")


class EmbedderLockError(RuntimeError):
    """Every refusal in this module. `.code` is one of REFUSALS; `.detail` is the
    dict a JSON envelope carries. Loud beats a silent wrong tier."""

    def __init__(self, code: str, message: str, **detail: Any):
        if code not in REFUSALS:
            raise ValueError(f"unknown refusal code {code!r}")
        super().__init__(f"{code}: {message}")
        self.code = code
        self.detail = dict(detail)

    def as_dict(self) -> Dict[str, Any]:
        return {"status": "error", "error": self.code, "message": str(self), **self.detail}


# --------------------------------------------------------------------------
# The checksum.
# --------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256sum_lines(snapshot_dir: Union[str, Path], files: Sequence[str]) -> str:
    """The text `sha256sum f1 f2 ...` prints for `files` under `snapshot_dir`
    ("<hex>  <name>\\n" per file, in the given order). Symlinks (the HF cache
    stores snapshots as links into blobs/) are followed, as sha256sum follows them."""
    snap = Path(snapshot_dir)
    if not files:
        raise ValueError("sha256sum_lines: an empty file list hashes nothing")
    lines = []
    for rel in files:
        p = snap / rel
        if not p.is_file():
            raise FileNotFoundError(f"{rel} is absent from {snap}")
        lines.append(f"{sha256_file(p)}  {rel}\n")
    return "".join(lines)


def compute_weights_hash(snapshot_dir: Union[str, Path], files: Sequence[str], *,
                         code_snapshot_dir: Union[None, str, Path] = None,
                         code_files: Sequence[str] = ()) -> str:
    """sha256 of the `sha256sum` output for `files` under the model snapshot,
    followed by the `sha256sum` output for `code_files` under the remote-code
    snapshot when the tier has one (Amendment 5 §2).

    Byte-for-byte the text the amended shell check pipes into `sha256sum`:
        ( cd <model snapshot> && sha256sum $(files); cd <code snapshot> && sha256sum $(code.files) ) | sha256sum
    so the shell reproduces it. With no code repository the first half is the
    whole hash (the round-2 form, unchanged for Qwen).
    """
    text = sha256sum_lines(snapshot_dir, files)
    if code_snapshot_dir is not None or code_files:
        if code_snapshot_dir is None or not code_files:
            raise ValueError("compute_weights_hash: a code repository needs both its snapshot and its files")
        text += sha256sum_lines(code_snapshot_dir, code_files)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_entry_hash(entry: Dict[str, Any], *, snapshot: Union[str, Path],
                       code_snapshot: Union[None, str, Path] = None) -> str:
    """The checksum for a catalogue entry / lock: the model snapshot's `files[]`
    and, when the entry carries a `code` pin, that repository's `code.files[]`."""
    code = entry.get("code")
    if code:
        if code_snapshot is None:
            raise FileNotFoundError(f"the code repository {code['model_id']}@{code['revision'][:12]} "
                                    "the entry pins is not on this machine")
        return compute_weights_hash(snapshot, entry["files"],
                                    code_snapshot_dir=code_snapshot, code_files=code["files"])
    return compute_weights_hash(snapshot, entry["files"])


def auto_map_repositories(snapshot_dir: Union[str, Path]) -> List[str]:
    """The hub repositories the model's config.json `auto_map` names, in first-
    seen order -- the SECOND repository a `trust_remote_code` load executes.
    An entry of the form `owner/repo--module.Class` names `owner/repo`; a bare
    `module.Class` means the code lives in the model's own snapshot (and is then
    among its output-determining *.py files already)."""
    cfg = Path(snapshot_dir) / "config.json"
    try:
        auto_map = json.loads(cfg.read_text(encoding="utf-8")).get("auto_map") or {}
    except (OSError, ValueError):
        return []
    out: List[str] = []
    for ref in auto_map.values():
        if isinstance(ref, str) and "--" in ref:
            repo = ref.split("--", 1)[0]
            if repo and repo not in out:
                out.append(repo)
    return out


def output_determining_files(snapshot_dir: Union[str, Path]) -> List[str]:
    """The builder's rule, applied: relative paths, sorted, of every file in the
    snapshot whose bytes determine the embedding. Used by the catalogue builder
    and by nothing on the load path (the load path hashes `files[]` as listed)."""
    snap = Path(snapshot_dir)
    out: List[str] = []
    for p in sorted(snap.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(snap).as_posix()
        if p.name in _OUTPUT_DETERMINING_NAMES or p.name.endswith(_OUTPUT_DETERMINING_SUFFIXES):
            out.append(rel)
    return out


def hf_hub_cache() -> Path:
    """The cache huggingface_hub itself reads, in ITS precedence: HF_HUB_CACHE,
    then HUGGINGFACE_HUB_CACHE, then HF_HOME/hub, then ~/.cache/huggingface/hub.
    (F8: the round-1 reader read HF_HOME only, so on a host with HF_HUB_CACHE set
    the loader and the stamp read different caches.)"""
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        v = (os.environ.get(var) or "").strip()
        if v:
            return Path(v).expanduser()
    home = (os.environ.get("HF_HOME") or "").strip()
    root = Path(home).expanduser() if home else Path.home() / ".cache" / "huggingface"
    return root / "hub"


def snapshot_dir_for(model_id: str, revision: str, *, hub: Optional[Path] = None) -> Optional[Path]:
    """The local snapshot directory for (model_id, revision), or None if absent.
    A revision that is a full commit sha names its snapshot directory directly;
    a symbolic revision (e.g. 'main') is resolved through refs/<name>."""
    hub = hub or hf_hub_cache()
    repo = hub / ("models--" + model_id.replace("/", "--"))
    rev = (revision or "").strip()
    if not rev:
        return None
    cand = repo / "snapshots" / rev
    if cand.is_dir():
        return cand
    try:
        resolved = (repo / "refs" / rev).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    cand = repo / "snapshots" / resolved
    return cand if cand.is_dir() else None


# --------------------------------------------------------------------------
# The catalogue.
# --------------------------------------------------------------------------

_catalogue_cache: Dict[str, Tuple[float, Dict[str, Any], str]] = {}


def catalogue_path(explicit: Optional[Union[str, Path]] = None) -> Path:
    return Path(explicit) if explicit else CATALOGUE_PATH


def load_catalogue(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """The shipped catalogue, parsed and shape-checked. Cached per process, keyed
    on path + mtime, so a rebuilt catalogue is re-read and an unchanged one is
    read once (Amendment 4: catalogue-loaded-once-per-process)."""
    p = catalogue_path(path)
    try:
        st = p.stat()
    except OSError as e:
        raise EmbedderLockError(
            "LOCK_NOT_SHIPPED",
            f"the package catalogue is absent at {p} ({e}); nothing can be shipped "
            "without it", catalogue=str(p))
    key = str(p)
    hit = _catalogue_cache.get(key)
    if hit and hit[0] == st.st_mtime_ns:
        return hit[1]
    raw = p.read_bytes()
    try:
        cat = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise EmbedderLockError("LOCK_NOT_SHIPPED",
                                f"the package catalogue at {p} is not JSON ({e})", catalogue=str(p))
    tiers = cat.get("tiers") if isinstance(cat, dict) else None
    if not isinstance(tiers, dict) or not tiers:
        raise EmbedderLockError("LOCK_NOT_SHIPPED",
                                f"the package catalogue at {p} lists no tiers", catalogue=str(p))
    for tier, entry in tiers.items():
        _check_entry(entry, where=f"catalogue tier {tier!r}")
    rerankers = cat.get("rerankers")
    if rerankers is not None:
        if not isinstance(rerankers, dict):
            raise EmbedderLockError("LOCK_MALFORMED", f"the package catalogue at {p}: `rerankers` is not an object")
        for name, entry in rerankers.items():
            _check_pin(entry, required=_RERANKER_REQUIRED, where=f"catalogue reranker {name!r}")
    _catalogue_cache[key] = (st.st_mtime_ns, cat, hashlib.sha256(raw).hexdigest())
    return cat


def catalogue_rerankers(path: Optional[Union[str, Path]] = None) -> Dict[str, Dict[str, Any]]:
    """The catalogue's pinned rerankers (M1-C1's ring covers EVERY model the
    install fetches, not only embedders -- Amendment 7). Empty when none."""
    return dict(load_catalogue(path).get("rerankers") or {})


def catalogue_reranker(name: str, path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """One pinned reranker by catalogue name, or LOCK_NOT_SHIPPED."""
    entry = catalogue_rerankers(path).get(name)
    if entry is None:
        raise EmbedderLockError(
            "LOCK_NOT_SHIPPED",
            f"the package catalogue pins no reranker named {name!r}; a model this package "
            "does not pin is a model it does not load", reranker=name)
    return dict(entry)


def shipped_pin_for_hash(weights_hash: str, path: Optional[Union[str, Path]] = None
                         ) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Any catalogue pin -- an embedder tier OR a reranker -- carrying this hash."""
    hit = catalogue_entry_for_hash(weights_hash, path)
    if hit is not None:
        return hit
    for name, entry in catalogue_rerankers(path).items():
        if entry["weights_hash"] == weights_hash:
            return f"reranker:{name}", entry
    return None


def catalogue_sha256(path: Optional[Union[str, Path]] = None) -> str:
    load_catalogue(path)
    return _catalogue_cache[str(catalogue_path(path))][2]


def catalogue_tiers(path: Optional[Union[str, Path]] = None) -> Dict[str, Dict[str, Any]]:
    return dict(load_catalogue(path)["tiers"])


def catalogue_entry_for_hash(weights_hash: str, path: Optional[Union[str, Path]] = None
                             ) -> Optional[Tuple[str, Dict[str, Any]]]:
    for tier, entry in catalogue_tiers(path).items():
        if entry["weights_hash"] == weights_hash:
            return tier, entry
    return None


def is_shipped_hash(weights_hash: str, path: Optional[Union[str, Path]] = None) -> bool:
    return catalogue_entry_for_hash(weights_hash, path) is not None


def _check_pin(entry: Any, *, required: Sequence[str], where: str) -> None:
    """Shape-check a catalogue pin that writes no vector row (a reranker)."""
    if not isinstance(entry, dict):
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: entry is not an object")
    missing = [k for k in required if k not in entry]
    if missing:
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: missing {missing}", missing=missing)
    wh = entry.get("weights_hash")
    if not isinstance(wh, str) or not _HEX64.match(wh):
        raise EmbedderLockError("LOCK_MALFORMED",
                                f"{where}: weights_hash must be 64 lowercase hex characters, got {wh!r}")
    if not isinstance(entry.get("files"), list) or not entry["files"]:
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: files must be a non-empty list")
    if not isinstance(entry.get("license"), str) or not entry["license"].strip():
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: the licence must be NAMED")
    if not isinstance(entry.get("revision"), str) or not re.match(r"^[0-9a-f]{40}$", entry["revision"]):
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: revision must be a 40-hex hub commit")


def _check_entry(entry: Any, *, where: str) -> None:
    if not isinstance(entry, dict):
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: entry is not an object")
    missing = [k for k in _ENTRY_REQUIRED if k not in entry]
    if missing:
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: missing {missing}", missing=missing)
    wh = entry.get("weights_hash")
    if not isinstance(wh, str) or not _HEX64.match(wh):
        raise EmbedderLockError("LOCK_MALFORMED",
                                f"{where}: weights_hash must be 64 lowercase hex characters, got {wh!r}")
    if not isinstance(entry.get("dim"), int) or entry["dim"] <= 0:
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: dim must be a positive integer")
    for k in ("model_id", "revision", "vec_table"):
        if not isinstance(entry.get(k), str) or not entry[k].strip():
            raise EmbedderLockError("LOCK_MALFORMED", f"{where}: {k} must be a non-empty string")
    files = entry.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) and f for f in files):
        raise EmbedderLockError("LOCK_MALFORMED", f"{where}: files must be a non-empty list of paths")
    code = entry.get("code")
    if code is not None:
        if not isinstance(code, dict):
            raise EmbedderLockError("LOCK_MALFORMED", f"{where}: code must be an object")
        cmissing = [k for k in _CODE_REQUIRED if k not in code]
        if cmissing:
            raise EmbedderLockError("LOCK_MALFORMED", f"{where}: code is missing {cmissing}", missing=cmissing)
        for k in ("model_id", "revision"):
            if not isinstance(code.get(k), str) or not code[k].strip():
                raise EmbedderLockError("LOCK_MALFORMED", f"{where}: code.{k} must be a non-empty string")
        cf = code.get("files")
        if not isinstance(cf, list) or not cf or not all(isinstance(f, str) and f for f in cf):
            raise EmbedderLockError("LOCK_MALFORMED", f"{where}: code.files must be a non-empty list of paths")


# --------------------------------------------------------------------------
# The install lock: locate, read, write.
# --------------------------------------------------------------------------

def default_data_dir() -> Path:
    """The tree root (organs/../), for `describe(None)` -- the module's own
    `__main__` diagnostic with no argument. NOT on any load, stamp or read path
    (r2 F16): the loader takes the lock its caller resolved from the database's
    own directory, the reader resolves from the connection's file, and the
    engine resolves from its DB_PATH (symlinks resolved). One database, one lock."""
    return Path(__file__).resolve().parent.parent


def data_dir_of(conn: sqlite3.Connection) -> Optional[Path]:
    """The directory holding the `main` database of this connection, or None for
    an in-memory / temporary database (which has no sibling and so no lock)."""
    try:
        for _seq, name, file in conn.execute("PRAGMA database_list").fetchall():
            if name == "main":
                return Path(file).resolve().parent if file else None
    except sqlite3.Error:
        return None
    return None


def lock_path_for(target: Union[None, str, Path, sqlite3.Connection]) -> Path:
    """`<data-dir>/embedder.lock.json` for a data dir, a database path, or a
    connection. None -> the package's own data dir."""
    if target is None:
        return default_data_dir() / LOCK_NAME
    if isinstance(target, sqlite3.Connection):
        d = data_dir_of(target)
        if d is None:
            raise EmbedderLockError(
                "LOCK_MISSING",
                "this connection is attached to an in-memory or temporary database, "
                "which has no data dir and therefore no embedder.lock.json beside it")
        return d / LOCK_NAME
    p = Path(target)
    if p.is_file() or p.suffix in (".db", ".sqlite", ".sqlite3"):
        return p.resolve().parent / LOCK_NAME
    return p / LOCK_NAME


def read_lock(path: Union[str, Path]) -> Dict[str, Any]:
    """Open and shape-check the install lock. LOCK_MISSING / LOCK_MALFORMED only:
    catalogue membership is the reader's and the loader's question, decided
    AFTER the facts (rows, bytes) -- see the module header."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise EmbedderLockError(
            "LOCK_MISSING",
            f"no embedder lock at {p}. A clean install writes it beside the database "
            "(`claude_memory.py init`); nothing embeds or searches without it.",
            lock=str(p))
    except OSError as e:
        raise EmbedderLockError("LOCK_MISSING", f"embedder lock at {p} unreadable: {e}", lock=str(p))
    try:
        lock = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise EmbedderLockError("LOCK_MALFORMED", f"embedder lock at {p} is not JSON: {e}", lock=str(p))
    if not isinstance(lock, dict):
        raise EmbedderLockError("LOCK_MALFORMED", f"embedder lock at {p} is not an object", lock=str(p))
    missing = [k for k in _LOCK_REQUIRED if k not in lock]
    if missing:
        raise EmbedderLockError("LOCK_MALFORMED", f"embedder lock at {p} is missing {missing}",
                                lock=str(p), missing=missing)
    _check_entry(lock, where=f"lock {p}")
    if not isinstance(lock.get("tier"), str) or not lock["tier"].strip():
        raise EmbedderLockError("LOCK_MALFORMED", f"embedder lock at {p}: tier must be a non-empty string",
                                lock=str(p))
    lock["_path"] = str(p)
    return lock


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass                                    # not every filesystem allows it


def write_lock(data_dir: Union[str, Path], tier: str, *, catalogue: Optional[Union[str, Path]] = None,
               locked_by: str = "claude_memory.init", replace: bool = False) -> Dict[str, Any]:
    """Write `<data-dir>/embedder.lock.json` naming exactly ONE catalogue tier.

    temp -> fsync -> rename, so a crash leaves either the old lock or the new one
    and never a torn file; a receipt line is appended to the history file on every
    write. An existing lock is NEVER silently replaced (an init re-run must not
    re-point a brain); `replace=True` is for `tier set` (Amendment 4 L1, not
    built this round) and is not reachable from any shipped verb today.
    """
    d = Path(data_dir)
    tiers = catalogue_tiers(catalogue)
    if tier not in tiers:
        raise EmbedderLockError(
            "LOCK_NOT_SHIPPED",
            f"tier {tier!r} is not in the package catalogue; shipped tiers: {sorted(tiers)}",
            tier=tier, shipped=sorted(tiers))
    target = d / LOCK_NAME
    previous: Optional[Dict[str, Any]] = None
    if target.exists():
        previous = read_lock(target)
        if not replace:
            if previous["tier"] != tier:
                raise EmbedderLockError(
                    "WEIGHTS_MISMATCH",
                    f"{target} already locks tier {previous['tier']!r} "
                    f"({previous['weights_hash'][:12]}…); refusing to re-point it to {tier!r}. "
                    "Re-pointing an install without re-embedding is the defect this file exists "
                    "to make impossible; `tier set` (not built) is the path that re-embeds.",
                    lock=str(target), existing_tier=previous["tier"], requested_tier=tier)
            return previous                     # same tier: idempotent, nothing written
    entry = tiers[tier]
    lock: Dict[str, Any] = {
        "schema": LOCK_SCHEMA,
        "tier": tier,
        "model_id": entry["model_id"],
        "revision": entry["revision"],
        "weights_hash": entry["weights_hash"],
        "dim": int(entry["dim"]),
        "vec_table": entry["vec_table"],
        "files": list(entry["files"]),
        "code": (dict(entry["code"]) if entry.get("code") else None),
        "license": entry.get("license"),
        "locked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "locked_by": locked_by,
        "catalogue_hash": catalogue_sha256(catalogue),
        "catalogue": str(catalogue_path(catalogue).name),
    }
    if lock["code"] is None:
        del lock["code"]                        # a tier with no remote-code repository
    d.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".embedder.lock.", suffix=".tmp", dir=str(d))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lock, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(d)
    receipt = {
        "at": lock["locked_at"], "action": "replace" if previous else "init",
        "by": locked_by, "tier": tier, "weights_hash": lock["weights_hash"],
        "model_id": lock["model_id"], "revision": lock["revision"],
        "catalogue_hash": lock["catalogue_hash"],
        "previous": ({"tier": previous["tier"], "weights_hash": previous["weights_hash"]}
                     if previous else None),
    }
    with open(d / LOCK_HISTORY_NAME, "a", encoding="utf-8") as h:
        h.write(json.dumps(receipt, sort_keys=True) + "\n")
        h.flush()
        os.fsync(h.fileno())
    lock["_path"] = str(target)
    return lock


def resolve(target: Union[None, str, Path, sqlite3.Connection] = None) -> Dict[str, Any]:
    """The validated install lock for a data dir / db path / connection.

    This is what `embed_resolver.resolve` derives (table, dim) from. It reads the
    lock and NOTHING in the database: `metadata.active_embedding_*` is the sibling
    trap and on a clean install the key no longer exists. Catalogue membership is
    deliberately NOT checked here (module header: facts before policy).
    """
    return read_lock(lock_path_for(target))


# --------------------------------------------------------------------------
# The process register: the one embedder resident, with its COMPUTED checksum.
# --------------------------------------------------------------------------

_LOADED: Dict[str, Any] = {}


def register_loaded(*, model_id: str, revision: str, weights_hash: str, dim: int,
                    vec_table: str, tier: Optional[str] = None,
                    snapshot: Optional[str] = None) -> Dict[str, Any]:
    """Record the embedder this process loaded and the checksum it COMPUTED.

    One model resident at a time: registering a different checksum while one is
    held is refused (WEIGHTS_MISMATCH), so a stamp can never name a model other
    than the one that produced the bytes. Re-registering the same checksum is a
    no-op.
    """
    if not _HEX64.match(weights_hash or ""):
        raise EmbedderLockError("WEIGHTS_MISMATCH",
                                f"a computed checksum must be 64 hex characters, got {weights_hash!r}")
    if _LOADED and _LOADED["weights_hash"] != weights_hash:
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"an embedder is already resident in this process ({_LOADED['model_id']} "
            f"{_LOADED['weights_hash'][:12]}…); refusing to register {model_id} "
            f"{weights_hash[:12]}…. One model per process.",
            resident=dict(_LOADED))
    _LOADED.update({"model_id": model_id, "revision": revision, "weights_hash": weights_hash,
                    "dim": int(dim), "vec_table": vec_table, "tier": tier, "snapshot": snapshot,
                    "registered_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return dict(_LOADED)


def loaded() -> Optional[Dict[str, Any]]:
    return dict(_LOADED) if _LOADED else None


def clear_loaded() -> None:
    """For tests and for a process that deliberately releases its model. The
    engine never calls this on a live path."""
    _LOADED.clear()


def require_loaded_hash(*, vec_table: Optional[str] = None, dim: Optional[int] = None) -> str:
    """The checksum the loader computed for the embedder resident in THIS process.

    This is the value the stamp carries. No embedder loaded -> no fact to write ->
    refused. A caller that names the table/dim it just wrote to gets them
    checked against the resident embedder's: the stamp describes the bytes the
    loaded model produced, so the table must be that model's table.
    """
    if not _LOADED:
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            "no embedder has been loaded in this process, so there is no computed "
            "weights_hash to stamp; a vector without a stamp is not written")
    if vec_table is not None and vec_table != _LOADED["vec_table"]:
        raise EmbedderLockError(
            "STAMP_INCONSISTENT",
            f"the write targets {vec_table!r} but the resident embedder "
            f"({_LOADED['model_id']}) writes {_LOADED['vec_table']!r}",
            vec_table=vec_table, resident=dict(_LOADED))
    if dim is not None and int(dim) != int(_LOADED["dim"]):
        raise EmbedderLockError(
            "STAMP_INCONSISTENT",
            f"the write is {dim}-wide but the resident embedder is {_LOADED['dim']}-wide",
            dim=dim, resident=dict(_LOADED))
    return _LOADED["weights_hash"]


# --------------------------------------------------------------------------
# The load-time check: bytes on disk vs the lock, then the catalogue.
# --------------------------------------------------------------------------

def hf_offline() -> bool:
    """HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE, as huggingface_hub reads them: a
    reader under either never fetches, and this module says so TYPED
    (SNAPSHOT_ABSENT) instead of letting the hub raise its own exception."""
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        v = (os.environ.get(var) or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    return False


def _fetch_pinned(model_id: str, revision: str, *, allow_fetch: bool, weights_hash: str,
                  what: str, allow_patterns: Optional[Sequence[str]] = None) -> Path:
    """The local snapshot for (model_id, revision), fetched at that revision when
    absent and fetching is allowed. Nothing is fetched unless the pin's hash is
    in the catalogue (checked by the caller before the first call). Absent and
    not allowed -- or the process is HF-offline -- is SNAPSHOT_ABSENT, typed
    (M1 round 5 #6): the pin is not on this disk and this process may not go
    and get it; the caller says so instead of downloading 3 GB into a self-test.

    `allow_patterns` (M1 round 6 #1, CUR-M1R5-F3, Drew approved): the fetch is
    bounded to the files the pin NAMES -- `files[]` for a model, `code.files[]`
    for a remote-code repository -- so nothing the catalogue does not hash lands
    on the customer's disk. Measured before this: 61.2 % of a clean install's
    fetched bytes (2,766,959,110 of 4,522,421,943) were files no pin named,
    including 1,099,584,724 B of weights for a repository pinned only as code."""
    snap = snapshot_dir_for(model_id, revision)
    if snap is not None:
        return snap
    if not allow_fetch or hf_offline():
        raise EmbedderLockError(
            "SNAPSHOT_ABSENT",
            f"no local snapshot for {what} {model_id}@{revision[:12]} under {hf_hub_cache()} and "
            + ("this process is HF-offline (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE)" if hf_offline()
               else "fetching is disabled for this caller"),
            weights_hash=weights_hash, model_id=model_id, revision=revision, what=what,
            hf_hub_cache=str(hf_hub_cache()))
    from huggingface_hub import snapshot_download    # only here, only when absent
    kwargs: Dict[str, Any] = {"revision": revision}
    if allow_patterns:
        kwargs["allow_patterns"] = list(allow_patterns)
    snapshot_download(model_id, **kwargs)
    snap = snapshot_dir_for(model_id, revision)
    if snap is None:
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"fetched {what} {model_id}@{revision[:12]} but no snapshot appeared under {hf_hub_cache()}",
            weights_hash=weights_hash)
    return snap


def verify_snapshot_against_lock(lock: Dict[str, Any], *, allow_fetch: bool = True,
                                 catalogue: Optional[Union[str, Path]] = None,
                                 pin_kind: str = "tier") -> Tuple[Path, str]:
    """Locate the snapshot the lock names -- AND the remote-code snapshot its
    `code` pin names, when it has one -- compute the checksum over both, compare.

    Returns (snapshot_dir, computed_hash) -- the value the loader then REGISTERS
    and the stamp then WRITES. Refusals, in order:
      * a snapshot absent locally and the lock's hash is not in the catalogue ->
        LOCK_NOT_SHIPPED, before any fetch (nothing outside the catalogue is
        ever downloaded on this path);
      * a snapshot absent and allow_fetch -> fetched at ITS pinned revision (the
        model at lock.revision, the code at lock.code.revision);
      * computed != lock.weights_hash -> WEIGHTS_MISMATCH (forty f's land here;
        so does a changed byte in the code repository);
      * computed == lock but the hash is not shipped -> LOCK_NOT_SHIPPED.
    """
    if pin_kind not in ("tier", "reranker"):
        raise EmbedderLockError("LOCK_MALFORMED", f"pin_kind must be 'tier' or 'reranker', got {pin_kind!r}")

    def _shipped(h: str) -> bool:
        # a tier lock is shipped only as a tier; a reranker pin only as a reranker
        if pin_kind == "tier":
            return is_shipped_hash(h, catalogue)
        return any(e["weights_hash"] == h for e in catalogue_rerankers(catalogue).values())

    code = lock.get("code")
    model_snap = snapshot_dir_for(lock["model_id"], lock["revision"])
    code_snap = snapshot_dir_for(code["model_id"], code["revision"]) if code else None
    if model_snap is None or (code and code_snap is None):
        if not _shipped(lock["weights_hash"]):
            raise EmbedderLockError(
                "LOCK_NOT_SHIPPED",
                f"the lock names {lock['model_id']}@{lock['revision'][:12]} with hash "
                f"{lock['weights_hash'][:12]}…, which is not in the package catalogue, and "
                "the weights are not on this machine; refusing to fetch anything the "
                "package does not ship", weights_hash=lock["weights_hash"])
    if model_snap is None:
        model_snap = _fetch_pinned(lock["model_id"], lock["revision"], allow_fetch=allow_fetch,
                                   weights_hash=lock["weights_hash"], what="model",
                                   allow_patterns=lock.get("files") or None)
    if code and code_snap is None:
        code_snap = _fetch_pinned(code["model_id"], code["revision"], allow_fetch=allow_fetch,
                                  weights_hash=lock["weights_hash"], what="remote code",
                                  allow_patterns=code.get("files") or None)
    try:
        computed = compute_entry_hash(lock, snapshot=model_snap, code_snapshot=code_snap)
    except FileNotFoundError as e:
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"snapshot {model_snap} (or the code snapshot {code_snap}) lacks a file the lock hashes "
            f"({e}); the bytes on disk are not the bytes the lock describes",
            snapshot=str(model_snap), code_snapshot=(str(code_snap) if code_snap else None),
            weights_hash=lock["weights_hash"])
    if computed != lock["weights_hash"]:
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"the weights on disk at {model_snap}" + (f" + code at {code_snap}" if code_snap else "")
            + f" hash to {computed[:12]}… but the lock promises "
            f"{lock['weights_hash'][:12]}…; refusing to load. The lock is the promise, the "
            "bytes are the fact, and they disagree.",
            snapshot=str(model_snap), code_snapshot=(str(code_snap) if code_snap else None),
            computed=computed, lock_hash=lock["weights_hash"])
    if not _shipped(computed):
        raise EmbedderLockError(
            "LOCK_NOT_SHIPPED",
            f"{lock['model_id']} hashes to {computed[:12]}… as the lock says, but that hash "
            "is not in the package catalogue; this package does not ship it",
            weights_hash=computed)
    return model_snap, computed


# --------------------------------------------------------------------------
# THE INSTALL LANE (M1 round 6 #1; Drew's ruling 2026-09-20, Amendment 8):
# "everything that matters to get the full memory system officially working is
# part of the install". The set of models the memory system needs is DERIVED --
# the lock's embedder, the remote-code repository its `code` pin names, and every
# catalogue-pinned model a SHIPPED organ loads (a reranker whose `loader` organ
# is laid) -- and fetched at the pins, bounded to the files the pins name.
# --------------------------------------------------------------------------

def _organ_present(loader: Optional[str], organs_dir: Optional[Union[str, Path]]) -> bool:
    if not loader:
        return False
    rel = loader[len("organs/"):] if loader.startswith("organs/") else loader
    base = Path(organs_dir) if organs_dir else Path(__file__).resolve().parent
    return (base / rel).is_file()


def models_needed(lock: Dict[str, Any], *, catalogue: Optional[Union[str, Path]] = None,
                  organs_dir: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """The models the memory system needs to WORK under `lock`, derived, with the
    derivation printed beside the set (never a typed list):
      1. the lock's embedder  (tiers.<lock.tier>: model_id @ revision, files[]);
      2. its remote-code repository, when the tier pins one (code.model_id @
         code.revision, code.files[]);
      3. every `rerankers` entry with status "active" whose `loader` organ is
         present in `organs_dir` (the shipped reranker today: organs/qwen_reranker.py).
    A catalogue reranker whose loader is NOT laid is listed under
    `not_needed_loader_absent` so the reader can see why it is not fetched."""
    _check_entry(lock, where="lock") if "vec_table" in lock else None
    needed: List[Dict[str, Any]] = [{
        "what": "embedder", "pin": f"tiers.{lock.get('tier')}", "model_id": lock["model_id"],
        "revision": lock["revision"], "files": list(lock.get("files") or []),
        "weights_hash": lock["weights_hash"], "pin_kind": "tier",
    }]
    code = lock.get("code")
    if code:
        needed.append({"what": "remote-code", "pin": f"tiers.{lock.get('tier')}.code",
                       "model_id": code["model_id"], "revision": code["revision"],
                       "files": list(code.get("files") or []), "weights_hash": lock["weights_hash"],
                       "pin_kind": "tier"})
    skipped: List[Dict[str, Any]] = []
    for name, entry in catalogue_rerankers(catalogue).items():
        if str(entry.get("status", "active")) != "active":
            skipped.append({"pin": f"rerankers.{name}", "model_id": entry["model_id"], "why": "status != active"})
            continue
        if not _organ_present(entry.get("loader"), organs_dir):
            skipped.append({"pin": f"rerankers.{name}", "model_id": entry["model_id"],
                            "why": f"loader {entry.get('loader')!r} is not present beside this module"})
            continue
        needed.append({"what": "reranker", "pin": f"rerankers.{name}", "model_id": entry["model_id"],
                       "revision": entry["revision"], "files": list(entry.get("files") or []),
                       "weights_hash": entry["weights_hash"], "pin_kind": "reranker",
                       "loader": entry.get("loader")})
    return {
        "derivation": ("the lock's embedder (tiers.<tier>) + its remote-code repository (tiers.<tier>.code, when "
                       "pinned) + every catalogue `rerankers` entry with status active whose `loader` organ is "
                       "present beside embedder_lock.py; nothing is fetched by bare name"),
        "catalogue_hash": catalogue_sha256(catalogue), "tier": lock.get("tier"),
        "needed": needed, "count": len(needed), "not_needed_loader_absent": skipped,
    }


def _snapshot_census(snap: Path, named: Sequence[str]) -> Dict[str, Any]:
    """What is on disk under a snapshot, split into the files the pin names and
    the files it does not (bytes follow symlinks into blobs/, deduplicated)."""
    named_set = set(named)
    on_disk: List[str] = []
    for root, _d, files in os.walk(snap):
        for fn in files:
            on_disk.append(str(Path(root, fn).relative_to(snap)))
    def _bytes(rels: Iterable[str]) -> int:
        seen, total = set(), 0
        for rel in rels:
            try:
                st = os.stat(snap / rel)
            except OSError:
                continue
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key); total += st.st_size
        return total
    unnamed = sorted(f for f in on_disk if f not in named_set)
    present_named = sorted(f for f in on_disk if f in named_set)
    return {"files_on_disk": len(on_disk), "named_present": len(present_named),
            "named_missing": sorted(named_set - set(on_disk)),
            "bytes_named": _bytes(present_named), "unnamed_files": unnamed,
            "unnamed_bytes": _bytes(unnamed)}


def fetch_models_needed(lock: Dict[str, Any], *, catalogue: Optional[Union[str, Path]] = None,
                        organs_dir: Optional[Union[str, Path]] = None,
                        allow_fetch: bool = True) -> Dict[str, Any]:
    """THE INSTALL'S FETCH. For every model `models_needed` derives: locate the
    snapshot the pin names; fetch it at the pin -- bounded by the pin's files[]
    -- when absent and fetching is allowed; hash it against the pin; census what
    landed. Returns the envelope the install receipt carries.

    Refuses TYPED, `MODELS_ABSENT`, when any needed model is absent and cannot be
    fetched (the process is HF-offline, or `allow_fetch` is False, or the hub
    could not be reached): the refusal names every absent repository, the files
    each pin names, the bytes already on disk, and the retry verb. The caller
    (the install) then does NOT report a working memory: a lock naming absent
    weights is not an install (Council 5, DESSA).
    """
    plan = models_needed(lock, catalogue=catalogue, organs_dir=organs_dir)
    hub = hf_hub_cache()
    out: Dict[str, Any] = {"status": "ok", "hf_hub_cache": str(hub), "offline": hf_offline(),
                           "derivation": plan["derivation"], "catalogue_hash": plan["catalogue_hash"],
                           "needed": plan["count"], "not_needed_loader_absent": plan["not_needed_loader_absent"],
                           "models": [], "fetched": 0, "present_before": 0,
                           "bytes_named_total": 0, "unnamed_bytes_total": 0, "unnamed_files_total": 0}
    absent: List[Dict[str, Any]] = []
    for m in plan["needed"]:
        rec: Dict[str, Any] = {k: m[k] for k in ("what", "pin", "model_id", "revision", "pin_kind")}
        rec["files_named"] = len(m["files"])
        snap = snapshot_dir_for(m["model_id"], m["revision"], hub=hub)
        rec["present_before"] = snap is not None
        if snap is None:
            if not allow_fetch or hf_offline():
                rec["status"] = "absent"
                rec["why"] = ("this process is HF-offline (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE)" if hf_offline()
                              else "fetching is disabled for this caller")
                absent.append(rec); out["models"].append(rec)
                continue
            try:
                snap = _fetch_pinned(m["model_id"], m["revision"], allow_fetch=True,
                                     weights_hash=m["weights_hash"], what=m["what"],
                                     allow_patterns=m["files"] or None)
            except EmbedderLockError:
                raise
            except Exception as e:                      # the hub unreachable, a DNS failure, a 5xx
                rec["status"] = "absent"; rec["why"] = f"fetch failed: {type(e).__name__}: {e}"
                absent.append(rec); out["models"].append(rec)
                continue
            rec["fetched"] = True; out["fetched"] += 1
        else:
            rec["fetched"] = False; out["present_before"] += 1
        rec["snapshot"] = str(snap)
        rec.update(_snapshot_census(snap, m["files"]))
        out["bytes_named_total"] += rec["bytes_named"]
        out["unnamed_bytes_total"] += rec["unnamed_bytes"]
        out["unnamed_files_total"] += len(rec["unnamed_files"])
        rec["status"] = "present"
        out["models"].append(rec)
    if absent:
        names = ", ".join(f"{a['model_id']}@{a['revision'][:12]} ({a['files_named']} files named by {a['pin']})"
                          for a in absent)
        raise EmbedderLockError(
            "MODELS_ABSENT",
            f"{len(absent)} of {plan['count']} models the memory system needs are not under {hub} and could "
            f"not be fetched: {names}; bytes on disk for the needed set: {out['bytes_named_total']:,}. "
            f"The install is NOT complete and no working memory is claimed. Retry, with the network: "
            f"`voidstead memory install` (or `python3 organs/model_fetch.py --db <brain>/claude_memory.db`) -- "
            f"or populate the cache yourself at exactly these revisions.",
            absent=absent, needed=plan["count"], hf_hub_cache=str(hub), offline=hf_offline(),
            bytes_on_disk=out["bytes_named_total"], models=out["models"],
            retry=["voidstead memory install --package <checkout> --data-dir <DIR>",
                   "python3 organs/model_fetch.py --db <brain>/claude_memory.db"])
    # every present model is then HASHED against its pin (the loader's own check),
    # so "present" means "the bytes the catalogue promised", never "a directory exists"
    for m, rec in zip(plan["needed"], out["models"]):
        if m["what"] == "remote-code":
            continue                                    # hashed together with its model below
        entry = dict(lock) if m["pin_kind"] == "tier" else catalogue_reranker(m["pin"].split(".", 1)[1], catalogue)
        _snap, computed = verify_snapshot_against_lock(entry, allow_fetch=False, catalogue=catalogue,
                                                       pin_kind=m["pin_kind"])
        rec["weights_hash_computed"] = computed
        rec["verified"] = (computed == m["weights_hash"])
    out["verified"] = sum(1 for r in out["models"] if r.get("verified"))
    out["verified_of"] = sum(1 for m in plan["needed"] if m["what"] != "remote-code")
    return out


def describe(target: Union[None, str, Path, sqlite3.Connection] = None) -> Dict[str, Any]:
    """A never-raising description for `doctor`: what the lock says, whether the
    catalogue ships it, whether the snapshot is present, what is resident."""
    out: Dict[str, Any] = {"lock": None, "lock_path": None, "refusal": None,
                           "shipped": None, "snapshot_present": None, "code_snapshot_present": None,
                           "catalogue_hash": None, "resident": loaded()}
    try:
        out["catalogue_hash"] = catalogue_sha256()
    except EmbedderLockError as e:
        out["refusal"] = e.as_dict()
    try:
        path = lock_path_for(target)
        out["lock_path"] = str(path)
        lock = read_lock(path)
    except EmbedderLockError as e:
        out["refusal"] = e.as_dict()
        return out
    out["lock"] = {k: v for k, v in lock.items() if k not in ("files", "_path")}
    try:
        out["shipped"] = is_shipped_hash(lock["weights_hash"])
    except EmbedderLockError as e:
        out["refusal"] = e.as_dict()
    out["snapshot_present"] = snapshot_dir_for(lock["model_id"], lock["revision"]) is not None
    code = lock.get("code")
    out["code_snapshot_present"] = (snapshot_dir_for(code["model_id"], code["revision"]) is not None
                                    if code else None)
    return out


if __name__ == "__main__":
    import sys
    tgt = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(describe(tgt), indent=1, sort_keys=True))
