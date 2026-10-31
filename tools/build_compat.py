#!/usr/bin/env python3
"""build_compat.py -- derive COMPAT.json (`voidloom.compat/1`) from this tree, or check it.

COMPAT.json tells a program that installs this package what the package produces and accepts, so it can refuse
a mismatch before it writes anything: the package version, the migrations it ships and the schema a fresh brain
ends up with, the Python range, the CLI surface, and per install profile the scheduled units. Every value is
DERIVED here from the tree (or from a brain this script builds); none is typed.

    python3 tools/build_compat.py --emit              # write COMPAT.json (builds a fresh brain: needs sqlite-vec)
    python3 tools/build_compat.py --check             # every field but schema_sha256; exit 1 on any drift
    python3 tools/build_compat.py --check --schema    # also rebuild the fresh brain and compare schema_sha256
    --schema-db PATH                                  # take schema_sha256 from an existing fresh brain instead
    --compat PATH                                     # with --check: check this file, not COMPAT.json

Fields and their sources:
  version                         voidloom/__init__.py __version__
  schema_produced.migrations      migrations/migration_*.sql, sorted file names
  schema_produced.schema_sha256   sha256 of the normalised schema dump of a FRESH brain made the way an installer
                                  makes one: `WIZARD_EMBED_MODEL=nomic organs/claude_memory.py init`, then
                                  `organs/claude_memory_lite.py load-seed --file seed/seed_pack.json`. The
                                  installer adds `--embed`; vectors do not change the schema (measured: init alone,
                                  init + seed, init + seed --embed and init + migrator + seed --embed gave one hash,
                                  203 objects), so no model is needed to derive it.
                                  The dump: sqlite_master rows not named sqlite_*, as [type, name, tbl_name, sql]
                                  with whitespace runs collapsed and IF NOT EXISTS removed, sorted by type then name,
                                  plus PRAGMA table_info per table (null where a stdlib connection cannot read it);
                                  hashed as compact, key-sorted JSON.
  oldest_migratable_schema        [] : a brain carrying only migrations/schema.sql, run through
                                  organs/schema_migrator.py and then `init`, reaches schema_sha256 exactly
                                  (voidloom/tests/test_compat_and_version_json.py measures it every run).
  install_verb_contract           the installers' option surface, as a version: `install-verb/1:` + the first 16 hex
                                  of sha256 over install.sh's argument-loop options (each with whether it takes a
                                  value) and install/install.ps1's param() names and types. It moves exactly when
                                  an installer gains, loses or changes an option. It names no commit (a file cannot
                                  hold the hash of the commit it sits in; the consumer's pin names the commit) and
                                  describes no lay (the consumer measures what its own install lays).
  cli_surface_version             voidloom.cli.cli_surface_version() (`voidloom --version --json` prints the same)
  python_range                    install.sh's own floor (py_ge_310); the package declares no ceiling
  profiles.<p>.unit_set           the unit names of install/cron_roster.json (the same roster for every profile)
Not emitted: profiles.<p>.write_set_hash and model_cache_layout. They describe the tree an installer lays in ITS
data directory and its model cache, which the installer measures; this package cannot derive them from itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPAT_REL = "COMPAT.json"
SCHEMA = "voidloom.compat/1"
PROFILES = ("personal", "workbench")
_WS = re.compile(r"\s+")
_INE = re.compile(r"\bIF\s+NOT\s+EXISTS\b\s*", re.IGNORECASE)


def version(root: Path = ROOT) -> str:
    m = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]",
                  (root / "voidloom" / "__init__.py").read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit("no __version__ in voidloom/__init__.py")
    return m.group(1)


def migrations(root: Path = ROOT) -> list:
    return sorted(p.name for p in (root / "migrations").glob("migration_*.sql"))


def unit_set(root: Path = ROOT) -> list:
    doc = json.loads((root / "install" / "cron_roster.json").read_text(encoding="utf-8"))
    return sorted(doc["units"])


def python_range(root: Path = ROOT) -> str:
    src = (root / "install.sh").read_text(encoding="utf-8")
    m = re.search(r"^py_ge_3(\d+)\(\).*sys\.version_info\[:2\] >= \(3,(\d+)\)", src, re.M)
    if not m or m.group(1) != m.group(2):
        raise SystemExit("install.sh's python floor (py_ge_3NN) is not where this builder reads it")
    return f">=3.{m.group(2)}"


_SH_LOOP = re.compile(r'^for arg in "\$@"; do\n(.*?)^done$', re.S | re.M)
_SH_OPT = re.compile(r"^\s+((?:--?[A-Za-z0-9][A-Za-z0-9-]*)(?:\|--?[A-Za-z0-9][A-Za-z0-9-]*)*)\)(.*)$", re.M)
_PS_PARAM = re.compile(r"^param\(\n(.*?)^\)$", re.S | re.M)
_PS_ARG = re.compile(r"\[(switch|string|int)\]\$([A-Za-z][A-Za-z0-9]*)")


def install_surface(root: Path = ROOT) -> dict:
    """The options a caller can pass the installers, read from their sources. Fails closed: a loop or a param()
    block this reader cannot find, or one with no options, stops the build rather than hashing an empty surface."""
    sh = (root / "install.sh").read_text(encoding="utf-8")
    loops = _SH_LOOP.findall(sh)
    if len(loops) != 1:
        raise SystemExit(f"install.sh: expected one `for arg in \"$@\"` loop, found {len(loops)}")
    sh_opts = sorted([label, "__EXPECT__" in rest] for label, rest in _SH_OPT.findall(loops[0]))
    ps = (root / "install" / "install.ps1").read_text(encoding="utf-8")
    blocks = _PS_PARAM.findall(ps)
    if len(blocks) != 1:
        raise SystemExit(f"install/install.ps1: expected one top-level param() block, found {len(blocks)}")
    ps_opts = sorted([name, kind] for kind, name in _PS_ARG.findall(blocks[0]))
    if not sh_opts or not ps_opts:
        raise SystemExit("an installer's option surface read empty; the reader no longer matches its source")
    return {"install.sh": sh_opts, "install/install.ps1": ps_opts}


def install_verb_contract(root: Path = ROOT) -> str:
    blob = json.dumps(install_surface(root), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "install-verb/1:" + hashlib.sha256(blob).hexdigest()[:16]


def cli_surface_version(root: Path = ROOT) -> str:
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); "
                        "from voidloom import cli; print(cli.cli_surface_version())", str(root)],
                       capture_output=True, text=True, timeout=120, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if r.returncode != 0:
        raise SystemExit(f"cli_surface_version unreadable: {r.stderr[-400:]}")
    return r.stdout.strip().splitlines()[-1]


def _normalise(sql):
    if sql is None:
        return None
    return _WS.sub(" ", _INE.sub("", _WS.sub(" ", sql).strip())).strip()


def schema_sha256(db: Path) -> str:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
        objs = sorted([[t, n, tb, _normalise(s)] for t, n, tb, s in rows if not str(n).startswith("sqlite_")],
                      key=lambda r: (r[0], r[1]))
        info = {}
        for t, n, _tb, _s in objs:
            if t != "table":
                continue
            try:
                info[n] = [list(r) for r in conn.execute('PRAGMA table_info("%s")' % n.replace('"', '""'))]
            except sqlite3.Error:
                info[n] = None
    finally:
        conn.close()
    dump = {"objects": objs, "table_info": info}
    return hashlib.sha256(json.dumps(dump, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def fresh_brain(root: Path, workdir: Path, steps=("init", "seed")) -> Path:
    """A brain made the way an installer makes one: the package laid INTO the brain directory (claude_memory.py
    resolves its database beside its own organs/ and refuses a WIZARD_LIBRARY_DB that disagrees), then `init`
    under the cpu tier, then the seed pack loaded with vectors. HOME is `workdir`, so nothing outside it is read."""
    import shutil
    brain = workdir / "brain"
    shutil.copytree(root, brain, ignore=shutil.ignore_patterns(".git", "tests", "__pycache__", "*.db", "*.db-*",
                                                               "embedder.lock*.json", ".pytest_boxes"))
    db = brain / "claude_memory.db"
    # the model files stay where this host keeps them: HOME moves into `workdir`, the HF cache does not
    hub = os.environ.get("HF_HUB_CACHE") or str(Path(os.environ.get("HF_HOME") or
                                                  Path.home() / ".cache" / "huggingface") / "hub")
    env = {**os.environ, "HF_HUB_CACHE": hub, "HOME": str(workdir), "WIZARD_LIBRARY_DB": str(db), "CLAUDE_MEMORY_DB": str(db),
           "WIZARD_EMBED_MODEL": "nomic", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "PYTHONDONTWRITEBYTECODE": "1"}
    argv = {"init": [sys.executable, "organs/claude_memory.py", "init"],
            "migrate": [sys.executable, "organs/schema_migrator.py", "apply", "--db", str(db), "--json"],
            "seed": [sys.executable, "organs/claude_memory_lite.py", "load-seed", "--file", "seed/seed_pack.json"],
            "seed-embed": [sys.executable, "organs/claude_memory_lite.py", "load-seed", "--file",
                           "seed/seed_pack.json", "--embed"]}
    for step in steps:
        r = subprocess.run(argv[step], capture_output=True, text=True, env=env, cwd=str(brain), timeout=1800)
        if r.returncode != 0:
            raise SystemExit(f"fresh brain: `{step}` rc {r.returncode}: {(r.stdout + r.stderr)[-800:]}")
    return db


def derive(root: Path = ROOT, schema_db: Path = None, build_schema: bool = True) -> dict:
    doc = {
        "schema": SCHEMA,
        "version": version(root),
        "schema_produced": {"migrations": migrations(root), "schema_sha256": None},
        "oldest_migratable_schema": {"migrations": []},
        "install_verb_contract": install_verb_contract(root),
        "cli_surface_version": cli_surface_version(root),
        "python_range": python_range(root),
        "profiles": {p: {"unit_set": unit_set(root)} for p in PROFILES},
    }
    if schema_db is not None:
        doc["schema_produced"]["schema_sha256"] = schema_sha256(Path(schema_db))
    elif build_schema:
        with tempfile.TemporaryDirectory(prefix="vl_compat_") as td:
            doc["schema_produced"]["schema_sha256"] = schema_sha256(fresh_brain(root, Path(td)))
    return doc


def _dump(doc: dict) -> str:
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--emit", action="store_true")
    g.add_argument("--check", action="store_true")
    ap.add_argument("--schema", action="store_true", help="with --check: also rebuild a fresh brain")
    ap.add_argument("--schema-db", type=Path, default=None)
    ap.add_argument("--compat", type=Path, default=None, help="with --check: the file to check (default COMPAT.json)")
    a = ap.parse_args(argv)
    path = a.compat if (a.check and a.compat) else ROOT / COMPAT_REL
    if a.emit:
        path.write_text(_dump(derive(schema_db=a.schema_db)), encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
        return 0
    if not path.is_file():
        print(f"DRIFT: {path} is absent; run tools/build_compat.py --emit")
        return 1
    shipped = json.loads(path.read_text(encoding="utf-8"))
    want = derive(schema_db=a.schema_db, build_schema=a.schema or a.schema_db is not None)
    if want["schema_produced"]["schema_sha256"] is None:
        want["schema_produced"]["schema_sha256"] = (shipped.get("schema_produced") or {}).get("schema_sha256")
    drift = [k for k in sorted(set(want) | set(shipped)) if want.get(k) != shipped.get(k)]
    if drift:
        print("DRIFT: " + ", ".join(drift))
        return 1
    print(f"CLEAN: {COMPAT_REL} equals its derivation"
          + ("" if (a.schema or a.schema_db) else " (schema_sha256 not rebuilt; --schema rebuilds it)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
