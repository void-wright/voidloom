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
       because seed/ holds what is loaded once, when a brain is born, and this
       file is read EVERY TIME THE EMBEDDER LOADS: it belongs with the code that
       reads it.
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
    revision keeps its original name, `model_ref`.

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

import contextlib
import hashlib
import json
import os
import shlex
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

CATALOGUE_PATH = Path(__file__).resolve().parent / "embedders.lock.json"
LOCK_NAME = "embedder.lock.json"
LOCK_HISTORY_NAME = "embedder.lock.history.jsonl"
BRAIN_NAME = "claude_memory.db"
BRAIN_LOCK_KEY = "embedder_lock_written"       # metadata row: see mark_brain
_LOCK_WRITING_RECEIPTS = ("init", "replace", "tier-set")
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
    """The cache huggingface_hub itself reads, in ITS precedence (huggingface_hub/constants.py at the pinned
    version): HF_HUB_CACHE, then HUGGINGFACE_HUB_CACHE, then HF_HOME/hub, then $XDG_CACHE_HOME/huggingface/hub, then
    ~/.cache/huggingface/hub. The ONE reader of these variables in this package: two readers read two caches.

    One deliberate difference: a variable set to the empty string counts as unset here, where the library would
    take it as a relative path."""
    def _set(var: str) -> Optional[Path]:
        v = (os.environ.get(var) or "").strip()
        return Path(os.path.expandvars(os.path.expanduser(v))) if v else None

    hub = _set("HF_HUB_CACHE") or _set("HUGGINGFACE_HUB_CACHE")
    if hub:
        return hub
    home = _set("HF_HOME")
    if home:
        return home / "hub"
    return (_set("XDG_CACHE_HOME") or Path.home() / ".cache") / "huggingface" / "hub"


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


def append_receipt(data_dir: Union[str, Path], receipt: Dict[str, Any]) -> None:
    """One line on the lock's history, owner-only whatever the umask: `lost_lock` trusts what this file says."""
    fd = os.open(str(Path(data_dir) / LOCK_HISTORY_NAME), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as h:
        h.write(json.dumps(receipt, sort_keys=True) + "\n")
        h.flush()
        os.fsync(h.fileno())


def brain_python() -> str:
    """The interpreter that can run this install's organs: its own venv when it has one. Whatever `python3` means on the
    caller's PATH usually has no sqlite_vec, so a repair command that names it fails where it is printed."""
    root = Path(os.path.abspath(__file__)).parent.parent
    for rel in ("venv/bin/python3", "venv/Scripts/python.exe"):
        if (root / rel).exists():
            return str(root / rel)
    return sys.executable or "python3"


def brain_db() -> str:
    """This install's brain: WIZARD_LIBRARY_DB when set (an attached brain lives elsewhere), else beside the organs."""
    return (os.environ.get("WIZARD_LIBRARY_DB") or "").strip() or \
        str(Path(os.path.abspath(__file__)).parent.parent / "claude_memory.db")


def organ_command(rel: str, *args: Any) -> str:
    """A command that runs an organ from any directory: absolute paths under the install's own interpreter. Every repair
    command the product prints goes through here, so what is printed is what works."""
    organs = Path(os.path.abspath(__file__)).parent
    return shell_command(brain_python(), organs / rel, *args)


def shell_command(*argv: Any, platform: Optional[str] = None) -> str:
    """One command, quoted for the shell it is printed to: PowerShell on Windows, where a quoted first word is only a
    string until `&` calls it, and a POSIX shell everywhere else."""
    if (platform or sys.platform) == "win32":
        return "& " + " ".join(_ps_quote(a) for a in argv)
    return " ".join(shlex.quote(str(a)) for a in argv)


def _ps_quote(word: Any) -> str:
    """A PowerShell literal. Single quotes, because `$` and a backtick in a path expand inside double ones."""
    return "'%s'" % str(word).replace("'", "''")


def fetch_command(db: Optional[Union[str, Path]] = None) -> str:
    return organ_command("model_fetch.py", "--db", db or brain_db())


def mark_brain(brain: Union[str, Path, sqlite3.Connection], tier: Optional[str]) -> None:
    """Say inside the brain whether a lock was written for it: `metadata.embedder_lock_written` holds the tier, and an
    empty value says a rollback took the brain back to having none. Deleting the lock and its history leaves this row
    behind. A connection takes the row in its own transaction; a path writes it now, and a brain too busy or too damaged
    to take it loses only this second record, never the lock. A row that already says so is left alone: its table
    stamps every write, and an install run twice must leave the brain's bytes as they were."""
    value = tier or ""
    own = not isinstance(brain, sqlite3.Connection)
    conn: Optional[sqlite3.Connection] = None
    try:
        if own and not Path(brain).is_file():
            return
        conn = sqlite3.connect(str(brain), timeout=60.0) if own else brain
        held = conn.execute("SELECT value FROM metadata WHERE key = ?", (BRAIN_LOCK_KEY,)).fetchone()
        if held is not None and held[0] == value:
            return
        conn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (BRAIN_LOCK_KEY, value))
        if own:
            conn.commit()
    except sqlite3.Error as e:
        print(f"[embedder_lock] the lock is written, but its row in the brain is not: {e}", file=sys.stderr)
    finally:
        if own and conn is not None:
            conn.close()


_BRAIN_UNREADABLE = object()


def _read_brain(brain: Union[None, str, Path, sqlite3.Connection], sql: str, args: Sequence[Any]) -> Optional[Any]:
    """One value from this brain's own database, read-only. None when it holds none: no file, no such table, no row.
    `_BRAIN_UNREADABLE` when the file is there and did not answer (busy, damaged, not ours to open), which says nothing
    about what it holds."""
    own = not isinstance(brain, sqlite3.Connection)
    conn: Optional[sqlite3.Connection] = None
    try:
        if brain is None or (own and not Path(brain).is_file()):
            return None
        conn = sqlite3.connect(f"{Path(brain).resolve().as_uri()}?mode=ro", uri=True, timeout=5.0) if own else brain
        row = conn.execute(sql, args).fetchone()
        return None if row is None else row[0]
    except sqlite3.OperationalError as e:
        return None if "no such table" in str(e) else _BRAIN_UNREADABLE
    except (sqlite3.Error, OSError, ValueError):
        return _BRAIN_UNREADABLE
    finally:
        if own and conn is not None:
            conn.close()


def _brain_beside(data_dir: Path) -> Path:
    return Path(data_dir) / BRAIN_NAME


def _tier_of_brain(brain: Union[None, str, Path, sqlite3.Connection]) -> Optional[str]:
    """The catalogue tier whose table this brain declares, or None: a tier nothing ships (jina), or nothing declared."""
    table = _read_brain(brain, "SELECT value FROM metadata WHERE key = 'active_embedding_table'", ())
    try:
        return next((t for t, e in catalogue_tiers().items() if e["vec_table"] == table), None)
    except EmbedderLockError:
        return None


def lost_lock(data_dir: Union[str, Path], brain: Union[None, str, Path, sqlite3.Connection] = None
              ) -> Optional[Dict[str, Any]]:
    """Should a lock be beside this database? Only what `write_lock` itself left can say so: a receipt in the history
    file, and the row it wrote into the brain (`mark_brain`), which survives both files being deleted. {"tier": ...}
    when the last word on the lock says it exists, None when no receipt and no row says one was written, or a rollback
    took the brain back to having none. A brain that did not answer is not a brain with no record: with no receipt to
    go on the answer is {"tier": None, "unreadable": True}, read as a lost lock until it does. No record, no lock: a brain that predates the lock is a legal brain, and a stamp
    proves nothing here, because a store, a `tier set` build and a website store all write shipped checksums with no
    lock in sight. Only the receipts that move the lock count: `re-embed` writes one on a brain that never had a lock.
    A history with nothing readable in it still proves a lock was written. `brain` is a connection or a path; by
    default the brain of the usual name beside the history."""
    d = Path(data_dir)
    mark = _read_brain(brain if brain is not None else _brain_beside(d),
                       "SELECT value FROM metadata WHERE key = ?", (BRAIN_LOCK_KEY,))
    # Empty, not absent: the rollback writes this row in the same transaction as the rollback, so it cannot outlive or
    # precede the state it names, and a rollback killed part-way reads the state it left. Only this question reads it:
    # no search filter is ever chosen from this answer.
    if mark == "":
        return None
    written = _lock_in_history(d)
    if mark is _BRAIN_UNREADABLE:
        return written if written is not None else {"tier": None, "unreadable": True}
    if written is None and mark:
        return {"tier": str(mark)}
    if written is not None and not written.get("tier") and mark:
        return {"tier": str(mark)}
    return written


def _lock_in_history(data_dir: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = (data_dir / LOCK_HISTORY_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    readable = 0
    locked: Optional[Dict[str, Any]] = None
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        readable += 1
        action = rec.get("action")
        if locked is None:
            if action == "tier-rollback" and not rec.get("restored_lock"):
                return None
            if action == "tier-rollback":
                # the rollback, not the switch it undid, is the last word on which tier the lock named
                back = [t for t, e in catalogue_tiers().items() if e["vec_table"] == rec.get("restored_vec_table")]
                if back:
                    return {"tier": back[0]}
                locked = {"tier": None}
            elif action in _LOCK_WRITING_RECEIPTS:
                locked = {"tier": None}
        if locked is not None and action in _LOCK_WRITING_RECEIPTS:
            to = rec.get("to")
            locked["tier"] = rec.get("tier") or (to.get("tier") if isinstance(to, dict) else None)
            if locked["tier"]:
                return locked
    if locked is not None:
        return locked
    return {"tier": None} if lines and not readable else None


def refuse_lost_lock(target: Any) -> None:
    """A write on a brain that lost its lock refuses: nothing vouches for the model the vector would be stamped under."""
    try:
        resolve(target)
    except EmbedderLockError as e:
        if e.code == "LOCK_MISSING" and e.detail.get("was_locked"):
            raise


def read_lock(path: Union[str, Path], *, brain: Union[None, str, Path, sqlite3.Connection] = None) -> Dict[str, Any]:
    """Open and shape-check the install lock. LOCK_MISSING / LOCK_MALFORMED only:
    catalogue membership is the reader's and the loader's question, decided
    AFTER the facts (rows, bytes) -- see the module header. `brain` is the database this
    lock serves, when the caller knows it: an attached brain need not have the usual name."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        lost = lost_lock(p.parent, brain)
        if lost is not None and lost.get("unreadable"):
            raise EmbedderLockError(
                "LOCK_MISSING", f"no embedder lock at {p}, and this brain did not answer when asked whether one was "
                "written: it is busy or damaged, and that is not a record of having none. Nothing was changed. Try "
                "again when it is free; `voidloom doctor` names what is wrong if it stays this way.",
                lock=str(p), was_locked=True, tier=None, unreadable=True)
        if lost is not None:
            tier = lost.get("tier") or _tier_of_brain(brain if brain is not None else _brain_beside(p.parent))
            said = f"tier {tier}" if tier else "its tier is not recorded"
            lead = (f"no embedder lock at {p}, and the record a lock leaves behind says one was written ({said}): "
                    f"this brain was locked and the file is gone, so nothing says which model its vectors came from. ")
            if tier:
                fix = organ_command("claude_memory.py", "init", "--tier", tier)
                raise EmbedderLockError("LOCK_MISSING", lead + f"Restore the file from a backup, or run `{fix}` to "
                                        "write it again from the catalogue.",
                                        lock=str(p), was_locked=True, tier=tier, retry=[fix])
            raise EmbedderLockError("LOCK_MISSING", lead + "Restore the file from a backup, or run `claude_memory.py "
                                    "init --tier` naming the tier its vectors came from, cpu or gpu.",
                                    lock=str(p), was_locked=True, tier=None)
        raise EmbedderLockError(
            "LOCK_MISSING",
            f"no embedder lock at {p}. A clean install writes it beside the database "
            "(`claude_memory.py init`); nothing embeds or searches without it.",
            lock=str(p))
    except OSError as e:
        # It is THERE and cannot be read: never read as absent, which would put a locked
        # brain on the lock-less rule and search its whole table.
        raise _with_way_out(EmbedderLockError("LOCK_MALFORMED", f"embedder lock at {p} unreadable: {e}", lock=str(p)),
                            p, brain, None)
    lock: Any = None
    try:
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
        _check_lock_table(lock, p)
    except EmbedderLockError as e:
        if e.code != "LOCK_MALFORMED":
            raise
        raise _with_way_out(e, p, brain, lock)
    lock["_path"] = str(p)
    return lock


def _with_way_out(e: EmbedderLockError, p: Path, brain: Any, lock: Any) -> EmbedderLockError:
    """A malformed lock stops `init`, `tier set` and the installer alike, so the refusal itself carries the route: move
    the file aside and write it again, under the tier the records name. What they cannot name, it says it cannot."""
    tier = (lost_lock(p.parent, brain) or {}).get("tier") or _tier_of_brain(brain if brain is not None
                                                                            else _brain_beside(p.parent))
    if not tier and isinstance(lock, dict) and isinstance(lock.get("tier"), str) and lock["tier"] in catalogue_tiers():
        tier = lock["tier"]
    aside = p.with_name(p.name + ".malformed")
    if tier:
        init = organ_command("claude_memory.py", "init", "--tier", tier)
        fix = (f"Move-Item -Force {_ps_quote(p)} {_ps_quote(aside)}; {init}" if sys.platform == "win32"
               else f"mv {shlex.quote(str(p))} {shlex.quote(str(aside))} && {init}")
        return EmbedderLockError("LOCK_MALFORMED", f"{str(e).split(': ', 1)[1]}. Restore it from a backup, or move it "
                                 f"aside and write it again: `{fix}`", **{**e.detail, "tier": tier, "retry": [fix]})
    return EmbedderLockError("LOCK_MALFORMED", f"{str(e).split(': ', 1)[1]}. Restore it from a backup, or move it aside "
                             f"and run `claude_memory.py init --tier` naming the tier its vectors came from, cpu or gpu "
                             "(nothing beside this brain records it).", **e.detail)


def _check_lock_table(lock: Dict[str, Any], p: Path) -> None:
    """The lock's (vec_table, dim) must be a pair this package builds.

    Every caller that takes a table from a lock -- the reader, the stamp, the
    loader, the doctor, the installers -- then searches or writes that table, so
    a lock naming a table nothing creates (or a known table at another width) is
    refused HERE, once, as LOCK_MALFORMED, instead of by each caller in turn.
    `write_lock` only ever writes a catalogue tier's own pair, so a lock that
    fails this was not written by this package. This is a fact about the lock's
    shape, not a policy about its weights: whether the lock's weights_hash is a
    shipped one is still decided later, after the rows and bytes are counted.
    """
    try:
        tiers = catalogue_tiers()
    except EmbedderLockError:
        raise                                    # the catalogue's own typed refusal, as is
    except Exception as e:                       # an unreadable catalogue is not a table check
        raise EmbedderLockError("LOCK_MALFORMED",
                                f"embedder lock at {p}: the package catalogue could not be read "
                                f"to check the lock's table ({e})", lock=str(p))
    table, dim = str(lock["vec_table"]), int(lock["dim"])
    pairs = {(str(e["vec_table"]), int(e["dim"])) for e in tiers.values()}
    own = tiers.get(lock["tier"])
    if own is not None:
        pairs = {(str(own["vec_table"]), int(own["dim"]))}
    if (table, dim) not in pairs:
        raise EmbedderLockError(
            "LOCK_MALFORMED",
            f"embedder lock at {p} names vec_table {table!r} at {dim} wide; this package builds "
            + (f"tier {lock['tier']!r} as {sorted(pairs)[0][0]!r} at {sorted(pairs)[0][1]}"
               if own is not None else f"only {sorted(pairs)}")
            + ". A table nothing creates is not one any reader may search.",
            lock=str(p), vec_table=table, dim=dim, tier=lock["tier"])


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
               locked_by: str = "claude_memory.init", replace: bool = False,
               brain: Union[None, str, Path, sqlite3.Connection] = None) -> Dict[str, Any]:
    """Write `<data-dir>/embedder.lock.json` naming exactly ONE catalogue tier.

    temp -> fsync -> rename, so a crash leaves either the old lock or the new one
    and never a torn file; a receipt line is appended to the history file on every
    write. An existing lock is NEVER silently replaced (an init re-run must not
    re-point a brain); `replace=True` is used by `tier set` (organs/tier_set.py)
    alone, and only after every memory is embedded under the new tier.

    `brain` is the database the lock serves: it takes the row `lost_lock` reads, so a lock
    whose file and history are both deleted is still known to have existed. A connection
    takes it in the caller's transaction. By default, the brain of the usual name beside the lock.
    """
    d = Path(data_dir)
    home = brain if brain is not None else _brain_beside(d)
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
                    "to make impossible; `voidloom tier set <tier>` is the path that re-embeds.",
                    lock=str(target), existing_tier=previous["tier"], requested_tier=tier)
            mark_brain(home, tier)              # the row, for a brain locked before it existed
            return previous                     # same tier: idempotent, no file written
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
    append_receipt(d, receipt)
    mark_brain(home, tier)
    lock["_path"] = str(target)
    return lock


def _brain_of(target: Union[None, str, Path, sqlite3.Connection]) -> Union[None, str, Path, sqlite3.Connection]:
    """The database a `resolve` target names; a data directory names none, and the usual name beside it is read."""
    if isinstance(target, sqlite3.Connection):
        return target
    if target is None:
        return None
    p = Path(target)
    return p if p.is_file() or p.suffix in (".db", ".sqlite", ".sqlite3") else None


def resolve(target: Union[None, str, Path, sqlite3.Connection] = None) -> Dict[str, Any]:
    """The validated install lock for a data dir / db path / connection.

    This is what `embed_resolver.resolve` derives (table, dim) from. It reads the
    lock and NOTHING in the database: `metadata.active_embedding_*` is the sibling
    trap and on a clean install the key no longer exists. Catalogue membership is
    deliberately NOT checked here (module header: facts before policy).
    """
    return read_lock(lock_path_for(target), brain=_brain_of(target))


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

_HUB_FLAGS = (("huggingface_hub.constants", "HF_HUB_OFFLINE"), ("transformers.utils.hub", "_is_offline_mode"))
_HUB_ENV = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
_hub_closures = 0


@contextlib.contextmanager
def hub_closed() -> Iterator[None]:
    """No hub traffic inside this block, whatever the environment says. A read loads from disk or
    fails here, and the read degrades. Both libraries read their offline flag once, at import, so
    a process that imported them already needs the flag set as well as the variables; one that has
    not yet imported them takes it from the variables. Both are put back afterwards, so a later
    fetch in this process (the install's) is not left offline."""
    global _hub_closures
    was_offline = hf_offline()
    saved = {k: os.environ.get(k) for k in _HUB_ENV}
    for k in _HUB_ENV:
        os.environ[k] = "1"

    def _flags(value: bool) -> None:
        for module, attr in _HUB_FLAGS:
            mod = sys.modules.get(module)
            if mod is not None and hasattr(mod, attr):
                setattr(mod, attr, value)
    _flags(True)
    _hub_closures += 1
    try:
        yield
    finally:
        _hub_closures -= 1
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _flags(was_offline)


_fetch_scopes = 0


@contextlib.contextmanager
def fetch_allowed() -> Iterator[None]:
    """The only way a model reaches this machine after install: `tier set`, `re-embed` and model_fetch run their load in
    here. Everywhere else the loader refuses SNAPSHOT_ABSENT, so a store or a nightly job never starts a download
    nobody asked for."""
    global _fetch_scopes
    _fetch_scopes += 1
    try:
        yield
    finally:
        _fetch_scopes -= 1


def snapshot_complete(snap: Optional[Path], files: Optional[Sequence[str]]) -> bool:
    """Is every file the pin names in this snapshot? An interrupted download leaves the directory and some of its files,
    and a directory is not a model."""
    return snap is not None and all((snap / f).exists() for f in (files or ()))


def hf_offline() -> bool:
    """HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE, as huggingface_hub reads them: a
    reader under either never fetches, and this module says so TYPED
    (SNAPSHOT_ABSENT) instead of letting the hub raise its own exception."""
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        v = (os.environ.get(var) or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    return False


def fetch_options(platform: Optional[str] = None) -> Dict[str, Any]:
    """Keyword arguments for every `snapshot_download`. On Windows without Developer Mode a symlink needs a privilege the
    user lacks, and the library finds that out from one download while parallel ones, told symlinks work, fail with
    WinError 1314. One file at a time it learns first and copies instead (measured: a fresh install lost its model to it)."""
    return {"max_workers": 1} if (platform or sys.platform) == "win32" else {}


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
    if snapshot_complete(snap, allow_patterns):
        return snap
    if not allow_fetch or hf_offline():
        fix = fetch_command()
        raise EmbedderLockError(
            "SNAPSHOT_ABSENT",
            f"{'no local snapshot' if snap is None else 'an incomplete snapshot (a download was interrupted)'} for "
            f"{what} {model_id}@{revision[:12]} under {hf_hub_cache()} and "
            + ("a read does not fetch, and neither does a store: only an install, `tier set` and `re-embed` do"
               if _hub_closures else "this process is HF-offline (HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE)" if hf_offline()
               else "only an install, `tier set` and `re-embed` fetch")
            + f". Fetch it, with the network: `{fix}` -- or populate the cache yourself at exactly this revision.",
            weights_hash=weights_hash, model_id=model_id, revision=revision, what=what,
            hf_hub_cache=str(hf_hub_cache()), retry=[fix])
    from huggingface_hub import snapshot_download    # only here, only when absent; a partial snapshot is completed
    kwargs: Dict[str, Any] = {"revision": revision, **fetch_options()}
    if allow_patterns:
        kwargs["allow_patterns"] = list(allow_patterns)
    snapshot_download(model_id, **kwargs)
    snap = snapshot_dir_for(model_id, revision)
    if not snapshot_complete(snap, allow_patterns):
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"fetched {what} {model_id}@{revision[:12]} but no complete snapshot appeared under {hf_hub_cache()}",
            weights_hash=weights_hash)
    return snap


def _replace_pinned(entry: Dict[str, Any], *, what: str, catalogue: Optional[Union[str, Path]] = None,
                    pin_kind: str = "tier") -> Tuple[Path, str]:
    """Fetch again, over what is on disk, every file `entry` pins at its revision -- the model's files and, for a tier
    that pins one, its remote-code repository's -- then hash the result against the pin. The bytes there failed the
    pin's hash; a file the hub serves at a pinned revision does not change, so a second failure is not a download that
    went wrong, and it refuses. Returns what verify_snapshot_against_lock returns."""
    fix = fetch_command()
    try:
        from huggingface_hub import snapshot_download
        for pin in (entry, entry.get("code")):
            if pin:
                snapshot_download(pin["model_id"], revision=pin["revision"], allow_patterns=list(pin["files"]),
                                  force_download=True, **fetch_options())
    except Exception as e:                          # the hub unreachable, a DNS failure, a 5xx
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"the {what} {entry['model_id']}@{entry['revision'][:12]} on disk does not hash to its pin, and fetching it "
            f"again failed: {type(e).__name__}: {e}. Retry with the network: `{fix}`",
            weights_hash=entry["weights_hash"], retry=[fix])
    try:
        return verify_snapshot_against_lock(entry, allow_fetch=False, catalogue=catalogue, pin_kind=pin_kind)
    except EmbedderLockError as e:
        if e.code != "WEIGHTS_MISMATCH":
            raise
        raise EmbedderLockError(
            "WEIGHTS_MISMATCH",
            f"the {what} {entry['model_id']}@{entry['revision'][:12]} was fetched again at its pin and still does not "
            f"hash to it ({e}). The bytes the hub serves there are not the bytes the package pinned: fetch nothing "
            "more, and report it.", **e.detail)


def verify_snapshot_against_lock(lock: Dict[str, Any], *, allow_fetch: Optional[bool] = None,
                                 catalogue: Optional[Union[str, Path]] = None,
                                 pin_kind: str = "tier") -> Tuple[Path, str]:
    """Locate the snapshot the lock names -- AND the remote-code snapshot its
    `code` pin names, when it has one -- compute the checksum over both, compare.

    Returns (snapshot_dir, computed_hash) -- the value the loader then REGISTERS
    and the stamp then WRITES. Refusals, in order:
      * a snapshot absent locally and the lock's hash is not in the catalogue ->
        LOCK_NOT_SHIPPED, before any fetch (nothing outside the catalogue is
        ever downloaded on this path);
      * a snapshot absent or missing a named file, and allow_fetch -> fetched at ITS
        pinned revision (the model at lock.revision, the code at lock.code.revision);
        allow_fetch defaults to whether the caller is inside fetch_allowed();
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

    if allow_fetch is None:
        allow_fetch = _fetch_scopes > 0
    code = lock.get("code")
    model_snap = snapshot_dir_for(lock["model_id"], lock["revision"])
    code_snap = snapshot_dir_for(code["model_id"], code["revision"]) if code else None
    if not snapshot_complete(model_snap, lock.get("files")):
        model_snap = None
    if code and not snapshot_complete(code_snap, code.get("files")):
        code_snap = None
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
                           "models": [], "fetched": 0, "present_before": 0, "replaced": 0,
                           "bytes_named_total": 0, "unnamed_bytes_total": 0, "unnamed_files_total": 0}
    absent: List[Dict[str, Any]] = []
    for m in plan["needed"]:
        rec: Dict[str, Any] = {k: m[k] for k in ("what", "pin", "model_id", "revision", "pin_kind")}
        rec["files_named"] = len(m["files"])
        snap = snapshot_dir_for(m["model_id"], m["revision"], hub=hub)
        if not snapshot_complete(snap, m["files"]):
            snap = None
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
            f"`{fetch_command()}` -- or populate the cache yourself at exactly these revisions.",
            absent=absent, needed=plan["count"], hf_hub_cache=str(hub), offline=hf_offline(),
            bytes_on_disk=out["bytes_named_total"], models=out["models"], retry=[fetch_command()])
    # every present model is then HASHED against its pin (the loader's own check),
    # so "present" means "the bytes the catalogue promised", never "a directory exists"
    for m, rec in zip(plan["needed"], out["models"]):
        if m["what"] == "remote-code":
            continue                                    # hashed together with its model below
        entry = dict(lock) if m["pin_kind"] == "tier" else catalogue_reranker(m["pin"].split(".", 1)[1], catalogue)
        try:
            _snap, computed = verify_snapshot_against_lock(entry, allow_fetch=False, catalogue=catalogue,
                                                           pin_kind=m["pin_kind"])
        except EmbedderLockError as e:
            # Bytes that fail a shipped pin's hash are fetched again at that pin, over what is on disk, and hashed
            # again: this command is the fix every refusal of them prints. A pin the catalogue does not ship is never
            # fetched, and a run that may not fetch keeps the refusal.
            shipped = m["pin_kind"] != "tier" or is_shipped_hash(entry["weights_hash"], catalogue)
            if e.code != "WEIGHTS_MISMATCH" or not allow_fetch or hf_offline() or not shipped:
                raise
            _snap, computed = _replace_pinned(entry, what=m["what"], catalogue=catalogue, pin_kind=m["pin_kind"])
            rec["replaced"] = True
            out["replaced"] += 1
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
        lock = read_lock(path, brain=_brain_of(target))
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
