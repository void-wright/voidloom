#!/usr/bin/env python3
"""Bring a brain up to the schema THIS PACKAGE ships — derived, never typed.

C2 (cycle 1, 2026-09-15).  Every gate that decided "this brain is complete"
asserted a list somebody had typed by hand:

    install.sh:506            ("memories", "memories_fts")          -- 2 names
    install/self_check.py     memories, memories_fts, memory_tags   -- 3 names
    install/install.ps1:214   memories_fts                          -- 1 name
    claude_memory.py cmd_init 7 hand-named migrations, each gated on a
                              Python module import

while `migrations/` shipped 29 files.  Measured on a fresh `--init-blank`
install: the brain carried 24 objects against a reference of 66, and no
`schema_migrations` table existed to say so.  Every gate passed, and the
installer printed "the install is alive".

THE DERIVATION, and it is the same inversion C1 made for the undo domain: the
required set is what the PACKAGE'S OWN MIGRATIONS BUILD, obtained by BUILDING
THEM — not by parsing their SQL for CREATE statements.  A regex is a second,
worse declaration of the same fact; when tried it captured the token `IF` and a
migration's internal `cross_tier_bridges_new` rebuild table.  Executing the
package against a throwaway file cannot drift from the package, because it IS
the package.

AUTHORITY vs RECORD.  `sqlite_master` and SQLite's own "already exists" errors
are the AUTHORITY -- they are the world answering.  The `schema_migrations`
table this module writes is a RECORD, for humans reading the brain later, and
nothing here ever decides from it.  (KAEL-EM, Council #13: everything that
verified by re-reading the world held; everything that verified by consulting
its own record failed.)

STDLIB ONLY.  No imports beyond the standard library, so it runs on the
interpreter the Tier-0 promise names.  `sqlite_vec` is loaded when present and
its absence is reported as UNMEASURABLE -- never as a failure -- because the
two vec0 migrations (013, 031) genuinely cannot build without it.

    python3 schema_migrator.py audit  [--db PATH] [--json]
    python3 schema_migrator.py apply  [--db PATH] [--json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# SQLite's own signals that an object is already there.  Tolerating exactly
# these -- and nothing else -- is what makes the whole set idempotent without a
# ledger being load-bearing: the DATABASE says it is already applied.
BENIGN = ("already exists", "duplicate column name")

# A migration that needs the vec0 virtual-table module cannot build where that
# module is absent.  That is a tier boundary, not a defect, and it is reported.
UNMEASURABLE = ("no such module",)


def migrations_dir(explicit: Optional[str] = None) -> pathlib.Path:
    if explicit:
        return pathlib.Path(explicit)
    return pathlib.Path(__file__).resolve().parent.parent / "migrations"


def iter_statements(sql: str):
    """Yield complete SQL statements from a script.

    A comment line belongs to the statement it introduces.  The first version of
    this dropped the buffer whenever the accumulated text began with '--', which
    silently discarded every statement carrying a comment -- in this package,
    most of them: schema.sql parsed to 1 statement instead of 18.
    """
    def has_sql(text: str) -> bool:
        return any(l.strip() and not l.strip().startswith("--")
                   for l in text.splitlines())

    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt, buf = buf, ""
            if has_sql(stmt):
                yield stmt
    if buf.strip() and has_sql(buf):
        yield buf


def connect(db: str) -> sqlite3.Connection:
    """Autocommit.  Several migrations carry their own BEGIN/COMMIT, and
    python's implicit transaction turns those into 'cannot start a transaction
    within a transaction' -- 022 and 024 were lost that way before this."""
    conn = sqlite3.connect(db, isolation_level=None, timeout=60.0)
    try:
        conn.enable_load_extension(True)
        import sqlite_vec  # type: ignore
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except Exception:
        pass
    return conn


def has_vec0(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE temp._vec0_probe USING vec0(e float[2])")
        conn.execute("DROP TABLE temp._vec0_probe")
        return True
    except Exception:
        return False


_TXN_CONTROL = ("begin", "commit", "end", "rollback")


def _is_txn_control(stmt: str) -> bool:
    """`BEGIN;` and `BEGIN TRANSACTION;` are the same statement.

    The first version took `t.split()[0]`, which on a bare `BEGIN;` is the token
    `begin;` -- semicolon attached -- and matched nothing. `BEGIN TRANSACTION;`
    split cleanly and was caught, so migrations 022 and 023 behaved while 024
    and 027 leaked their transactions into the next file and read as failures.
    """
    for line in stmt.splitlines():
        t = line.strip().lower()
        if not t or t.startswith("--"):
            continue
        head = t.split()[0].strip(";").strip()
        return head in _TXN_CONTROL
    return False


def _is_pragma(stmt: str) -> bool:
    for line in stmt.splitlines():
        t = line.strip().lower()
        if not t or t.startswith("--"):
            continue
        return t.startswith("pragma")
    return False


def _run_file(conn: sqlite3.Connection, path: pathlib.Path) -> Dict[str, Any]:
    """Apply ONE migration ATOMICALLY: all of it, or none of it.

    THE DEFECT THIS EXISTS TO PREVENT, found by C2's own harness against this
    module's first draft, and it destroyed data.  `migration_022` rebuilds
    `cross_tier_bridges`: CREATE _new, copy, DROP the original, RENAME.  SQLite
    revalidates EVERY view on an `ALTER TABLE ... RENAME`, so one dangling view
    anywhere in the brain aborts the rename -- and because statements were being
    executed one at a time in autocommit, the DROP had already committed.
    Measured: `cross_tier_bridges` gone, `cross_tier_bridges_new` orphaned, a
    table that existed before the run destroyed by the run.

    The file's own BEGIN/COMMIT could not save it: those are just more statements
    to a statement-at-a-time runner.  So transaction control is taken AWAY from
    the file and exercised here, which is strictly stronger than what any file
    declares -- every migration becomes all-or-nothing whether it asked to be or
    not.  PRAGMAs run outside, because several cannot take effect inside a
    transaction.
    """
    applied = already = 0
    unmeasurable: List[str] = []
    errors: List[str] = []
    stmts = [x for x in iter_statements(path.read_text(encoding="utf-8"))
             if not _is_txn_control(x)]
    for stmt in [x for x in stmts if _is_pragma(x)]:
        try:
            conn.execute(stmt)
        except sqlite3.Error:
            pass
    body = [x for x in stmts if not _is_pragma(x)]
    # Start from a CLEAN transaction state. Swallowing a failed BEGIN leaked the
    # previous file's open transaction into this one and produced a cascade of
    # "cannot start a transaction within a transaction" / "cannot commit - no
    # transaction is active" -- errors about bookkeeping, not about the schema,
    # which then read as migration failures and pulled the reference set from
    # 66 objects down to 62.
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
    opened = False
    try:
        conn.execute("BEGIN")
        opened = True
    except sqlite3.Error as e:
        errors.append(f"could not open a transaction: {e}")
    for stmt in body:
        try:
            conn.execute(stmt)
            applied += 1
        except sqlite3.OperationalError as e:
            msg = str(e)
            if any(b in msg for b in BENIGN):
                already += 1
            elif any(u in msg for u in UNMEASURABLE):
                unmeasurable.append(msg)
            else:
                errors.append(msg)
                break            # nothing further may build on a broken step
        except sqlite3.Error as e:
            errors.append(f"{type(e).__name__}: {e}")
            break
    # Close only what is actually open. A few statements commit implicitly, so
    # `in_transaction` is the authority here rather than the fact that we asked
    # for a BEGIN -- and when it says the transaction is already gone while we
    # are holding an error, the file was NOT atomic and that is reported rather
    # than assumed away.
    atomic = opened
    if conn.in_transaction:
        try:
            conn.execute("ROLLBACK" if errors else "COMMIT")
        except sqlite3.Error as e:
            errors.append(f"transaction close failed: {e}")
            atomic = False
    elif opened and errors:
        atomic = False
    if errors:
        applied = already = 0    # nothing was kept, so nothing is reported kept
    return {"file": path.name, "applied": applied, "already": already,
            "unmeasurable": unmeasurable, "errors": errors, "atomic": atomic}


def _migration_files(mdir: pathlib.Path) -> List[pathlib.Path]:
    return sorted(mdir.glob("migration_*.sql"))


def build_reference(mdir: pathlib.Path, dest: Optional[str] = None
                    ) -> Tuple[Set[str], List[str], List[Dict[str, Any]]]:
    """Build a throwaway brain from schema.sql + every migration and return the
    objects it contains.  THIS is the required set: the package, executed.

    It is a FLOOR, never a ceiling.  A real brain legitimately carries more --
    measured: the vec0 tier table `memory_embeddings` and its four shadow
    tables, which `cmd_init` creates from the RESOLVED embedding tier rather
    than from any migration file, plus this module's own `schema_migrations`
    record.  `audit` therefore reports what is MISSING and never what is extra.
    """
    # ":memory:" by default -- measured at 66 objects in 0.12 s, identical to the
    # on-disk build, and it litters nothing. The first draft wrote a temp .db
    # into the BRAIN DIRECTORY, which survives a crash and leaves a stray
    # database beside the real one; a reference that can be mistaken for a brain
    # is a bad reference. `dest` stays available for debugging the build.
    tmp = dest or ":memory:"
    if dest:
        pathlib.Path(tmp).unlink(missing_ok=True)
    conn = connect(tmp)
    unmeasurable: List[str] = []
    # CRUCIBLE-3 D2-F1: `r["errors"]` was COLLECTED BY `_run_file` AND DISCARDED HERE.
    # When a migration errors during the reference build, its objects never enter
    # `ref` -- so they can never be reported missing, and `missing = ref - have`
    # is computed against a denominator that shrank silently. Measured: a brain
    # missing 3 required tables audited `reference_objects: 2` (not 5),
    # `missing: 0`, `complete: true`, `status: ok`, rc 0. The numerator is where
    # the reader looks; the denominator is what moved.
    files_with_errors: List[Dict[str, Any]] = []
    try:
        base = mdir / "schema.sql"
        if base.exists():
            r = _run_file(conn, base)
            unmeasurable += [f"{base.name}: {m}" for m in r["unmeasurable"]]
            if r.get("errors"):
                files_with_errors.append({"file": base.name, "errors": list(r["errors"])})
        for f in _migration_files(mdir):
            r = _run_file(conn, f)
            unmeasurable += [f"{f.name}: {m}" for m in r["unmeasurable"]]
            if r.get("errors"):
                files_with_errors.append({"file": f.name, "errors": list(r["errors"])})
        objs = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    finally:
        conn.close()
    return objs, unmeasurable, files_with_errors


def signature(conn: sqlite3.Connection) -> Dict[str, Set[str]]:
    """The world as this brain actually is: every schema object, and for tables
    and views their column names. This is the evidence an "is it applied?"
    question gets answered from."""
    sig: Dict[str, Set[str]] = {}
    for name, typ in conn.execute("SELECT name, type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"):
        cols: Set[str] = set()
        if typ in ("table", "view"):
            try:
                cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
            except sqlite3.Error:
                cols = set()
        sig[name] = cols
    return sig


def migration_deltas(mdir: pathlib.Path) -> Tuple[Dict[str, Tuple[Set[str], Set[Tuple[str, str]]]], Set[str]]:
    """What each migration INTRODUCES, derived by applying them one at a time.

    WHY THIS EXISTS, and it is the most expensive thing learned in C2.  The
    first runner re-applied every migration on every run.  Four of the 29 are
    DESTRUCTIVE on re-application -- 001, 022, 023 and 030 all DROP or DELETE --
    and `migration_022` is a deliberate purge: its own header reads "PURGES the
    293 legacy bridges ... We do NOT copy old rows -- fresh discovery
    repopulates", and then "Idempotency: guarded by the schema_version check in
    the runner; safe to apply once."  THERE HAS NEVER BEEN SUCH A RUNNER.
    Measured against a COPY of the live brain: `cross_tier_bridges` went from
    26 rows to 0.  Those are the twin-brain cross-tier links.  Re-running
    migrations unconditionally does not merely fail to be idempotent; it
    silently destroys data while reporting `missing: 0` and rc 0.

    Tolerating SQLite's "already exists" errors cannot catch this, because 022
    does not error -- it succeeds, and succeeding is the damage.  So "already
    applied" has to be decided from the WORLD, not from an error code: apply the
    migrations one at a time to a throwaway reference, and record which objects
    and which COLUMNS each one adds.  A migration whose additions are all
    already present in the target brain has been applied, and must not run
    again.  Columns matter as much as objects: 022 creates no lasting new object
    (its `_new` table is renamed away) and is recognised only by the
    `bridge_scope` column it adds.
    """
    conn = connect(":memory:")
    deltas: Dict[str, Tuple[Set[str], Set[Tuple[str, str]]]] = {}
    try:
        base = mdir / "schema.sql"
        if base.exists():
            _run_file(conn, base)
        prev = signature(conn)
        for f in _migration_files(mdir):
            _run_file(conn, f)
            cur = signature(conn)
            new_objects = set(cur) - set(prev)
            new_cols = {(t, c) for t in (set(cur) & set(prev)) for c in (cur[t] - prev[t])}
            deltas[f.name] = (new_objects, new_cols)
            prev = cur
        final = set(prev)
    finally:
        conn.close()
    return deltas, final


def already_applied(target: Dict[str, Set[str]],
                    delta: Tuple[Set[str], Set[Tuple[str, str]]]) -> Optional[bool]:
    """True / False / None (the migration adds nothing detectable)."""
    objs, cols = delta
    if not objs and not cols:
        return None
    if any(o not in target for o in objs):
        return False
    if any(c not in target.get(t, set()) for t, c in cols):
        return False
    return True


def brain_objects(db: str) -> Set[str]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    finally:
        conn.close()


def audit(db: str, mdir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Compare a brain against the package, by re-reading both."""
    mdir = mdir or migrations_dir()
    ref, unmeasurable, files_with_errors = build_reference(mdir)
    _probe = connect(db)
    try:
        vec = has_vec0(_probe)
    finally:
        _probe.close()
    have = brain_objects(db)
    missing = sorted(ref - have)
    return {
        "database": db,
        "migrations_dir": str(mdir),
        "migrations_on_disk": len(_migration_files(mdir)),
        "reference_objects": len(ref),
        "brain_objects": len(have),
        "missing": missing,
        "missing_count": len(missing),
        # D2-F1: `complete` is a claim about the BRAIN, and it may only be made
        # when the REFERENCE it is measured against was itself built completely.
        # A reference whose migrations errored has a denominator nobody can trust,
        # so `missing: 0` means "nothing was compared", not "nothing is missing".
        "complete": (not missing) and not files_with_errors,
        "reference_complete": not files_with_errors,
        "files_with_errors": files_with_errors,
        # UNMEASURABLE is first-class: "this tier cannot build these" is not
        # "these are fine".  A caller that treats it as a pass is lying.
        "unmeasurable": unmeasurable,
        "vec0_available": vec,
        "note": ("the required set is the package's own migrations, EXECUTED; "
                 "sqlite_master is the authority and schema_migrations is only a record"),
    }


def apply(db: str, mdir: Optional[pathlib.Path] = None) -> Dict[str, Any]:
    """Apply every migration the package ships, idempotently."""
    mdir = mdir or migrations_dir()
    t0 = time.time()
    conn = connect(db)
    vec = has_vec0(conn)
    files: List[Dict[str, Any]] = []
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name TEXT PRIMARY KEY, applied_at TEXT, sha256 TEXT, statements INTEGER)")

        # C3 -- BOOTSTRAP, so Tier 0 can build a brain at all.
        #
        # `apply` used to run only migration_*.sql, which assumes something else
        # already laid the base tables. The only thing that does is
        # `claude_memory.py init` -- and that hard-requires sqlite_vec, so on the
        # interpreter the Tier-0 promise names it cannot run. Measured: on
        # /usr/bin/python3, `install.sh --init-blank --tier0-only` died FATAL at
        # init, and a brain built by `apply` alone had no `memories_fts` at all.
        # schema.sql is pure stdlib SQL, so running it when the base table is
        # absent is what makes "keyword recall works on ANY python3" true rather
        # than advertised.
        base = mdir / "schema.sql"
        bootstrapped = False
        if base.exists() and not conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'").fetchone():
            _b = _run_file(conn, base)
            bootstrapped = not _b["errors"]
            if _b["errors"]:
                files.append(_b)

        # ITERATE TO A FIXPOINT rather than encode a dependency order.
        #
        # Filename order is not dependency order. Measured: `migration_022`
        # renames a table, SQLite revalidates every view on a rename, and the
        # view `v_decision_unit_state` is created by `migration_030` -- so on a
        # brain where that view exists but its table does not, 022 fails until
        # 030 has run. Hand-writing the true order would be one more typed list
        # of exactly the kind this class exists to delete, and it would be wrong
        # the first time a migration is added.
        #
        # So: apply everything, then re-apply whatever failed, for as long as
        # the failure set keeps SHRINKING. A pass that fixes nothing ends it,
        # and what is still failing is reported by name. Each attempt is
        # all-or-nothing (see _run_file), so a retried file cannot accumulate
        # half-applied state across passes.
        deltas, _final = migration_deltas(mdir)
        pending = _migration_files(mdir)
        results: Dict[str, Dict[str, Any]] = {}
        skipped: List[str] = []
        passes = 0
        while pending:
            passes += 1
            failed_now: List[pathlib.Path] = []
            target = signature(conn)
            for f in pending:
                # ALREADY APPLIED IS DECIDED FROM THE BRAIN, not from a ledger
                # and not from an error code. Four migrations destroy data when
                # re-run; 022 wiped 26 live cross-brain bridges in the measured
                # case, and it did so by SUCCEEDING.
                if already_applied(target, deltas.get(f.name, (set(), set()))) is True:
                    results[f.name] = {"file": f.name, "applied": 0, "already": 0,
                                       "unmeasurable": [], "errors": [],
                                       "skipped_already_applied": True, "atomic": True}
                    skipped.append(f.name)
                    continue
                r = _run_file(conn, f)
                results[f.name] = r
                if r["errors"]:
                    failed_now.append(f)
                else:
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_migrations "
                        "(name, applied_at, sha256, statements) VALUES (?,?,?,?)",
                        (f.name,
                         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         hashlib.sha256(f.read_bytes()).hexdigest(),
                         r["applied"] + r["already"]))
            if len(failed_now) >= len(pending):
                break                      # no progress; retrying cannot help
            pending = failed_now
        files = [results[f.name] for f in _migration_files(mdir)]
    finally:
        conn.close()
    errs = [(r["file"], r["errors"]) for r in files if r["errors"]]
    unm = [f"{r['file']}: {m}" for r in files for m in r["unmeasurable"]]
    out = audit(db, mdir)
    out.update({
        "action": "apply",
        "vec0_available": vec,
        "files_seen": len(files),
        "passes": passes,
        "bootstrapped_base_schema": bootstrapped,
        "skipped_already_applied": skipped,
        # These count STATEMENTS EXECUTED, not objects created: `CREATE TABLE
        # IF NOT EXISTS` executes as a silent no-op, so a clean re-run still
        # reports a large `statements_executed`.  Naming it "applied" made a
        # second run look like it had done the work again.
        "statements_executed": sum(r["applied"] for r in files),
        "statements_refused_as_present": sum(r["already"] for r in files),
        "files_with_errors": [{"file": f, "errors": e} for f, e in errs],
        "unmeasurable": unm or out["unmeasurable"],
        "ok": not errs,
        "time_ms": int((time.time() - t0) * 1000),
    })
    return out


def _resolve_db(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    env = os.environ.get("WIZARD_LIBRARY_DB")
    if env:
        return env
    return str(pathlib.Path(__file__).resolve().parent.parent / "claude_memory.db")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("apply", "audit"))
    ap.add_argument("--db", default=None)
    ap.add_argument("--migrations", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    db = _resolve_db(a.db)
    mdir = migrations_dir(a.migrations)
    if not os.path.exists(db):
        payload = {"status": "error", "error": "no_brain", "database": db}
        print(json.dumps(payload, indent=1) if a.json else f"FATAL: no brain at {db}",
              file=sys.stderr)
        return 2
    res = apply(db, mdir) if a.action == "apply" else audit(db, mdir)
    if a.json:
        print(json.dumps({"status": "ok" if res.get("complete") or res.get("ok") else "incomplete",
                          "action": a.action, "data": res}, indent=1))
    else:
        print(f"  migrations on disk : {res['migrations_on_disk']}")
        print(f"  reference objects  : {res['reference_objects']}")
        print(f"  brain objects      : {res['brain_objects']}")
        if a.action == "apply":
            print(f"  statements executed: {res['statements_executed']}"
                  f"  (refused as already present: {res['statements_refused_as_present']})")
            _sk = res.get("skipped_already_applied") or []
            print(f"  already applied    : {len(_sk)}"
                  + (f"  -> {', '.join(x.replace('migration_', '').replace('.sql', '') for x in _sk[:6])}"
                     + (" ..." if len(_sk) > 6 else "") if _sk else ""))
        print(f"  missing            : {res['missing_count']}"
              + (f"  -> {', '.join(res['missing'][:8])}" if res["missing"] else ""))
        for u in res.get("unmeasurable") or []:
            print(f"  UNMEASURABLE       : {u}")
        for fe in res.get("files_with_errors") or []:
            print(f"  ERROR              : {fe['file']}: {fe['errors'][:1]}")
    if res.get("files_with_errors"):
        return 1
    return 0 if res.get("complete") else 4     # 4 = INCOMPLETE, the shipped taxonomy


if __name__ == "__main__":
    sys.exit(main())
