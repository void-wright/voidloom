"""Wizard library canonical path resolution — INSTALL-ANYWHERE PORTABLE.

Single source of truth for the brain DB path + module home, designed so the
brain works on ANY machine (local, laptop, teammate handoff, a brain on Windows,
AWS Fargate) with zero manual path editing.

THREE-TIER FLEXIBILITY (Council deliberation, May 28, 2026 — memory #4247):

  1. PRIMARY — relative-self resolution: the DB is found relative to THIS
     file's own location (Path(__file__).parent). Self-locating, CWD-independent.
     The DB travels WITH the modules in every package, so this Just Works
     wherever the package is unpacked.

  2. OVERRIDE — env var WIZARD_LIBRARY_DB / WIZARD_LIBRARY_HOME. Set by the
     install script (or manually) to point at a SPECIFIC brain. REQUIRED for
     Phase 4 Cross-Tier Bridges, where one brain's modules query ANOTHER
     brain's DB (a brain <-> its twin). When set, the env var wins. When unset,
     we fall back to relative-self (tier 1) — NEVER a hardcoded absolute path.

  3. MACHINE-SPECIFIC — the install script generates systemd services / Windows
     Task Scheduler tasks with the correct absolute paths for that machine and
     writes the env file. This module never hardcodes machine paths.

Usage:
    from wizard_paths import get_db_path, get_module_dir
    db_path = get_db_path()          # env var > relative-self
    home = get_module_dir()          # env var > relative-self

To point at a DIFFERENT brain (e.g., a bridge target), set the env var or pass
an explicit override:
    db_path = get_db_path(override="/path/to/brain/claude_memory.db")
"""
import os
from pathlib import Path
from typing import Optional


def _self_dir() -> Path:
    """The directory this module lives in — the MODULE anchor."""
    return Path(__file__).resolve().parent


def _tree_root() -> Path:
    """The tree root — where the DATA lives (the DB, archives, manifest).

    Since the 2026-09-07 layout the modules sit in ``organs/`` and the data sits
    one level up beside it. Before that both were the same directory, and an
    older install is still a flat tree. So this PROBES rather than
    assumes: if this module is inside a directory named ``organs``, the root is
    its parent; otherwise the module dir IS the root. The two layouts are
    distinguishable by inspection, so nothing here is a guess.
    """
    d = _self_dir()
    return d.parent if d.name == "organs" else d


def get_db_path(override: Optional[str] = None) -> str:
    """Return the wizard library DB path.

    Resolution priority:
      1. explicit `override` argument (for cross-brain pointing, e.g. bridges)
      2. $WIZARD_LIBRARY_DB env var (set by install script per machine)
      3. relative-self: <this module's dir>/claude_memory.db (portable default)
    """
    if override:
        return override
    env = os.environ.get("WIZARD_LIBRARY_DB")
    if env:
        return env
    return str(_tree_root() / "claude_memory.db")


def get_module_dir(override: Optional[str] = None) -> Path:
    """Return the wizard library module directory.

    Resolution priority:
      1. explicit `override`
      2. $WIZARD_LIBRARY_HOME env var
      3. relative-self: this module's own directory (portable default)
    """
    if override:
        return Path(override)
    env = os.environ.get("WIZARD_LIBRARY_HOME")
    if env:
        return Path(env)
    return _self_dir()


def get_archive_dir() -> Path:
    """Return the archive directory for snapshots (DATA — beside the DB)."""
    return _tree_root() / "archives"


def get_brain_sync_dir() -> Path:
    """Return the Brain Sync infrastructure directory (tree root)."""
    return _tree_root() / "Brain_Sync"


def get_cron_dir() -> Path:
    """Return the cron scripts directory (tree root)."""
    return _tree_root() / "cron"


def get_migration_path(migration_filename: str) -> str:
    """Resolve a migration SQL file path (migrations/ at the tree root)."""
    return str(_tree_root() / "migrations" / migration_filename)


if __name__ == "__main__":
    # Self-test / diagnostic: print resolved paths
    import json
    print(json.dumps({
        "self_dir": str(_self_dir()),
        "tree_root": str(_tree_root()),
        "db_path": get_db_path(),
        "module_dir": str(get_module_dir()),
        "archive_dir": str(get_archive_dir()),
        "brain_sync_dir": str(get_brain_sync_dir()),
        "env_WIZARD_LIBRARY_DB": os.environ.get("WIZARD_LIBRARY_DB", "(unset — using relative-self)"),
        "env_WIZARD_LIBRARY_HOME": os.environ.get("WIZARD_LIBRARY_HOME", "(unset — using relative-self)"),
    }, indent=2))
