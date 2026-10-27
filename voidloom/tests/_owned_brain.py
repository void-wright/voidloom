# -*- coding: utf-8 -*-
"""A default brain THE TEST OWNS, so "the verb did not write it" is decidable.

CRUCIBLE-3 seat D8-F8. Three tests asserted the right invariant — *"store must
write to the temp copy, never the live DB"* — over the wrong file:

    live_before = md5(Path(LIVE_DB).read_bytes())
    ... exercise the verb against a TEMP COPY ...
    live_after  = md5(Path(LIVE_DB).read_bytes())
    assertEqual(live_before, live_after)

`LIVE_DB` is the operator's real brain. **The product's own cron organs and any
concurrent session write to it**; the seat watched it change twice while running
read-only commands. So the assertion is a race: it passes when nothing else
happened to run, and a red says "a cron fired", not "the verb misbehaved". The
seat filed it Uncertain precisely because it could not make the failure fire —
which is the honest reading of a check whose signal is somebody else's traffic.

The invariant is worth keeping and is stated exactly by this module instead:
**a verb given an explicit `--db` must write that database and no other, including
the one it would have resolved by default.** Point the resolver at a copy nothing
else on the machine knows about, run the verb against a different temp file, and
the before/after hash is now a fact about the verb.

Strictly stronger than what it replaces, and deterministic:
  * no third party can write the file, so a red is always the product;
  * it exercises the DEFAULT RESOLUTION path too — if `--db` were ignored, the
    write would land in the owned brain and the hash would move.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
from pathlib import Path


def md5(path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


@contextlib.contextmanager
def owned_default_brain(source, workdir, name: str = "owned_default.db"):
    """Copy ``source`` into ``workdir`` and make it the RESOLVED default brain.

    Yields the owned copy's path. Restores ``WIZARD_LIBRARY_DB`` /
    ``CLAUDE_MEMORY_DB`` on exit whether or not the body raised — a test that
    leaves the resolver pointed at its scratch file is the module-global class
    (D8-F4) in a new costume.
    """
    src = Path(source)
    owned = Path(workdir) / name
    shutil.copy2(src, owned)
    # A WAL brain carries its tail in sidecars; copy them or the owned copy is a
    # different database from the one the source presents.
    for sfx in ("-wal", "-shm"):
        side = Path(str(src) + sfx)
        if side.exists():
            shutil.copy2(side, str(owned) + sfx)
    saved = {k: os.environ.get(k) for k in ("WIZARD_LIBRARY_DB", "CLAUDE_MEMORY_DB")}
    os.environ["WIZARD_LIBRARY_DB"] = str(owned)
    os.environ.pop("CLAUDE_MEMORY_DB", None)
    try:
        yield owned
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def packaged_brain(dst):
    """A brain built from the PACKAGE, not borrowed from the host: the shipped blank seed
    (seed/claude_memory_blank.db) brought up to this package's schema by the shipped
    migrator -- exactly what install.sh Step B does to a fresh brain (B.1.5).

    Reference-host round, R9. Two tests assumed a brain beside the tree: one passed
    ``db=None`` and let the resolver fall to ``<tree>/claude_memory.db``, one let an organ
    copy it. An INSTALLED brain has that file; a CHECKOUT does not -- so both went red in
    every checkout (the parent's "known pair") while passing on the author's machine. The
    brain is now the test's own, built from files every tree carries (both are laid under
    root=brain), so the verdict is the same in a checkout and on an install.
    """
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[2]
    seed = root / "seed" / "claude_memory_blank.db"
    migrator = root / "organs" / "schema_migrator.py"
    dst = Path(dst)
    shutil.copy2(str(seed), str(dst))
    p = subprocess.run([sys.executable, "-B", str(migrator), "apply", "--db", str(dst)],
                       capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "WIZARD_LIBRARY_DB": str(dst)})
    if p.returncode != 0:
        raise AssertionError("the packaged migrator could not build a brain from the packaged seed: "
                             + (p.stdout[-600:] + p.stderr[-600:]))
    return dst
