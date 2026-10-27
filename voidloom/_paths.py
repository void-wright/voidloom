"""_paths.py — the SINGLE source of truth for Voidloom brain-path resolution.

Every module in this package that needs to know *which brain DB to open* asks
here. This is the one place the relative-self default is derived, and the one
place the env-first resolution chain is implemented — so the facade, the guard,
the bootstrap probe, the approvals faces and the CLI can never silently diverge
on which DB they are talking about (the divergence class that bites when a
caller sets ``WIZARD_LIBRARY_DB`` but one module still hardcodes its own default).

Design rules:
  * **stdlib-only.** Imports nothing heavy. In particular it does NOT import
    ``claude_memory`` (the engine) — resolving a path must never load a model.
  * **One relative-self derivation.** ``default_db()`` is the ONLY place in the
    package that computes ``Path(__file__).resolve().parent.parent /
    "claude_memory.db"``. This file ships inside ``voidloom/`` (one level under
    the brain dir), so ``parent.parent`` is the brain dir — identical to the
    derivation every module used to carry on its own.
  * **Env-first, deterministic order.** ``WIZARD_LIBRARY_DB`` >
    ``CLAUDE_MEMORY_DB`` > the relative-self default. The same order the engine,
    the hook and the installer honour. An explicit caller-supplied path (e.g. a
    temp-DB test copy) is the responsibility of the caller and is layered ON TOP
    of this via the ``override`` argument.
  * **Env-missing discipline.** If an env var IS set but points at a path whose
    PARENT directory does not exist, we do NOT silently fall through to a
    different DB. We resolve to it anyway (so the downstream engine-import / open
    error names the real misconfiguration) and expose a marker the caller can
    log via :func:`resolve_db_verbose`. We only raise :class:`PathResolutionError`
    when there is nothing usable to resolve to at all (which, given the
    relative-self fallback, effectively never happens — the raise exists as an
    explicit, documented failure mode rather than a silent ``None``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple, Optional

__all__ = [
    "PathResolutionError",
    "default_db",
    "resolve_db",
    "resolve_db_verbose",
    "brain_dir",
    "organ_dir",
    "DbResolution",
]


class PathResolutionError(RuntimeError):
    """Raised only when no brain DB path can be resolved at all.

    With the relative-self fallback always available this is effectively
    unreachable in normal operation; it exists as an explicit, named failure
    mode so a caller never silently receives ``None`` for a brain path.
    """


def default_db() -> str:
    """The relative-self default brain DB path.

    THE single relative-self derivation in the package. ``_paths.py`` ships
    inside ``voidloom/`` (one level under the brain dir), so ``parent.parent``
    is the brain dir and the DB sits beside the package. Resolved at runtime, so
    a fresh checkout on a stranger's machine resolves to wherever they unpacked
    it — no path is hardcoded in source.
    """
    return str(Path(__file__).resolve().parent.parent / "claude_memory.db")


class DbResolution(NamedTuple):
    """The verbose result of resolving the brain DB path.

    * ``path`` — the resolved DB path string (always present).
    * ``source`` — where it came from: ``"override"`` (explicit arg),
      ``"WIZARD_LIBRARY_DB"``, ``"CLAUDE_MEMORY_DB"``, or ``"relative_self"``.
    * ``parent_exists`` — whether the DB path's PARENT directory exists. When an
      env var is set but ``parent_exists`` is False, the path is a misconfig the
      caller should surface (we do NOT silently swap to a different DB).
    """

    path: str
    source: str
    parent_exists: bool


def resolve_db_verbose(override: Optional[str] = None) -> DbResolution:
    """Resolve the brain DB path with provenance, for callers that want to log.

    Order: ``override`` (explicit caller arg) > ``WIZARD_LIBRARY_DB`` >
    ``CLAUDE_MEMORY_DB`` > the relative-self default.

    Env-missing discipline (the sibling-resolver rule): if an env var is set but
    its parent dir does not exist, we resolve to it ANYWAY and report
    ``parent_exists=False`` — we never silently fall through to a different DB,
    so the downstream open/import error names the real misconfiguration.
    """
    if override:
        path, source = override, "override"
    else:
        wl = os.environ.get("WIZARD_LIBRARY_DB")
        cm = os.environ.get("CLAUDE_MEMORY_DB")
        if wl:
            path, source = wl, "WIZARD_LIBRARY_DB"
        elif cm:
            path, source = cm, "CLAUDE_MEMORY_DB"
        else:
            path, source = default_db(), "relative_self"

    if not path:  # defensive — default_db() never returns empty.
        raise PathResolutionError(
            "no brain DB path could be resolved (no override, no env var, and "
            "the relative-self default was empty)")

    try:
        parent_exists = Path(path).resolve().parent.is_dir()
    except Exception:
        parent_exists = False
    return DbResolution(path=path, source=source, parent_exists=parent_exists)


def resolve_db(override: Optional[str] = None) -> str:
    """Resolve the brain DB path. The canonical chain.

    Order: ``override`` > ``WIZARD_LIBRARY_DB`` > ``CLAUDE_MEMORY_DB`` >
    relative-self default. Returns the path string. For provenance + the
    parent-exists marker, use :func:`resolve_db_verbose`.
    """
    return resolve_db_verbose(override).path


def brain_dir(override: Optional[str] = None) -> Path:
    """The directory the brain DB lives in (where the engine + organ siblings
    sit). Derived from :func:`resolve_db` so the dir added to ``sys.path`` and
    the DB opened are always the same resolution."""
    return Path(resolve_db(override)).resolve().parent


def organ_dir(override: Optional[str] = None) -> Path:
    """The directory the engine + organ MODULES live in.

    Until 2026-09-07 the modules and the DB shared one directory, so
    :func:`brain_dir` served both jobs. They are now separate concerns: the DB
    is data at the tree root, the modules are code in ``organs/``. This PROBES
    for the layout rather than assuming it, so a flat tree -- an older install,
    or a second machine's brain -- still resolves. The two are distinguishable by inspection.
    """
    root = brain_dir(override)
    cand = root / "organs"
    return cand if cand.is_dir() else root
