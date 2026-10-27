"""_guard.py — the local-filesystem guard (the CLI's auth model).

Voidloom is *ungated locally because it is gated on the local filesystem*
(locality-as-auth, plan #6105). A caller who can reach Drew's local brain file
on a local disk is, by construction, Drew (or a process Drew is running). The
guard is therefore CORE correct behaviour — not deferred security — and it does
exactly two cheap, structural things:

  1. **Locality check.** Resolve the DB path and REFUSE (status=``refused``,
     code=``non_local_db``) if it lives on a UNC / network / synced-cloud root
     (``//host/share``, ``\\\\host\\share``, a drive under a known cloud-sync
     root, or an explicitly network-mounted filesystem). A brain on a synced or
     networked path is not a trustworthy auth boundary — someone else's machine
     could be writing it.

  2. **Read-only degrade.** Probe whether the DB is writable. If it is reachable
     but read-only, READS are still allowed; WRITES degrade gracefully
     (status=``refused``, code=``readonly_db``) instead of crashing mid-write.

Everything here is stdlib-only and fail-closed-but-graceful: any ambiguity that
cannot be resolved is reported as a clean refusal, never an exception into the
caller.

Deferred to the final comprehensive security audit (#176) — NOT built here:
the pre-store secret-detector, the enact audit-log row, terminal output
sanitisation, and supply-chain pinning/signing. Those are explicitly out of
scope for v0.1; this guard is the structural floor they will later build on.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path, PurePath
from typing import Optional, Tuple

from . import _paths

# The default brain DB is resolved RELATIVE-SELF in the single package resolver
# (``_paths.default_db``): this package (``voidloom/``) ships inside the brain
# dir, so the DB sits one level up. The env knobs (WIZARD_LIBRARY_DB /
# CLAUDE_MEMORY_DB) always win over the fallback — see ``_paths.resolve_db``.

# Substrings that mark a path as living under a known consumer cloud-sync root.
# A brain on a synced path is not a sound local-auth boundary: another device on
# the same account can mutate it. Case-insensitive containment check.
_CLOUD_SYNC_MARKERS: Tuple[str, ...] = (
    "/dropbox/",
    "/onedrive",            # "OneDrive", "OneDrive - Corp", ...
    "/google drive/",
    "/googledrive/",
    "/icloud",              # "iCloud Drive", "Library/Mobile Documents/com~apple~CloudDocs"
    "/com~apple~clouddocs/",
    "/mobile documents/",
    "/box sync/",
    "/pcloud/",
    "/mega/",
    "/nextcloud/",
    "/owncloud/",
    "/sync.com/",
    "/cryptomator/",
)

# Linux filesystem types that are network/remote rather than local block storage.
# Probed best-effort via /proc/mounts; absence of the probe never blocks.
_NETWORK_FSTYPES: Tuple[str, ...] = (
    "nfs", "nfs4", "cifs", "smbfs", "smb3", "afpfs", "fuse.sshfs",
    "fuse.rclone", "fuse.gdrive", "fuse.davfs", "davfs", "ncpfs",
    "glusterfs", "ceph",
    # WSL host bridges — the brain belongs on native Linux block storage, NOT on
    # the Windows-host bridge (that path is the *other* brain's home, e.g. EDDIE
    # under /mnt/c, reached only via the cross-tier bridge — never the primary).
    # 9p is the WSL1 bridge; iter-2 (Morrow) adds drvfs, the WSL2 /mnt/<drive>
    # bridge, so /mnt/c is detected as non-local on drvfs boxes too, not only 9p.
    "9p", "drvfs",
)


class GuardVerdict:
    """Result of a guard probe. ``ok`` is True only when the path is a sound
    local auth boundary. ``writable`` is a best-effort hint; the authoritative
    write decision is made by ``require_write`` at call time."""

    __slots__ = ("ok", "code", "message", "db_path", "writable")

    def __init__(self, ok: bool, code: str, message: str,
                 db_path: str, writable: Optional[bool]):
        self.ok = ok
        self.code = code
        self.message = message
        self.db_path = db_path
        self.writable = writable

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "db_path": self.db_path,
            "writable": self.writable,
        }


def _looks_like_unc(raw: str) -> bool:
    """A Windows UNC or a POSIX double-slash authority path (//host/share)."""
    s = raw.strip()
    if s.startswith("\\\\"):
        return True
    # POSIX: a leading '//' (but not a single '/') denotes an authority/share.
    # '///' collapses to root on most systems, so only treat exactly-two as UNC.
    if s.startswith("//") and not s.startswith("///"):
        return True
    return False


def _is_cloud_sync_path(resolved: str) -> bool:
    low = resolved.replace("\\", "/").lower()
    # Normalise to a trailing slash so a marker like "/onedrive" also matches a
    # path that *ends* at the sync root (".../OneDrive").
    probe = low if low.endswith("/") else low + "/"
    return any(marker in probe for marker in _CLOUD_SYNC_MARKERS)


def _network_fstype_for(resolved: str) -> Optional[str]:
    """Best-effort: return the network fstype backing ``resolved`` per
    /proc/mounts, else None. Never raises; absence of /proc/mounts -> None."""
    try:
        best_mount = ""
        best_fstype = ""
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount_point, fstype = parts[1], parts[2]
                # Longest matching mount prefix wins (most specific mount).
                if (resolved == mount_point
                        or resolved.startswith(mount_point.rstrip("/") + "/")):
                    if len(mount_point) >= len(best_mount):
                        best_mount = mount_point
                        best_fstype = fstype
        if best_fstype and best_fstype.lower() in _NETWORK_FSTYPES:
            return best_fstype
    except Exception:
        return None
    return None


def resolve_db_path(db_path: Optional[str] = None) -> str:
    """Resolve the DB path the engine will use, honouring the same env knobs the
    engine + approvals backend honour, then the relative-self default.

    Priority: explicit arg > WIZARD_LIBRARY_DB > CLAUDE_MEMORY_DB >
    the relative-self default (the brain DB beside this package).

    Delegates to the single package resolver (``_paths.resolve_db``) so the
    guard, facade, bootstrap, approvals faces and CLI all resolve identically.
    """
    return _paths.resolve_db(db_path)


def check(db_path: Optional[str] = None) -> GuardVerdict:
    """Run the locality + readability probe. Pure, stdlib-only, never raises.

    Returns a GuardVerdict. ``ok`` is True only when the path resolves to a
    local, non-cloud, non-network location. ``writable`` is a best-effort hint.
    """
    raw = resolve_db_path(db_path)

    # 1) UNC / network-authority path — refuse before touching the filesystem.
    if _looks_like_unc(raw):
        return GuardVerdict(
            ok=False, code="non_local_db",
            message=(f"DB path {raw!r} is a UNC/network path; Voidloom's auth "
                     "model requires a LOCAL filesystem (locality-as-auth)."),
            db_path=raw, writable=None,
        )

    # Resolve symlinks / relative segments to an absolute path for the markers.
    try:
        resolved = str(Path(raw).resolve())
    except Exception:
        # An unresolvable path is, structurally, not a sound local boundary.
        resolved = str(PurePath(raw))

    # 2) Synced-cloud root — refuse.
    if _is_cloud_sync_path(resolved):
        return GuardVerdict(
            ok=False, code="non_local_db",
            message=(f"DB path {resolved!r} lives under a synced-cloud root; a "
                     "synced brain is not a sound local auth boundary."),
            db_path=resolved, writable=None,
        )

    # 3) Network-mounted filesystem — refuse (best-effort; absence never blocks).
    netfs = _network_fstype_for(resolved)
    if netfs is not None:
        return GuardVerdict(
            ok=False, code="non_local_db",
            message=(f"DB path {resolved!r} is on a {netfs} (network) mount; "
                     "Voidloom requires local block storage for its auth model."),
            db_path=resolved, writable=None,
        )

    # Locality OK. Probe readability/writability without mutating anything.
    writable = _probe_writable(resolved)
    return GuardVerdict(
        ok=True, code="local_ok",
        message="DB path is local; locality auth boundary satisfied.",
        db_path=resolved, writable=writable,
    )


def _probe_writable(resolved: str) -> Optional[bool]:
    """Best-effort writability probe. None if the file does not yet exist (the
    engine's ``init`` will create it) or the probe is inconclusive.

    We use os.access first (cheap), then — only when the file exists — a true
    read-only SQLite open does NOT prove writability, so we additionally consult
    the filesystem write bit. We never actually write."""
    p = Path(resolved)
    if not p.exists():
        return None  # not-yet-created — defer the decision to engine init.
    try:
        # C5: `os.access(db, W_OK)` is a FILE question, and SQLite's is not.
        # A journal-mode database needs to create its `-wal` and `-shm` BESIDE
        # the file, so a writable DB inside a read-only DIRECTORY is not
        # writable in any sense that matters. Measured: os.access True, and the
        # store then failed as `status: error, code: unhandled, "attempt to
        # write a readonly database"`, exit 1 -- an UNHANDLED error, where the
        # file-permission case gives a typed `readonly_db` refusal at exit 3.
        # Same condition, two different answers, because the probe could only
        # see half of it.
        if not os.access(resolved, os.W_OK):
            return False
        parent = p.parent
        if parent.exists() and not os.access(str(parent), os.W_OK | os.X_OK):
            return False
        return True
    except Exception:
        return None


def require_local(db_path: Optional[str] = None) -> Optional[dict]:
    """Gate helper for verbs: returns None when local-auth is satisfied, or a
    ready-to-emit ``refused`` envelope-data dict when it is not.

    Usage in a verb::

        refusal = _guard.require_local(db)
        if refusal is not None:
            return refused(refusal["code"], refusal["message"], data=refusal)
    """
    v = check(db_path)
    if v.ok:
        return None
    return v.as_dict()


def require_write(db_path: Optional[str] = None) -> Optional[dict]:
    """Gate helper for WRITE verbs (store). Returns None when a local, writable
    DB is available, else a ``refused`` data dict.

    Refuses with ``non_local_db`` for a non-local path, or ``readonly_db`` when
    the DB is reachable-but-read-only (graceful degrade: reads still work).
    """
    v = check(db_path)
    if not v.ok:
        return v.as_dict()
    # writable is True (ok), None (not-yet-created -> let init handle it), or
    # False (exists but read-only -> degrade writes gracefully).
    if v.writable is False:
        return {
            "ok": False,
            "code": "readonly_db",
            "message": (f"DB {v.db_path!r} is read-only; reads are allowed but "
                        "writes are refused (read-only degrade). Check file "
                        "permissions or remount read-write to store memories."),
            "db_path": v.db_path,
            "writable": False,
        }
    return None
