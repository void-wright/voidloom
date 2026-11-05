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
  2. ``WIZARD_ENV_FILE``   — the env file the installer wrote and the scheduled
     jobs load. Set, non-empty and absolute: the env file itself for
     ``wizard-library-env``, else ``<its directory>/name``, unconditionally.
     basis ``"env-file"``. A relative value is ignored (the installer refuses
     one too) and the basis says so (``+env-file-relative-ignored``).
  3. ``<preferred>/name``  — the neutral default dir, if the file exists in
     it. basis ``"preferred"``.
  4. LEGACY discovery, ONLY when ``VOIDLOOM_LEGACY_DISCOVERY`` is exactly
     ``1``: the immediate hidden directories of ``$HOME``, sorted, and the
     first one that carries THIS install's ownership marker (below) AND
     contains ``name``. basis ``"discovered:<dirname>"``. Without the opt-in no
     other hidden directory is listed or touched at all.
  5. ``<preferred>/name``  — the neutral default, for creation by whoever
     needs to create it. basis ``"default"``.

  Note that limbs 3 and 5 return the SAME PATH and different bases. That is
  deliberate: "found in the default location" and "nothing exists anywhere,
  here is where it would go" are different facts about the world, and a
  caller that must not create a file needs to tell them apart.

WHY DISCOVERY IS OPT-IN AND MARKED
    Discovery used to run for every lookup. Two installs that share one user
    account and one ``$HOME`` (a second product running this package, or two
    copies) could then read each other's sidecars, credentials included,
    because "a hidden directory that contains a file of this name" names no
    owner. Now discovery runs only when asked for, and a discovered directory
    is used only if it carries ``.voidloom-owner`` (:data:`OWNER_MARKER`), a
    small JSON file ``{"schema": "voidloom.env-owner/1", "install_root":
    "<abs path>"}`` whose ``install_root`` is THIS install's root (the
    directory this module was laid into, both sides resolved). The marker is
    read before anything else in that directory; a directory without it, or
    marked for another install, has none of its sidecars opened or even
    looked up, and is counted in the basis (``+refused-unowned:<n>``).
    Limbs 1–3 and 5 are named by the operator or by the product, so an
    UNMARKED directory there is trusted (every install made before the marker
    existed is unmarked). A directory there that carries a marker which is NOT
    this install's (another root, malformed, a symlink) is refused on every
    limb with :class:`EnvOwnershipRefused` (a ``ValueError``): never a fall-
    through, because the default limb would then create this install's files
    inside the other install's directory. To keep a legacy directory working,
    set ``WIZARD_ENV_FILE`` or ``VOIDLOOM_ENV_DIR`` to it, or opt in and mark it:
    ``python3 organs/voidloom_env.py --mark <dir>``.

WHO MARKS, AND WHO THE CHECK SPEAKS FOR
    The installer marks the directory it creates, and only that one: ownership
    comes from the act of creation, so :func:`claim_env_dir` is an atomic
    ``mkdir`` that writes the marker when, and only when, that ``mkdir`` made the
    directory. It has no path that marks a directory which already existed.
    :func:`release_owner_marker` removes a marker only if it names the given
    root, and is what uninstall calls. Neither runs during resolution.

    :func:`install_root` says where this code IS; :func:`acting_root` says which
    install it speaks for, and every ownership check compares against that. They
    differ in one case: the installer runs this module from the downloaded package
    while serving the brain directory it is laying, so it sets
    ``VOIDLOOM_INSTALL_ROOT`` for its own process tree. That is a claim which is
    still checked: it passes only a directory whose marker names that same root.
    It is never written to the env file, so scheduled jobs and the CLI answer
    for themselves. A marker is a label between installs, not a lock: a process
    of the same user can read it and copy it.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional

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
    "ENV_FILE_KEY",
    "ENV_FILE_NAME",
    "LEGACY_DISCOVERY_KEY",
    "OWNER_MARKER",
    "INSTALL_ROOT_KEY",
    "install_root",
    "acting_root",
    "owner_marker_ok",
    "owner_state",
    "write_owner_marker",
    "claim_env_dir",
    "release_owner_marker",
    "EnvOwnershipRefused",
    "embed_pin",
    "set_embed_pin",
]

#: The neutral, vendor-free directory this project uses on a fresh machine.
#: Named for the project, not for anyone who runs it.
DEFAULT_DIR_NAME = ".voidloom"

#: The one environment variable that overrides everything.
ENV_DIR_KEY = "VOIDLOOM_ENV_DIR"

#: The env file the installer writes and the scheduled jobs load; its directory is limb 2.
ENV_FILE_KEY = "WIZARD_ENV_FILE"
ENV_FILE_NAME = "wizard-library-env"

# The env file's managed block, as install.sh writes it, and the one line of it that names the embedder.
ENV_BLOCK_BEGIN = "# >>> wizard-library managed >>>"
ENV_BLOCK_END = "# <<< wizard-library managed <<<"
EMBED_PIN_KEY = "WIZARD_EMBED_MODEL"

#: Discovery of a legacy directory runs only when this is exactly "1".
LEGACY_DISCOVERY_KEY = "VOIDLOOM_LEGACY_DISCOVERY"

#: Which install this process speaks for, when that is not where its code sits (the installer sets it).
INSTALL_ROOT_KEY = "VOIDLOOM_INSTALL_ROOT"

#: The ownership marker a discovered directory must carry, and its schema.
OWNER_MARKER = ".voidloom-owner"
OWNER_SCHEMA = "voidloom.env-owner/1"

#: Hard cap on how many hidden directories the discovery limb will consider.
#: A pathological $HOME (thousands of dotdirs) must not turn a path lookup
#: into a directory crawl. If the cap is hit we say so in the basis rather
#: than silently truncating — a truncated search that reports success is the
#: "shrunken domain, full coverage" defect.
_DISCOVERY_CAP = 512


class EnvOwnershipRefused(ValueError):
    """A sidecar directory carries an ownership marker that is not this install's. Raised, never fallen through."""

    code = "ENV_DIR_FOREIGN"


class EnvResolution(NamedTuple):
    """The verbose result of resolving a sidecar path.

    * ``path``   — the resolved path (always present; may not exist).
    * ``basis``  — provenance: ``"env"``, ``"env-file"``, ``"preferred"``,
      ``"discovered:<dirname>"``, ``"default"``, optionally suffixed
      ``"+env-file-relative-ignored"``, ``"+discovery-capped"`` or
      ``"+refused-unowned:<n>"``.
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


def install_root() -> Path:
    """This install's root: the directory this module was laid into (``<root>/organs/voidloom_env.py``)."""
    return Path(__file__).resolve().parent.parent


def acting_root() -> Path:
    """The install this process speaks for: ``VOIDLOOM_INSTALL_ROOT`` when set, else :func:`install_root`.

    A relative value is refused rather than read against the process CWD."""
    claimed = _clean_env(INSTALL_ROOT_KEY)
    if not claimed:
        return install_root()
    p = Path(claimed).expanduser()
    if not p.is_absolute():
        raise ValueError("%s must be an absolute path, got %r" % (INSTALL_ROOT_KEY, claimed))
    return p.resolve()


def owner_marker_ok(d: Path) -> bool:
    """True only when ``d/.voidloom-owner`` is a regular file (not a symlink) whose JSON names THIS install's
    root. Reads that one small file and nothing else in ``d``. Any defect (absent, unreadable, too large, not
    JSON, wrong schema, another root) is False: an unproven owner is not this install."""
    root = _marker_root(d)
    return root is not None and root == acting_root()


def _marker_root(d: Path) -> Optional[Path]:
    """The root ``d``'s marker names, resolved; None for any marker that proves nothing."""
    import json
    m = d / OWNER_MARKER
    try:
        if m.is_symlink() or not m.is_file() or m.stat().st_size > 4096:
            return None
        doc = json.loads(m.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("schema") != OWNER_SCHEMA:
            return None
        root = doc.get("install_root")
        if not isinstance(root, str) or not os.path.isabs(root):
            return None
        return Path(root).resolve()
    except (OSError, ValueError):
        return None


def owner_state(d: Path) -> str:
    """``absent`` (no directory), ``unmarked``, ``own``, or ``foreign``. Reads the marker and nothing else."""
    d = Path(d)
    if not d.is_dir():
        return "absent"
    m = d / OWNER_MARKER
    try:
        present = m.is_symlink() or m.exists()
    except OSError:
        present = True
    if not present:
        return "unmarked"
    return "own" if owner_marker_ok(d) else "foreign"


def _refuse_if_foreign(d: Path, limb: str) -> None:
    """Named limbs trust an UNMARKED directory; a marker that is present and not this install's refuses."""
    m = d / OWNER_MARKER
    try:
        present = m.is_symlink() or m.exists()
    except OSError:
        present = True
    if present and not owner_marker_ok(d):
        raise EnvOwnershipRefused(
            "%s: the %s sidecar directory %s carries %s naming another install (or one that cannot be read); this "
            "install (root %s) will not read or create its files there. Point VOIDLOOM_ENV_DIR at this install's own "
            "directory, or for one command act as the install that owns it: %s=<its root> <command>. If this brain moved "
            "here, delete %s or run: python3 %s --mark %s"
            % (EnvOwnershipRefused.code, limb, d, OWNER_MARKER, acting_root(), INSTALL_ROOT_KEY,
               d / OWNER_MARKER, os.path.abspath(__file__), d))


def write_owner_marker(d: Path, root: Optional[Path] = None) -> Path:
    """Mark ``d`` as an install's sidecar directory (``root``, else :func:`acting_root`). The ONE function that
    writes the marker; resolution never calls it. The directory must already exist. By hand this is the opt-in
    route for a legacy directory; the installer reaches it only through :func:`claim_env_dir`."""
    import json
    d = Path(d)
    if not d.is_dir():
        raise FileNotFoundError("not a directory: %s" % d)
    m = d / OWNER_MARKER
    tmp = d / (OWNER_MARKER + ".tmp")
    named = Path(root).resolve() if root is not None else acting_root()
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"schema": OWNER_SCHEMA, "install_root": str(named)}) + "\n")
    os.replace(str(tmp), str(m))
    return m


def claim_env_dir(d: Path, root: Path) -> bool:
    """Create ``d`` and mark it for ``root``. True when this call created it; False, with nothing written, when
    it already existed. The ``mkdir`` is the test of creation, so no probe can go stale before the mark."""
    d = Path(d)
    d.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.mkdir(str(d), 0o700)
    except FileExistsError:
        return False
    write_owner_marker(d, root)
    return True


def release_owner_marker(d: Path, root: Path) -> bool:
    """Remove ``d``'s marker only if it names ``root``. True when removed. The root need not exist any more."""
    named = _marker_root(Path(d))
    if named is None or named != Path(root).resolve():
        return False
    os.unlink(str(Path(d) / OWNER_MARKER))
    return True


def candidate_dirs() -> List[Path]:
    """Hidden directories directly under ``$HOME``, sorted, capped.

    ONE level, no recursion, no symlink following into the tree. Sorted so the
    answer is deterministic across runs and across filesystems whose readdir
    order is not stable. Only the opt-in discovery limb calls this, and it
    uses a directory only if :func:`owner_marker_ok` says it is this install's.
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
        d = Path(override).expanduser()
        _refuse_if_foreign(d, "VOIDLOOM_ENV_DIR")
        p = d / name
        return EnvResolution(p, "env", p.exists())

    # 2. the env file the installer wrote (and the jobs load): its directory.
    note = ""
    env_file = _clean_env(ENV_FILE_KEY)
    if env_file:
        f = Path(env_file).expanduser()
        if f.is_absolute():
            _refuse_if_foreign(f.parent, "WIZARD_ENV_FILE")
            p = f if name == ENV_FILE_NAME else f.parent / name
            return EnvResolution(p, "env-file", p.exists())
        note = "+env-file-relative-ignored"

    default_dir = Path.home() / DEFAULT_DIR_NAME
    _refuse_if_foreign(default_dir, "default")
    preferred = default_dir / name

    # 3. the neutral default, if the file is actually there.
    try:
        if preferred.is_file():
            return EnvResolution(preferred, "preferred" + note, True)
    except OSError:
        pass

    # 4. discovery, ONLY on the explicit legacy opt-in, and only into a directory marked as THIS install's.
    if os.environ.get(LEGACY_DISCOVERY_KEY) == "1":
        cands = candidate_dirs()
        if len(cands) >= _DISCOVERY_CAP:
            note += "+discovery-capped"
        refused = 0
        for d in cands:
            if d == default_dir:
                continue  # already tried, and it did not have the file
            if not owner_marker_ok(d):
                refused += 1
                continue  # not this install's: none of its files is looked at
            cand = d / name
            try:
                if cand.is_file():
                    return EnvResolution(cand, "discovered:" + d.name + note, True)
            except OSError:
                continue
        if refused:
            note += "+refused-unowned:%d" % refused

    # 5. nothing exists anywhere. Report where it WOULD go, and say plainly
    #    that it is not there — never create it here.
    return EnvResolution(preferred, "default" + note, False)


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


def _engine_reader():
    """install/env_file.py, the one reader and writer of the env file; None where it is not laid."""
    try:
        import voidloom_device
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import voidloom_device
    return voidloom_device._systemd_reader()


def embed_pin(db: Any) -> Dict[str, Any]:
    """What the env file THIS brain's jobs load says about its embedder. Reads only.

    ``belongs`` is whether the file names this database in WIZARD_LIBRARY_DB: an env file written for another brain says
    nothing about this one, and no verb acting on a copy may change what the installed brain's jobs read. ``value`` is
    the effective WIZARD_EMBED_MODEL, ``inside`` whether the managed block assigns it, and ``owner_line`` the line number
    of one assigned outside the markers: that line is the owner's, and it wins by systemd's last-wins."""
    out: Dict[str, Any] = {"path": None, "belongs": False, "value": None, "inside": False, "owner_line": None,
                           "unreadable": None}
    ef = _engine_reader()
    try:
        path = env_path(ENV_FILE_NAME)
    except (EnvOwnershipRefused, ValueError, OSError) as e:
        out["unreadable"] = str(e)
        return out
    out["path"] = str(path)
    if ef is None or not path.is_file():
        return out
    try:
        res = ef.read_file(str(path), ENV_BLOCK_BEGIN, ENV_BLOCK_END)
    except OSError as e:
        out["unreadable"] = str(e)
        return out
    if res["error"]:
        out["unreadable"] = "line %d: %s" % (res["error_line"], res["error"])
        return out
    named = res["effective"].get("WIZARD_LIBRARY_DB", (None,))[0]
    out["belongs"] = bool(named) and os.path.realpath(os.path.expanduser(named)) == os.path.realpath(str(db))
    got = res["effective"].get(EMBED_PIN_KEY)
    if got:
        out["value"] = got[0]
    out["inside"] = any(u.inside and any(k == EMBED_PIN_KEY for k, _v in u.sd) for u in res["units"])
    if EMBED_PIN_KEY in res["outside"]:
        out["owner_line"] = res["outside"][EMBED_PIN_KEY][1]
    return out


def set_embed_pin(path: Any, value: str) -> None:
    """Rewrite the managed block's WIZARD_EMBED_MODEL line, atomically (temp in the same directory, fsync, rename), with
    the file's mode kept. Raises the reader's Refusal, or OSError, with the file exactly as it was."""
    ef = _engine_reader()
    path = Path(path)
    data = path.read_bytes()
    image = ef.rewrite_block_key(data, EMBED_PIN_KEY, value, ENV_BLOCK_BEGIN, ENV_BLOCK_END)
    if image == data:
        return
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(image)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def describe(name: str) -> str:
    """One-line human/gate-readable provenance, for doctors and logs."""
    r = resolve_verbose(name)
    return "%s (basis: %s, exists: %s)" % (r.path, r.basis, "yes" if r.exists else "no")


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import sys

    if sys.argv[1:2] == ["--mark"] and len(sys.argv) == 3:
        print("marked: %s" % write_owner_marker(Path(sys.argv[2]).expanduser()))
        raise SystemExit(0)
    if sys.argv[1:2] == ["--owner-state"] and len(sys.argv) == 3:
        print(owner_state(Path(sys.argv[2]).expanduser()))
        raise SystemExit(0)
    if sys.argv[1:2] == ["--claim"] and len(sys.argv) == 4:
        print("created" if claim_env_dir(Path(sys.argv[2]), Path(sys.argv[3])) else "existed")
        raise SystemExit(0)
    if sys.argv[1:2] == ["--release"] and len(sys.argv) == 4:
        print("released" if release_owner_marker(Path(sys.argv[2]), Path(sys.argv[3])) else "kept")
        raise SystemExit(0)
    names = sys.argv[1:] or ["wizard-library-env", "wizard-config.json",
                             "claude-oauth-token"]
    print("preferred dir: %s" % preferred_dir())
    for n in names:
        print("  %-24s %s" % (n, describe(n)))
