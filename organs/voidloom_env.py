"""voidloom_env.py — the SINGLE source of truth for Voidloom ENV-DIRECTORY resolution.

Companion to ``voidloom/_paths.py``. That module answers *which brain DB do I
open*; this one answers *where does this install keep its operator-owned
sidecar files* — the environment file (PATH + OAuth token + tier), the JSON
config, the OAuth token, the per-organ config files.

WHY THIS MODULE EXISTS
    Before it, thirteen Python call sites each spelled the same directory as a
    literal, and that literal named the author's employer. A package cannot
    ship a vendor's name in a path, and a stranger's machine has no such
    directory. Meanwhile the author's live machine DOES, and must keep working
    across the change: a decoupling that breaks the only running instance has
    not decoupled anything, it has just moved the outage.

    So the requirement is genuinely two-sided:
      (a) nothing in the package names any particular organisation; and
      (b) an existing install whose sidecars live in a legacy directory is
          still found, with no migration step and no operator action.

    Those look contradictory only if you resolve by DIRECTORY NAME. Resolve by
    FILE CONTENT-ADDRESS instead and they are compatible: we do not look for a
    directory called anything in particular, we look for *the directory that
    actually contains the file being asked for*. The filenames themselves
    (``wizard-library-env``, ``wizard-config.json``) are Voidloom's own legacy
    vocabulary — they name this project, not any company — so the package may
    say them out loud.

DESIGN RULES  (deliberately mirrored from voidloom/_paths.py)
  * **stdlib-only.** Imports nothing heavy, never imports the engine, never
    loads a model. Resolving a path must stay free.
  * **Never creates anything.** Resolution is a pure question. No mkdir, no
    touch, no chmod. A resolver that creates the thing it was asked to find
    can never report that it is missing. (This is the same defect class as a
    DB-path resolver that opens with ``sqlite3.connect(path)`` — which
    CREATES a zero-byte file — instead of a ``mode=ro`` URI.)
  * **Env-first, deterministic order**, and an explicit empty env var is
    treated as UNSET rather than as an empty path. An exported-but-empty
    variable is the single most common way a shell profile silently disables
    a setting; resolving it to ``Path("")`` would silently relocate every
    sidecar to the process CWD.
  * **Provenance is returned, not logged.** :func:`resolve_verbose` reports
    WHERE the answer came from, so a caller — or a gate — can assert on the
    basis and not merely on the string. A check that asserts only the final
    path cannot tell a correct answer from a coincidence.

RESOLUTION ORDER for :func:`env_path(name)`
  1. ``VOIDLOOM_ENV_DIR``  — explicit operator override. Set and non-empty
     wins unconditionally, EVEN IF the file does not exist there, so that a
     fresh machine writes where it was told to. basis ``"env"``.
  2. ``<preferred>/name``  — the neutral default dir, if the file exists in
     it. basis ``"preferred"``.
  3. discovery            — the immediate hidden directories of ``$HOME``,
     sorted, first one that actually CONTAINS a file called ``name``. This is
     the limb that finds a legacy install without naming it.
     basis ``"discovered:<dirname>"``.
  4. ``<preferred>/name``  — the neutral default, for creation by whoever
     needs to create it. basis ``"default"``.

  Note that limbs 2 and 4 return the SAME PATH and different bases. That is
  deliberate: "found in the default location" and "nothing exists anywhere,
  here is where it would go" are different facts about the world, and a
  caller that must not create a file needs to tell them apart.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, NamedTuple, Optional

__all__ = [
    "DEFAULT_DIR_NAME",
    "ENV_DIR_KEY",
    "EnvResolution",
    "preferred_dir",
    "env_dir",
    "env_path",
    "resolve_verbose",
    "describe",
    "candidate_dirs",
]

#: The neutral, vendor-free directory this project uses on a fresh machine.
#: Named for the project, not for anyone who runs it.
DEFAULT_DIR_NAME = ".voidloom"

#: The one environment variable that overrides everything.
ENV_DIR_KEY = "VOIDLOOM_ENV_DIR"

#: Hard cap on how many hidden directories the discovery limb will consider.
#: A pathological $HOME (thousands of dotdirs) must not turn a path lookup
#: into a directory crawl. If the cap is hit we say so in the basis rather
#: than silently truncating — a truncated search that reports success is the
#: "shrunken domain, full coverage" defect.
_DISCOVERY_CAP = 512


class EnvResolution(NamedTuple):
    """The verbose result of resolving a sidecar path.

    * ``path``   — the resolved path (always present; may not exist).
    * ``basis``  — provenance: ``"env"``, ``"preferred"``,
      ``"discovered:<dirname>"``, ``"default"``, optionally suffixed
      ``"+discovery-capped"``.
    * ``exists`` — whether the resolved path exists RIGHT NOW. Callers that
      must not create files branch on this.
    """

    path: Path
    basis: str
    exists: bool


def _clean_env(key: str) -> Optional[str]:
    """Read an env var, treating exported-but-empty and whitespace as UNSET.

    ``VOIDLOOM_ENV_DIR=`` in a profile is a disabled setting, not a request to
    use the empty path. Returning "" here would resolve every sidecar relative
    to the process CWD — which for a systemd unit is ``/``.
    """
    raw = os.environ.get(key)
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def preferred_dir() -> Path:
    """The neutral default sidecar directory: ``$HOME/.voidloom``.

    Honours ``VOIDLOOM_ENV_DIR`` when set and non-empty, so this is also the
    directory a fresh install writes into.
    """
    override = _clean_env(ENV_DIR_KEY)
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DIR_NAME


def candidate_dirs() -> List[Path]:
    """Hidden directories directly under ``$HOME``, sorted, capped.

    ONE level, no recursion, no symlink following into the tree. Sorted so the
    answer is deterministic across runs and across filesystems whose readdir
    order is not stable. ``$HOME/.voidloom`` is not special-cased here — the
    caller checks it first — but it will also appear in this list if present,
    which is harmless because it resolves to the same path.
    """
    home = Path.home()
    out: List[Path] = []
    try:
        with os.scandir(home) as it:
            for entry in it:
                if not entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                out.append(home / entry.name)
                if len(out) >= _DISCOVERY_CAP:
                    break
    except OSError:
        # An unreadable or absent $HOME is a real condition, not an error to
        # swallow into "no candidates". We return empty and the basis the
        # caller receives will be "default", which is the honest answer: we
        # could not look, so we did not find.
        return []
    return sorted(out)


def resolve_verbose(name: str) -> EnvResolution:
    """Resolve one sidecar filename to a path, with provenance.

    ``name`` is a bare filename (e.g. ``"wizard-library-env"``). It must not
    contain a path separator: this function resolves WHICH DIRECTORY, and a
    caller passing ``"a/b"`` is asking a different question.
    """
    if not name or os.sep in name or (os.altsep and os.altsep in name):
        raise ValueError(
            "voidloom_env.resolve_verbose expects a bare filename, got %r" % (name,)
        )

    # 1. explicit override wins unconditionally, existing or not.
    override = _clean_env(ENV_DIR_KEY)
    if override:
        p = Path(override).expanduser() / name
        return EnvResolution(p, "env", p.exists())

    default_dir = Path.home() / DEFAULT_DIR_NAME
    preferred = default_dir / name

    # 2. the neutral default, if the file is actually there.
    try:
        if preferred.is_file():
            return EnvResolution(preferred, "preferred", True)
    except OSError:
        pass

    # 3. discovery: the directory that CONTAINS the file, whatever it is called.
    cands = candidate_dirs()
    capped = len(cands) >= _DISCOVERY_CAP
    suffix = "+discovery-capped" if capped else ""
    for d in cands:
        if d == default_dir:
            continue  # already tried, and it did not have the file
        cand = d / name
        try:
            if cand.is_file():
                return EnvResolution(cand, "discovered:" + d.name + suffix, True)
        except OSError:
            continue

    # 4. nothing exists anywhere. Report where it WOULD go, and say plainly
    #    that it is not there — never create it here.
    return EnvResolution(preferred, "default" + suffix, False)


def env_path(name: str) -> Path:
    """Resolve one sidecar filename to a path. The common entry point."""
    return resolve_verbose(name).path


def env_dir(name: Optional[str] = None) -> Path:
    """The sidecar DIRECTORY.

    With ``name``, returns the directory that resolution chose for that file —
    which is the only honest answer, because different sidecars may legitimately
    live in different directories during a migration. Without ``name``, returns
    the preferred directory (override, else ``$HOME/.voidloom``).
    """
    if name is None:
        return preferred_dir()
    return resolve_verbose(name).parent if False else resolve_verbose(name).path.parent


def describe(name: str) -> str:
    """One-line human/gate-readable provenance, for doctors and logs."""
    r = resolve_verbose(name)
    return "%s (basis: %s, exists: %s)" % (r.path, r.basis, "yes" if r.exists else "no")


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import sys

    names = sys.argv[1:] or ["wizard-library-env", "wizard-config.json",
                             "claude-oauth-token"]
    print("preferred dir: %s" % preferred_dir())
    for n in names:
        print("  %-24s %s" % (n, describe(n)))
