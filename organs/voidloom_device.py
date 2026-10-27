#!/usr/bin/env python3
"""voidloom_device.py -- RESOLVE the CUDA device. Never DECLARE it.

PROTECTED #1697 (2026-04-22): "Python code must NEVER set CUDA_VISIBLE_DEVICES
at runtime ... per-machine drop-in overrides become impossible ... code stays
portable between GPU and CPU hosts."  #17330: "a package carries the MECHANISM
for an exception, never the exception itself."

THE EXCEPTION THIS PACKAGE MUST NOT CARRY
-----------------------------------------
The author's workstation has two GPUs, and index 0 drives the monitor
(display_active=Enabled); a job that takes it evicts the desktop session
silently.  So THAT box wants device 1.  Roughly 99% of machines have one GPU
and it is index 0, so EVERY OTHER box wants device 0.  The device is therefore
a property of the MACHINE, and it belongs in a file on that machine -- not in
a line of shipped Python.

=> THE DECLARING SURFACE MUST BE A FILE, because a file is readable from all
   four surfaces (systemd / login shell / hook subprocess / bare CLI) and an
   exported variable is not.  MEASURED 2026-09-05: WIZARD_CUDA_DEVICE is UNSET
   on 3 of those 4.

WHAT ROUND pi-2 CHANGED, AND WHY  (sigma-3 + sigma-4)
------------------------------------------------------
Round pi shipped this resolver and it was RIGHT about the safe question and
BLIND about the adjacent one.  Measured on the round-pi build:

    a stranger's box, no declaring file          -> 0  (basis: default)
    Drew's box, file truncated mid-write         -> 0  (basis: default)
    Drew's box, file is binary garbage           -> 0  (basis: default)
    Drew's box, file chmod 000                   -> 0  (basis: default)
    Drew's box, path is a directory              -> 0  (basis: default)
    Drew's box, dangling symlink                 -> 0  (basis: default)
    Drew's box, "WIZARD_CUDA_DEVICE=1 # card"    -> 0  (basis: default)
    Drew's box, CUDA_VISIBLE_DEVICES=GPU-abc     -> 0  (basis: default:ambient-invalid)

**Seven degradations of a PRESENT declaring file returned the SAFE answer with
the SAFE basis, byte-identical to the legitimate stranger's answer.**  A
resolver whose failure answer and whose safe answer cannot be told apart has no
failure signal -- and on THIS box the shared answer is the monitor card.  That
is the founding (0,True) vs (0,False) defect of this build, recurring inside
its own remedy.

Three rules now hold, and each one is asserted by a minimal pair in
_canaryO/PI2/pairs/ on which IT AND NO OTHER predicate flips:

  R1  DEGRADED IS NEVER "default".  Every degradation of a PRESENT declaring
      file resolves to PACKAGE_SAFE_DEFAULT (still safe -- the value does not
      change) under a basis beginning "degraded:".  The VALUE is unchanged; the
      BASIS is now a signal, and callers/gates can act on it.
  R2  LAST-WINS, one reader.  POSIX `source`, systemd EnvironmentFile= and
      install.sh:66 all take the LAST assignment of a duplicated key.  Round pi
      shipped a FIRST-wins Python reader beside a `tail -n1` shell reader; on
      "=1 then =0" they answered 1 and 0.  Three authorities agree and none of
      them is this file, so THIS FILE CHANGED.  Appending an override to the
      bottom of the file is the natural human act, and it used to split.
  R3  AN INVALID AMBIENT VALUE SKIPS ITS LAYER, as the docstring below always
      claimed.  It used to short-circuit straight to the default -- so a
      one-character typo in an operator's export routed every organ onto the
      monitor while the declaring file that said 1 was never consulted.

WHY NOT setdefault
------------------
#15708 forbids os.environ.setdefault("CUDA_VISIBLE_DEVICES", ...): setdefault
yields to an existing OR EMPTY value, so an exported-but-empty variable
silently wins and the pin never lands.  Rule 1 below is NOT setdefault -- it
tests explicitly for a NON-EMPTY value, which is the exact case setdefault
gets wrong.

ORDERING CONTRACT
-----------------
apply_cuda_pin() MUST be called BEFORE `import torch`.  CUDA reads
CUDA_VISIBLE_DEVICES at initialisation; setting it afterwards fails SILENTLY
and the process lands on device 0.

STDLIB ONLY, BY CONTRACT.  This module runs before torch, before
sentence_transformers, and from cron/ scripts that have not yet fixed sys.path.
It must never grow a third-party import.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import List, Optional, Tuple

__all__ = [
    "PACKAGE_SAFE_DEFAULT",
    "resolve_cuda_device",
    "apply_cuda_pin",
    "declared_device",
    "env_file_health",
    "is_degraded",
    "is_refused",
    "declares_no_gpu",
    "health_report",
    "describe",
    "nvidia_smi",
    "NVIDIA_SMI_DIRS",
    "cuda_selects_a_device",
    "cuda_available",
    "declared_display",
]

# The package-safe default.  0 is correct for ~99% of machines: one GPU, index
# 0, nothing else to pick.  This constant is the ONLY device literal that may
# appear anywhere in the packaged tree, and it is the safe one.
PACKAGE_SAFE_DEFAULT = "0"

_ENV_KEY = "CUDA_VISIBLE_DEVICES"
_DECLARE_KEY = "WIZARD_CUDA_DEVICE"
_DISPLAY_KEY = "WIZARD_DISPLAY_INDEX"   # the card that drives the monitor(s): '<index>' or 'none' -- the OWNER's word
_CONFIG_KEY = "gpu_device_index"

# R1's vocabulary.  "absent" and "ok" are the two HEALTHY states; everything
# else is a degradation and must never share a basis with them.
HEALTH_ABSENT = "absent"          # no declaring file: the legitimate stranger
HEALTH_OK = "ok"                  # a readable file that declares the key
HEALTH_KEY_ABSENT = "key-absent"  # a readable file that declares other keys
DEGRADED_HEALTHS = (
    "empty-file",        # zero bytes -- the truncate-rewrite race window
    "unreadable",        # chmod 000, or any OSError on read
    "not-a-file",        # a directory, or a dangling symlink
    "binary",            # NUL bytes: not an env file at all
    "unparseable-value", # the key is there and its value is not a device spec
)

# Bounded retry for the truncate-rewrite race.  A writer that does
# `open(path,"w")` then writes leaves a window in which the file exists and is
# EMPTY.  Measured on the round-pi build: 85 of 150 reads in that window landed
# on (0, "default") -- a RATE, not an integer; re-runs differ.  Re-reading a
# few times collapses almost all of it, and whatever survives is reported as
# degraded rather than as the safe default.
_RACE_RETRIES = 3
_RACE_SLEEP_S = 0.02

_cache: dict = {}

# WHERE nvidia-smi LIVES WHEN IT IS NOT ON PATH (reference-host round, R7). On WSL2 the
# driver ships it in /usr/lib/wsl/lib, which an interactive shell's PATH carries and a
# systemd user unit's PATH does not -- measured on the reference host: gpu_guard said
# "TOPOLOGY FAIL: no GPUs visible -> cloud-claude-p" on every dream-daytime run while the
# same probe in a terminal saw both cards. Searched ONLY after PATH, and only for an
# executable file named nvidia-smi; a directory, not a guess about a card. A tuple so a
# test can point it at a fake without touching PATH.
NVIDIA_SMI_DIRS = ("/usr/lib/wsl/lib",)


def nvidia_smi() -> Optional[str]:
    """The nvidia-smi this process can execute: PATH first, then NVIDIA_SMI_DIRS; else None.

    The ONE locator for every organ that enumerates GPUs (gpu_guard, voidloom_manifest).
    It never runs the binary and never reads CUDA_VISIBLE_DEVICES: finding the tool that
    LISTS the cards is not choosing one, and the pin rules above are untouched by it."""
    import shutil
    exe = shutil.which("nvidia-smi")
    if exe:
        return exe
    for d in NVIDIA_SMI_DIRS:
        cand = os.path.join(d, "nvidia-smi")
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


# ---------------------------------------------------------------------------------------------------------------
# R-6 (model-choice round 1, EW4-05): NEVER INITIALISE CUDA IN THIS PROCESS UNDER A CUDA_VISIBLE_DEVICES THAT SELECTS
# NO DEVICE. MEASURED on the reference host (WSL2): libcuda's cuInit SEGFAULTS the interpreter (rc 139, no Python
# exception, no `except` can reach it) when CUDA_VISIBLE_DEVICES is '' , ' ', '-1', 'none', 'NoDevFiles' or an index
# the host lacks -- and `torch.cuda.is_available()` IS a cuInit. Twelve in-process call sites asked it directly; each now
# asks cuda_available(), which answers "no" for a value that selects no device WITHOUT calling the driver. An unset
# variable is the driver's to enumerate (unchanged); a numeric index is checked against the cards nvidia-smi LISTS (a
# separate process: it cannot crash this one) when nvidia-smi can be found, and trusted when it cannot.
# ---------------------------------------------------------------------------------------------------------------
_listed_cache: dict = {}


def _listed_indices() -> Optional[Tuple[int, ...]]:
    """The card indices nvidia-smi lists, once per process; None when it cannot be asked (never a guess)."""
    if "v" in _listed_cache:
        return _listed_cache["v"]
    exe = nvidia_smi()
    out = None
    if exe:
        import subprocess
        try:
            p = subprocess.run([exe, "--query-gpu=index", "--format=csv,noheader,nounits"], capture_output=True,
                               text=True, timeout=15, stdin=subprocess.DEVNULL)
            if p.returncode == 0:
                vals = [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]
                if vals and all(v.isdigit() for v in vals):
                    out = tuple(int(v) for v in vals)
        except (OSError, subprocess.SubprocessError):
            out = None
    _listed_cache["v"] = out
    return out


def _cuda_reading(raw: str) -> List[str]:
    """The entries CUDA actually honours in a CUDA_VISIBLE_DEVICES value: the list read up to its FIRST invalid entry.

    THE ONE PREDICATE (iterations 2-3 of the tier1-upgrade loop). An entry is valid when it is a card index (digits)
    or a GPU-/MIG- UUID; the first entry that is neither -- '-1', 'none', 'NoDevFiles', '', anything -- ends the list.
    So '1,-1' honours card 1, '-1,1' honours nothing, '' and '-1' honour nothing. cuda_selects_a_device() and
    resolve_cuda_device() both read the value through this function. Asks nothing of the driver or nvidia-smi."""
    out: List[str] = []
    for part in (raw or "").split(","):
        e = part.strip()
        if e.isdigit() or e.startswith(("GPU-", "MIG-")):
            out.append(e)
        else:
            break
    return out


def _spells_no_device(raw: str) -> Optional[str]:
    """Why ``raw`` selects NO device (CUDA's reading leaves no entry), else None."""
    if _cuda_reading(raw):
        return None
    first = (raw or "").split(",")[0].strip()
    return "empty" if not first else "%r is not a card index or UUID" % first


_cards_cache: dict = {}


def _card_table() -> Optional[List[Tuple[int, str, str]]]:
    """(index, uuid, display_active) per card, from nvidia-smi, once per process; None when it cannot be asked."""
    if "v" in _cards_cache:
        return _cards_cache["v"]
    exe = nvidia_smi()
    out = None
    if exe:
        import subprocess
        try:
            p = subprocess.run([exe, "--query-gpu=index,uuid,display_active", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
            if p.returncode == 0:
                rows = []
                for ln in p.stdout.splitlines():
                    f = [x.strip() for x in ln.split(",")]
                    if len(f) >= 2 and f[0].isdigit() and f[1].startswith("GPU-"):
                        rows.append((int(f[0]), f[1], f[2] if len(f) > 2 else ""))
                out = rows or None
        except (OSError, subprocess.SubprocessError):
            out = None
    _cards_cache["v"] = out
    return out


def _uuid_to_index(entry: str) -> Optional[Tuple[int, str]]:
    """(index, display_active) for a GPU- UUID (CUDA accepts a unique prefix); None when it cannot be resolved
    (no nvidia-smi, no match, an ambiguous prefix, or a MIG- instance, which nvidia-smi does not list here)."""
    table = _card_table()
    if not table or not entry.startswith("GPU-"):
        return None
    hits = [(i, d) for i, u, d in table if u == entry or u.startswith(entry)]
    return hits[0] if len(hits) == 1 else None


def cuda_selects_a_device(environ=None) -> Tuple[bool, str]:
    """(selects, why): whether CUDA_VISIBLE_DEVICES, as this process will hand it to the driver, selects at least one
    device -- decided WITHOUT initialising CUDA. CUDA reads the list up to its first invalid entry, so the FIRST entry
    decides: absent (unset) -> the driver enumerates; empty/blank, negative or not an index/UUID -> no device; a
    GPU-/MIG- UUID -> trusted; an index -> checked against nvidia-smi's list when it can be asked."""
    env = os.environ if environ is None else environ
    if _ENV_KEY not in env:
        return True, "CUDA_VISIBLE_DEVICES is unset: the driver enumerates the cards"
    raw = env.get(_ENV_KEY) or ""
    honoured = _cuda_reading(raw)
    first = honoured[0] if honoured else ""
    nd = _spells_no_device(raw)
    if nd is not None:
        return False, "CUDA_VISIBLE_DEVICES=%r selects no device (%s): CUDA is not initialised" % (raw, nd)
    if first.startswith(("GPU-", "MIG-")):
        return True, "CUDA_VISIBLE_DEVICES names a device UUID (%s)" % first[:12]
    listed = _listed_indices()
    if listed is None:
        return True, "CUDA_VISIBLE_DEVICES=%s (nvidia-smi could not be asked; the index is trusted)" % first
    if int(first) not in listed:
        return False, ("CUDA_VISIBLE_DEVICES=%r selects no device (card %s is not one nvidia-smi lists: %s): CUDA is "
                       "not initialised" % (raw, first, " ".join(str(i) for i in listed)))
    return True, "CUDA_VISIBLE_DEVICES=%s: a card nvidia-smi lists" % first


def cuda_available(torch_mod=None) -> bool:
    """torch.cuda.is_available(), asked ONLY when CUDA_VISIBLE_DEVICES selects a device (cuda_selects_a_device);
    otherwise False without touching the driver. Never raises: a torch that cannot be imported or asked is "no CUDA"."""
    ok, _why = cuda_selects_a_device()
    if not ok:
        return False
    if torch_mod is None:
        try:
            import torch as torch_mod  # noqa: F811 -- importing torch does not initialise CUDA; is_available() does
        except Exception:
            return False
    try:
        return bool(torch_mod.cuda.is_available())
    except Exception:
        return False


def _valid(value: str) -> bool:
    """A device spec is empty, or a comma-separated list of indices."""
    v = value.strip()
    if v == "":
        return True
    return all(part.strip().isdigit() for part in v.split(",") if part.strip() != "")


_NO_GPU_DECLARATIONS = ("-1", "none")


def declares_no_gpu(value: Optional[str]) -> bool:
    """WIZARD_CUDA_DEVICE=-1 or =none (any case): the owner declares NO GPU. Drew's ruling (2026-10-13, memory anchor
    vl_gpu_minus1_ruling): it means no GPU to EVERY consumer -- the resolver yields no device (CUDA_VISIBLE_DEVICES
    forced to ''), never card 0 and never the degraded card-0 fallback. The writers still refuse to WRITE it."""
    return value is not None and value.strip().strip("'\"").lower() in _NO_GPU_DECLARATIONS


def _strip_inline_comment(value: str) -> str:
    """Drop an UNQUOTED trailing '#' comment, the way `source` does.

    MEASURED: the real declaring file already carries 18 '#' lines, so this
    style is in live use.  Round pi's Python reader handed "1 # the compute
    card" to _valid(), which rejected the whole string and fell through to the
    default -- device 0, the monitor -- while `source` and every shell reader
    took it as 1.  Round pi's shell reader was no better: its `tr -d
    '[:space:]'` produced the literal "1#thecomputecard", which it would then
    have written into a unit file.  Both are now the same: strip, then
    validate, and if what is left is not a device spec say DEGRADED out loud.
    """
    v = value.strip()
    if v[:1] in ("'", '"'):
        quote = v[0]
        end = v.find(quote, 1)
        if end != -1:
            return v[1:end]
        return v[1:]
    cut = v.find("#")
    if cut == -1:
        return v
    # bash requires whitespace before an inline '#'; "a#b" is one word.
    if cut > 0 and not v[cut - 1].isspace():
        return v
    return v[:cut].strip()


def _env_file_path() -> Path:
    override = os.environ.get("WIZARD_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return voidloom_env.env_path("wizard-library-env")


def _config_file_path() -> Path:
    override = os.environ.get("WIZARD_CONFIG_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return voidloom_env.env_path("wizard-config.json")


_EF = None


def _systemd_reader():
    """install/env_file.py -- THE ONE READER of the env file, by systemd's EnvironmentFile= rules (a port of
    parse_env_file_internal(), bound to libsystemd-shared's own load_env_file() by voidloom/tests/test_env_file_reader.py).
    It lies beside organs/ in the checkout and in every installed brain (install/ and organs/ are laid as siblings).
    Loaded by PATH (no sys.path change, no __pycache__ left in install/); None when it is not there."""
    global _EF
    if _EF is None:
        import importlib.util
        import sys
        where = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "install", "env_file.py")
        if not os.path.isfile(where):
            return None
        spec = importlib.util.spec_from_file_location("voidloom_env_file_reader", where)
        mod = importlib.util.module_from_spec(spec)
        was = sys.dont_write_bytecode
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = was            # the reader sets it for its own CLI; not this process's business
        _EF = mod
    return _EF


def _read_env_file_once(path: Path, key: str = _DECLARE_KEY, valid=None) -> Tuple[Optional[str], str]:
    """One attempt.  Returns (value_or_None, health).

    THE UNITS' OWN READING (env-file writer pass, iteration 4): this is the layer-3 fallback of a UNIT's resolver, so it
    reads the file exactly as systemd's EnvironmentFile= hands it to that unit -- through install/env_file.py, the one
    tokenizer -- never by a second set of rules. The old line reader here stripped `export ` and so read
    `export WIZARD_CUDA_DEVICE=1` as 1, while systemd drops that line (an invalid key) and the unit never gets it;
    it also read `WIZARD_CUDA_DEVICE="1" # c` as 1 where systemd delivers '1# c'. LAST-WINS (R2) is systemd's own
    rule; the value then gets the resolver's own leniency (_strip_inline_comment), as the env layer does.
    A file systemd rejects WHOLE (a NUL, invalid UTF-8 in an assignment, a Unicode noncharacter) is "binary": the
    units start without it, and a degraded basis says so (R1). No tokenizer beside organs/ (an incomplete install):
    "unreadable", never a guess.
    """
    try:
        if path.is_symlink() and not path.exists():
            return None, "not-a-file"
        if path.exists() and not path.is_file():
            return None, "not-a-file"
        if not path.exists():
            return None, HEALTH_ABSENT
        raw_bytes = path.read_bytes()
    except OSError:
        return None, "unreadable"

    if len(raw_bytes) == 0:
        return None, "empty-file"
    if b"\x00" in raw_bytes:
        return None, "binary"
    ef = _systemd_reader()
    if ef is None:
        return None, "unreadable"
    res = ef.parse(raw_bytes)
    if res["error"]:
        return None, "binary"
    got = res["effective"].get(key)
    if got is None:
        return None, HEALTH_KEY_ABSENT
    value = _strip_inline_comment(got[0])
    if not (valid or _valid)(value):
        return None, "unparseable-value"
    return value, HEALTH_OK


def _read_env_file(key: str = _DECLARE_KEY, valid=None) -> Tuple[Optional[str], str]:
    """Read with a bounded retry across the truncate-rewrite window."""
    path = _env_file_path()
    last = (None, HEALTH_ABSENT)
    for attempt in range(_RACE_RETRIES):
        last = _read_env_file_once(path, key, valid)
        if last[1] in (HEALTH_OK, HEALTH_ABSENT, HEALTH_KEY_ABSENT):
            return last
        if attempt < _RACE_RETRIES - 1:
            time.sleep(_RACE_SLEEP_S)
    return last


def env_file_health() -> str:
    return _read_env_file()[1]


def is_refused(basis: str) -> bool:
    """True iff the resolver REFUSED the operator's value and set no device (iteration 5: a refusal overrides an
    explicit choice, so it must be recognisable by status code, not only by reading a basis string). The companion of
    is_degraded(): degraded = a damaged declaring surface; refused = a value set on purpose, overruled for safety."""
    return "refused" in (basis or "")


def refusal_line(value: Optional[str], basis: str) -> str:
    """The ONE stderr line a refusal prints at the point of use: the value, the reason, the result."""
    if basis.startswith("ambient-display-refused:"):
        why = ("card %s drives a display and this host lists another card (an owner with no display card to protect "
               "declares WIZARD_DISPLAY_INDEX=none)" % basis.split(":", 1)[1])
    elif basis.startswith("ambient-uuid-unresolved-refused"):
        why = "the UUID names no card nvidia-smi lists (or a MIG instance), so its card cannot be told"
    else:
        why = basis
    return ("voidloom: CUDA_VISIBLE_DEVICES=%r REFUSED (%s): %s -- running with NO GPU (CUDA_VISIBLE_DEVICES='')."
            % (value, basis, why))


def is_degraded(basis: str) -> bool:
    """True iff this basis reports a DAMAGED declaring surface (R1)."""
    return basis.startswith("degraded:")


def _read_config_file() -> Tuple[Optional[str], str]:
    path = _config_file_path()
    try:
        if not path.is_file():
            return None, HEALTH_ABSENT
        data = json.loads(path.read_text(errors="ignore"))
    except OSError:
        return None, "unreadable"
    except ValueError:
        return None, "unparseable-value"
    if not isinstance(data, dict):
        return None, "unparseable-value"
    value = data.get(_CONFIG_KEY)
    if value is None:
        return None, HEALTH_KEY_ABSENT
    value = str(value).strip()
    if not _valid(value):
        return None, "unparseable-value"
    return value, HEALTH_OK


def resolve_cuda_device() -> Tuple[Optional[str], str]:
    """Resolve the device WITHOUT touching the environment.

    Returns (value, basis).  A value of None means "make no assertion" -- the
    caller must leave CUDA_VISIBLE_DEVICES exactly as it found it.

    Priority, highest first:

      1. ambient      CUDA_VISIBLE_DEVICES already set and VALID.
                      An explicit operator or systemd Environment= line wins.
                      An EMPTY ambient is a deliberate "no devices" (CUDA's own
                      semantics): (None, "ambient-empty"), never the default.
                      Read as CUDA reads it (_cuda_reading): the list up to
                      its first invalid entry. Nothing honoured ('-1', '-1,1')
                      -> (None, "ambient-no-device"); something honoured ('1',
                      '1,-1') -> the value as set. A GPU- UUID is resolved to
                      its card through nvidia-smi; unresolvable, or a display
                      card -> ('', "ambient-uuid-...-refused"): no device,
                      never card 0. (Iteration 3: the old "ambient-invalid"
                      fall-through to the file or card 0 is gone.)
      2. env          WIZARD_CUDA_DEVICE exported (the systemd surface).
      3. env-file     WIZARD_CUDA_DEVICE in the declaring file, LAST-WINS (R2).
      4. config-file  gpu_device_index in wizard-config.json.
      5. default      PACKAGE_SAFE_DEFAULT -- device 0, correct everywhere else.

    A DEGRADED declaring surface at layer 3 or 4 still yields the safe VALUE
    and a basis beginning "degraded:" (R1), so a damaged file on Drew's box is
    never mistaken for a healthy one-GPU stranger's box.
    """
    if "resolved" in _cache:
        return _cache["resolved"]

    prefix = ""
    ambient = os.environ.get(_ENV_KEY)
    if ambient is not None and ambient.strip() == "":
        # An EMPTY ambient is CUDA's own spelling of "no devices visible" -- a
        # deliberate CPU declaration by whoever exported it. Before 2026-09-20 it
        # was read as "unset" and fell through to the file or to the DEFAULT, so on
        # a box with no declaring file `CUDA_VISIBLE_DEVICES=` pinned card 0: the
        # operator said "none" and got the monitor card (#20154). Honoured now
        # exactly as a file's declared-empty is: no assertion, the variable stays
        # as found.
        return _finish(None, "ambient-empty")
    if ambient is not None and _spells_no_device(ambient) is not None:
        # '-1', 'none', 'NoDevFiles', '-1,1' ... -- CUDA's own reading honours no entry: the same "no device" the
        # empty value means. No assertion, the variable stays as found, and the driver sees no card.
        return _finish(None, "ambient-no-device")
    if ambient is not None:
        # CUDA's reading honours at least one entry ('1', '1,-1', 'GPU-...'). The operator's value is HONOURED
        # exactly as set -- never rewritten, and never replaced by the file's card or card 0 (before iteration 3,
        # '1,-1' and every GPU- UUID were "ambient-invalid" and, with no declaring file, became card 0).
        honoured = _cuda_reading(ambient)
        # ONE DISPLAY-CARD RULE FOR EVERY SPELLING (iteration 4): an index, a full GPU- UUID and a unique UUID prefix
        # of the same card get the same answer. Where nvidia-smi lists TWO OR MORE cards, a pin that reaches a card
        # driving a display (display_active=Enabled, or the owner's WIZARD_DISPLAY_INDEX) -- or a UUID that names no
        # listed card, or a MIG- instance, whose card cannot be told -- is REFUSED: the pin becomes '' (no device),
        # never another card. With ONE card (a laptop whose only GPU drives its screen) or no nvidia-smi to ask,
        # nothing can be told apart, and the operator's value is honoured as set, whichever spelling.
        table = _card_table()
        if not table or len(table) < 2:
            uu = [e for e in honoured if not e.isdigit()]
            basis = "ambient" if not uu else ("ambient-unchecked:%s" % ("one card" if table else "no nvidia-smi"))
            return _finish(ambient.strip(), basis)
        disp, _src = declared_display()
        # THE OPT-OUT (iteration 5): WIZARD_DISPLAY_INDEX=none is the owner declaring there is NO display card to
        # protect (a headless box, or one whose display card is meant to compute). It lifts the display refusal for
        # every spelling -- display_active=Enabled included -- and a UUID that names no listed card is then honoured as
        # set (there is nothing it could wrongly reach). Without it the default stands: refuse the display card.
        opt_out = (disp == "none")
        display = set() if opt_out else {n for n, _u, a in table if a.strip().lower() == "enabled"}
        if disp is not None and disp != "none" and disp.isdigit():
            display.add(int(disp))
        reached = []
        for e in honoured:
            if e.isdigit():
                reached.append(int(e))
                continue
            hit = _uuid_to_index(e)
            if hit is None:
                if opt_out:
                    return _finish(ambient.strip(), "ambient-unchecked:display-opt-out")
                return _finish("", "ambient-uuid-unresolved-refused")
            reached.append(hit[0])
        bad = sorted(n for n in reached if n in display)
        if bad:
            return _finish("", "ambient-display-refused:%s" % ",".join(str(n) for n in bad))
        if all(e.isdigit() for e in honoured):
            return _finish(ambient.strip(), "ambient")
        return _finish(ambient.strip(), "ambient-uuid:" + ",".join(str(n) for n in reached))

    return _finish(*_declared_layers(prefix))


def _declared_layers(prefix: str = "") -> Tuple[Optional[str], str]:
    """Layers 2-5 of resolve_cuda_device() -- the DURABLE declaration of the compute card index, ambient ignored.

    Pure (no cache write), so the accelerator resolver can ask the same question for ROCm and XPU, whose ambient
    variables are not CUDA_VISIBLE_DEVICES. resolve_cuda_device() delegates here unchanged (round 1, lane L1)."""
    declared = os.environ.get(_DECLARE_KEY)
    if declared is not None and declares_no_gpu(_strip_inline_comment(declared)):
        return ("", prefix + "env:declared-no-gpu")
    if declared is not None:
        value = _strip_inline_comment(declared)
        if _valid(value):
            if value == "":
                return (None, prefix + "env:declared-empty")
            return (value, prefix + "env")
        return (PACKAGE_SAFE_DEFAULT, prefix + "degraded:env-unparseable-value")

    _ef = _read_env_file(_DECLARE_KEY, lambda v: _valid(v) or declares_no_gpu(v))
    if _ef[1] == HEALTH_OK and declares_no_gpu(_ef[0]):
        return ("", prefix + "env-file:declared-no-gpu")
    for basis, (value, health) in (("env-file", _read_env_file()),
                                   ("config-file", _read_config_file())):
        if health in DEGRADED_HEALTHS:
            # R1: SAME safe value, DIFFERENT basis. This is the whole point.
            return (PACKAGE_SAFE_DEFAULT, prefix + "degraded:" + basis + ":" + health)
        if health == HEALTH_ABSENT:
            continue
        if health == HEALTH_KEY_ABSENT:
            # A healthy file that simply does not declare the key. Legitimate,
            # and still distinguishable from "there is no file at all".
            prefix = prefix + basis + "-present-key-absent+"
            continue
        if value == "":
            # A DELIBERATE empty declaration means "do not pin" (the laptop and
            # teammate profiles ship gpu_device_index="" for CPU-only hosts).
            return (None, prefix + basis + ":declared-empty")
        return (value, prefix + basis)

    return (PACKAGE_SAFE_DEFAULT, prefix + "default")


def _finish(value: Optional[str], basis: str) -> Tuple[Optional[str], str]:
    result = (value, basis)
    _cache["resolved"] = result
    return result


def _guard_hidden_cuda_probe() -> None:
    """When every CUDA card is hidden, make torch answer "no CUDA" through NVML instead of the driver.

    Measured 2026-10-15 on WSL2 with torch 2.6.0+cu124: `torch.cuda.is_available()` dies with SIGSEGV when
    CUDA_VISIBLE_DEVICES is "" or "-1", and library code (sentence-transformers, peft inside the jina embedder) calls
    it on every load, so a CPU route crashed before the first recall. torch's own PYTORCH_NVML_BASED_CUDA_CHECK=1
    answers the same question without initialising the driver: False, no crash. Forced, never setdefault, and only
    when the visible set is empty, so a GPU route is untouched.
    """
    if hides_every_cuda_card():
        os.environ["PYTORCH_NVML_BASED_CUDA_CHECK"] = "1"


def hides_every_cuda_card(environ=None) -> bool:
    """True when CUDA_VISIBLE_DEVICES is SET and, as the driver will read it, selects NO device -- the resolver's own
    class (cuda_selects_a_device / _spells_no_device), not a list of spellings.

    Round 2 (M1): the round-1 guard matched only '' and '-1'. Measured on WSL2 + torch 2.6.0+cu124 at 6bb45ee,
    torch.cuda.is_available() also died with SIGSEGV (rc 139) for 'none', 'NoDevFiles', '-1,1' and '7' (an index
    nvidia-smi does not list) -- every value this module already reads as "no device". Unset is not hidden (the driver
    enumerates). install/tier1_probe.py asks THIS function for its child environments, so the two cannot drift."""
    env = os.environ if environ is None else environ
    if _ENV_KEY not in env:
        return False
    raw = env.get(_ENV_KEY) or ""
    if raw.strip() == "" or _spells_no_device(raw) is not None:
        return True
    first = _cuda_reading(raw)[0]
    if not first.isdigit():
        return False                      # a GPU-/MIG- UUID: trusted, as cuda_selects_a_device() trusts it
    # An index: the same membership test as cuda_selects_a_device(), read from the card table the resolver already
    # asked nvidia-smi for in this process (one call, cached) -- the pin path stays at one nvidia-smi call.
    table = _card_table()
    if table is None:
        return False                      # nvidia-smi cannot be asked: the index is trusted
    return int(first) not in {i for i, _u, _d in table}


def cpu_declaration() -> Optional[str]:
    """'declared:cpu (<layer>)' when this brain DECLARES WIZARD_ACCELERATOR=cpu (env, env file or config), else None.

    apply_cuda_pin() expresses that declaration in the process environment (M3); doctor reads it to say why the GPU
    pin is not used. A DEGRADED declaration is deliberately NOT included: the resolver answers CPU for it, but an empty
    or racing env file must not take the card away from every process (test_stranger_tier1_upgrade's honoured-pin
    contract binds this); it stays a degraded basis that doctor reports."""
    decl, basis = declared_accelerator()
    if decl == "cpu":
        return "declared:cpu (%s)" % basis
    return None


def apply_cuda_pin() -> Tuple[Optional[str], str]:
    """Resolve, then FORCE the assignment.  Call before `import torch`.

    Forced, not setdefault (#15708).  When the basis is "ambient" the variable
    already holds that exact value, so the write is a no-op and the operator's
    choice is preserved verbatim.
    """
    before = os.environ.get(_ENV_KEY)
    value, basis = resolve_cuda_device()
    if value is not None:
        os.environ[_ENV_KEY] = value
    if cpu_declaration() is not None:
        # Round 2 (M3): a CPU declaration hides EVERY card for this process. torch_device() already answers 'cpu',
        # but third-party loaders choose their own device -- peft's load_peft_weights (inside the jina embedder) takes
        # infer_device(), which initialised CUDA on the visible card under WIZARD_ACCELERATOR=cpu (traced on card 1).
        # An EMPTY value is CUDA's own "no devices"; the guard below then makes torch answer through NVML.
        # Proven for NVIDIA/CUDA only. On a ROCm build HIP reads an EMPTY CUDA_VISIBLE_DEVICES as unset (clr
        # rocdevice.cpp:391, cited above), so this does not hide an AMD card: SUPPORTED-UNPROVEN, like ROCm itself.
        os.environ[_ENV_KEY] = ""
    _guard_hidden_cuda_probe()
    if is_refused(basis) and not _cache.get("announced"):
        # VISIBLE (iteration 5): the operator set this value on purpose; say once, at the point of use, what was refused
        # and what runs instead -- never a silent CPU run.
        _cache["announced"] = True
        try:
            import sys as _sys
            _sys.stderr.write(refusal_line(before, basis) + "\n")
        except Exception:
            pass
    return value, basis


def declared_device() -> str:
    """The device this MACHINE declares, ignoring the ambient variable.

    gpu_guard, voidloom_manifest and cron/platform-detection.sh need the
    CONFIGURED compute index, not the live pin: they run as separate processes
    where CUDA_VISIBLE_DEVICES may be empty or may already have been rewritten
    to "0" by an earlier pin (torch renumbers the visible card to cuda:0).
    Rule 1 is therefore skipped and only the durable declarations are read.

    SINGLE READER (sigma-4): cron/platform-detection.sh calls THIS, so the
    shell and the Python cannot drift apart on duplicate keys, inline comments
    or a damaged file.  Its `sed` path is a fallback for a box with no python3
    and is asserted equal to this one by a minimal pair.
    """
    env = os.environ.get(_DECLARE_KEY)
    if env is not None:
        v = _strip_inline_comment(env)
        if declares_no_gpu(v):
            return "none"          # the ruling: no GPU (gpu_guard / voidloom_manifest read a non-index as "no index")
        if v != "" and _valid(v):
            return v
    _ef = _read_env_file(_DECLARE_KEY, lambda v: _valid(v) or declares_no_gpu(v))
    if _ef[1] == HEALTH_OK and declares_no_gpu(_ef[0]):
        return "none"
    for value, health in (_read_env_file(), _read_config_file()):
        if health == HEALTH_OK and value is not None and value.strip() != "":
            return value.strip()
    return PACKAGE_SAFE_DEFAULT


def _valid_display(value: str) -> bool:
    v = (value or "").strip()
    return v.isdigit() or v.lower() == "none"


def declared_display() -> Tuple[Optional[str], str]:
    """R-8 (model-choice round 1, EW5-10): the card the OWNER declared drives the monitor(s) -- WIZARD_DISPLAY_INDEX,
    recorded by the installer's GPU question (or written by hand) OUTSIDE the env file's managed block.

    Returns (value, source): value is '<index>' or 'none' when declared, else None. Read the way declared_device()
    reads its key -- the variable first (a unit receives the env file's lines as its environment), then the env file
    through install/env_file.py, the ONE reader (systemd's rules). NEVER inferred here: when nothing declares it the
    answer is (None, 'not declared') and the caller's own inference is the caller's to name. A present-but-unusable
    declaration is never mistaken for an absent one: (None, 'unusable: ...') / (None, 'degraded: ...')."""
    env = os.environ.get(_DISPLAY_KEY)
    if env is not None:
        v = _strip_inline_comment(env).strip()
        if _valid_display(v):
            return (v.lower() if v.lower() == "none" else v), "declared by %s in the environment" % _DISPLAY_KEY
        if v != "":
            return None, "unusable: %s=%r in the environment is neither a card index nor 'none'" % (_DISPLAY_KEY, env)
    value, health = _read_env_file(_DISPLAY_KEY, _valid_display)
    if health == HEALTH_OK and value is not None:
        v = value.strip()
        return (v.lower() if v.lower() == "none" else v), "declared by %s in %s" % (_DISPLAY_KEY, _env_file_path())
    if health in (HEALTH_ABSENT, HEALTH_KEY_ABSENT):
        return None, "not declared"
    if health == "unparseable-value":
        return None, "unusable: %s in %s is neither a card index nor 'none'" % (_DISPLAY_KEY, _env_file_path())
    return None, "degraded: the env file %s is %s, so the declaration cannot be read" % (_env_file_path(), health)


def health_report() -> dict:
    value, basis = resolve_cuda_device()
    return {
        "value": value,
        "basis": basis,
        "degraded": is_degraded(basis),
        "refused": is_refused(basis),
        "env_file": str(_env_file_path()),
        "env_file_health": _read_env_file()[1],
        "config_file": str(_config_file_path()),
        "config_file_health": _read_config_file()[1],
        "declared_device": declared_device(),
        "package_safe_default": PACKAGE_SAFE_DEFAULT,
    }


def describe() -> str:
    value, basis = resolve_cuda_device()
    shown = "<unset>" if value is None else value
    return "CUDA_VISIBLE_DEVICES=" + shown + " (basis: " + basis + ")"


# =====================================================================================================================
# THE ACCELERATOR  (Voidloom handoff round 1, lane L1 -- frozen contracts C1 and C2)
# =====================================================================================================================
# Before this section every organ answered "which device?" on its own line -- `"cuda:0" if _cuda_usable(torch) else
# "cpu"`, ten times in eight files -- so a Mac, an AMD card or an Intel GPU could only ever be CPU, and nothing said
# so. This is now the ONE answer every organ asks.
#
#   C1  resolve_accelerator() -> (backend, torch_device, basis, display_shared, ram_bytes)
#       torch_device() -> str                     ('cuda:0' | 'mps' | 'xpu:0' | 'cpu')
#   C2  WIZARD_ACCELERATOR in {auto, cuda, rocm, mps, xpu, cpu}; writers write only 'auto' or 'cpu'.
#
# TWO STAGES, BECAUSE ASKING A RUNTIME INITIALISES IT.
#   resolve_accelerator()             stdlib only, never imports torch, never touches the environment. It reads the
#                                     declaration, the ambient pin variables, nvidia-smi (out of process), the
#                                     platform, sysfs and RAM. Safe before `import torch`.
#   torch_device() / resolve_accelerator(confirm=True)
#                                     applies the backend's pin (forced, never setdefault -- #15708), THEN imports
#                                     torch and asks the backend's own API. CUDA, HIP and Level Zero read their
#                                     visibility variables when they initialise, so the pin must land first.
#   A backend the stdlib stage cannot see well enough to trust -- an AMD or Intel GPU, whose usability depends on a
#   wheel the owner chose deliberately -- is a CANDIDATE there and becomes the backend only when torch confirms it.
#   Anything degraded, refused or unconfirmed answers 'cpu' and says why in the basis (C1).
#
# PROOF LABELS (FABLE_PLAN §4). CUDA and CPU are PROVEN on the reference host. MPS, ROCm and Intel XPU are
# SUPPORTED-UNPROVEN: the code paths below are exercised by fakes and by a real-torch attribute test, never on that
# silicon. DirectML is a CANDIDATE row only -- C2 has no value for it (see directml_probe()).
#
# SOURCES (cited in the round's log): PyTorch HIP notes (torch.cuda reused on ROCm; device string 'cuda';
# torch.version.hip tells the builds apart); ROCm "GPU isolation techniques" (ROCR_/HIP_/CUDA_VISIBLE_DEVICES);
# ROCm clr rocdevice.cpp:391 (HIP reads HIP_VISIBLE_DEVICES when NON-EMPTY, else CUDA_VISIBLE_DEVICES -- so an empty
# value hides nothing on ROCm); PyTorch "Getting Started on Intel GPU" (torch.xpu, device 'xpu', whl/xpu); the
# Level Zero spec, Environment Variables > Affinity Mask (ZE_AFFINITY_MASK = comma-separated device[.sub] ordinals);
# PyTorch MPS notes (torch.backends.mps.is_available / is_built, device 'mps').

__all__ += [
    "ACCEL_KEY", "ACCELERATOR_VALUES", "ACCEL_WRITER_VALUES", "BACKENDS", "TORCH_DEVICE", "CODE_LABELS",
    "PIN_VARS", "TORCH_ATTRS_READ", "declared_accelerator", "resolve_accelerator", "torch_device",
    "apply_accelerator_pin", "accelerator_report", "backend_label", "ram_bytes", "directml_probe", "reset_cache",
    "empty_cache", "is_out_of_memory", "PROOF_LABEL_WORDS", "proof_label_for", "hides_every_cuda_card",
    "cpu_declaration",
]

ACCEL_KEY = "WIZARD_ACCELERATOR"
_ACCEL_CONFIG_KEY = "accelerator"
ACCELERATOR_VALUES = ("auto", "cuda", "rocm", "mps", "xpu", "cpu")
ACCEL_WRITER_VALUES = ("auto", "cpu")          # C2: the only values an installer or tool may WRITE
BACKENDS = ("cuda", "rocm", "mps", "xpu", "cpu")
TORCH_DEVICE = {"cuda": "cuda:0", "rocm": "cuda:0", "mps": "mps", "xpu": "xpu:0", "cpu": "cpu"}
# What the code claims ON LINUX, the only OS any row was proven on; backend_label() answers SUPPORTED-UNPROVEN for
# every backend elsewhere (round 2, M2 -- the same rows as install/accelerator_plan.PROOF_LABELS).
CODE_LABELS = {"cuda": "PROVEN", "cpu": "PROVEN", "mps": "SUPPORTED-UNPROVEN",
               "rocm": "SUPPORTED-UNPROVEN", "xpu": "SUPPORTED-UNPROVEN"}
PROOF_LABEL_WORDS = ("PROVEN", "PROVEN-BY-PROXY", "SUPPORTED-UNPROVEN", "FAILED-ON-THIS-MACHINE")   # contract C4
# The visibility variables each runtime reads, in the order that runtime gives them precedence.
PIN_VARS = {"cuda": ("CUDA_VISIBLE_DEVICES",),
            "rocm": ("HIP_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"),
            "xpu": ("ZE_AFFINITY_MASK",), "mps": (), "cpu": ()}
_PIN_WRITE = {"cuda": "CUDA_VISIBLE_DEVICES", "rocm": "HIP_VISIBLE_DEVICES", "xpu": "ZE_AFFINITY_MASK"}
_VENDOR_PCI = {"rocm": "0x1002", "xpu": "0x8086"}   # AMD, Intel (PCI vendor ids as sysfs prints them)

# Observation points, module constants so a test can point them at a fake tree (as NVIDIA_SMI_DIRS above).
SYSFS_DRM = "/sys/class/drm"
DEV_KFD = "/dev/kfd"                               # the ROCm kernel driver's device node
MEMINFO_PATH = "/proc/meminfo"

# EVERY torch attribute this module reads, as a dotted path from the torch module. Each is read ONLY through
# _torch_attr(), which answers None when the installed torch lacks it -- the real-torch attribute test binds this
# tuple to the source and to the torch actually installed.
# cuda_available() (R-6, above) reads cuda.is_available directly inside its try: the one sanctioned exception, kept so
# voidloom/tests/test_cuda_no_device_guard.py still finds the decider by AST.
TORCH_ATTRS_READ = ("version.hip", "cuda.is_available", "backends.mps.is_available", "xpu.is_available",
                    "cuda.empty_cache", "mps.empty_cache", "xpu.empty_cache")
_EMPTY_CACHE = {"cuda": "cuda.empty_cache", "rocm": "cuda.empty_cache", "mps": "mps.empty_cache",
                "xpu": "xpu.empty_cache"}

_accel: dict = {}


def reset_cache() -> None:
    """Forget every per-process answer (the CUDA resolution, nvidia-smi's tables, the accelerator)."""
    for c in (_cache, _listed_cache, _cards_cache, _accel):
        c.clear()


def _platform() -> Tuple[str, str]:
    import platform as _pf
    try:
        return (_pf.system() or "unknown"), (_pf.machine() or "unknown").lower()
    except Exception:
        return "unknown", "unknown"


def _torch_attr(mod, path: str):
    """getattr down a dotted path; None at the first missing link. The ONLY way this module reads torch."""
    cur = mod
    for part in path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def _call(fn):
    try:
        return fn() if callable(fn) else None
    except Exception:
        return None


def ram_bytes() -> Optional[int]:
    """Physical RAM as the OS presents it, stdlib only; None when it cannot be read (never a guess).

    Linux: /proc/meminfo MemTotal. macOS: sysctl hw.memsize. Windows: GlobalMemoryStatusEx. Else sysconf."""
    system, _m = _platform()
    if system == "Linux":
        try:
            with open(MEMINFO_PATH, "r", encoding="ascii", errors="ignore") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            return int(parts[1]) * 1024
        except OSError:
            pass
    elif system == "Darwin":
        try:
            import ctypes
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            size = ctypes.c_uint64(0)
            ln = ctypes.c_size_t(ctypes.sizeof(size))
            if libc.sysctlbyname(b"hw.memsize", ctypes.byref(size), ctypes.byref(ln), None, ctypes.c_size_t(0)) == 0:
                return int(size.value)
        except Exception:
            pass
    elif system == "Windows":
        try:
            import ctypes

            class _MemStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _MemStatus()
            st.dwLength = ctypes.sizeof(_MemStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return int(st.ullTotalPhys)
        except Exception:
            pass
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):
        return None


def _valid_accel(value: str) -> bool:
    return (value or "").strip().lower() in ACCELERATOR_VALUES + ("",)


def declared_accelerator() -> Tuple[Optional[str], str]:
    """(value, basis): WIZARD_ACCELERATOR read through the same layers as WIZARD_CUDA_DEVICE -- the environment (the
    surface a unit receives), the env file LAST-WINS through install/env_file.py (R2), wizard-config.json's
    'accelerator', then the default 'auto'. An exported-but-empty value is unset. A present but unusable value is
    (None, 'degraded:...') -- never mistaken for 'auto' (R1), and the resolver answers CPU for it (C1)."""
    env = os.environ.get(ACCEL_KEY)
    if env is not None:
        v = _strip_inline_comment(env).strip().lower()
        if v in ACCELERATOR_VALUES:
            return v, "env"
        if v != "":
            return None, "degraded:env-unparseable-value:%r" % env
    value, health = _read_env_file(ACCEL_KEY, _valid_accel)
    if health == HEALTH_OK and (value or "").strip():
        return value.strip().lower(), "env-file"
    if health in DEGRADED_HEALTHS:
        return None, "degraded:env-file:%s" % health
    path = _config_file_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(errors="ignore"))
            if isinstance(data, dict) and data.get(_ACCEL_CONFIG_KEY) not in (None, ""):
                v = str(data.get(_ACCEL_CONFIG_KEY)).strip().lower()
                if v in ACCELERATOR_VALUES:
                    return v, "config-file"
                return None, "degraded:config-file:unparseable-value"
    except OSError:
        return None, "degraded:config-file:unreadable"
    except ValueError:
        pass                                     # an unparseable config is the CUDA layer's degradation to report
    return "auto", "default"


def _drm_devices(vendor: str) -> Optional[List[dict]]:
    """The DRM cards whose PCI vendor is ``vendor`` (sysfs, a kernel ABI -- not a tool's output format); None when
    sysfs cannot be read. Each: {'card': 'cardN', 'boot_vga': True/False/None}. Used for COUNTS and corroboration only:
    sysfs order is not a runtime's device order, so no index is ever taken from here."""
    try:
        names = sorted(n for n in os.listdir(SYSFS_DRM) if n.startswith("card") and n[4:].isdigit())
    except OSError:
        return None
    out = []
    for n in names:
        dev = os.path.join(SYSFS_DRM, n, "device")
        try:
            with open(os.path.join(dev, "vendor"), "r", encoding="ascii", errors="ignore") as fh:
                if fh.read().strip().lower() != vendor:
                    continue
        except OSError:
            continue
        boot = None
        try:
            with open(os.path.join(dev, "boot_vga"), "r", encoding="ascii", errors="ignore") as fh:
                boot = fh.read().strip() == "1"
        except OSError:
            pass
        out.append({"card": n, "boot_vga": boot})
    return out


def _first_entry(raw: str) -> str:
    return (raw or "").split(",")[0].strip()


def _ambient_pin(backend: str) -> Tuple[str, Optional[str], Optional[str]]:
    """(state, variable, value) for a ROCm or XPU ambient pin: state 'unset' | 'no-device' | 'selects'.

    ROCm (clr rocdevice.cpp:391): HIP reads HIP_VISIBLE_DEVICES when it is NON-EMPTY, else CUDA_VISIBLE_DEVICES; an
    empty value is the runtime's 'unset' and would expose EVERY card. Voidloom's own rule (#20154) is stricter: an
    operator who exports CUDA_VISIBLE_DEVICES='' declared no device, so that is 'no-device' here and the resolver
    answers CPU -- the runtime is never relied on to hide a card it does not hide. ROCR_VISIBLE_DEVICES filters below
    HIP (indices or UUIDs); honoured as set. XPU: ZE_AFFINITY_MASK, device[.subdevice] ordinals; empty = unset (spec)."""
    env = os.environ
    if backend == "rocm":
        hip = env.get("HIP_VISIBLE_DEVICES")
        if hip is not None and hip.strip():
            f = _first_entry(hip)
            return ("selects" if f.isdigit() else "no-device"), "HIP_VISIBLE_DEVICES", hip.strip()
        cvd = env.get("CUDA_VISIBLE_DEVICES")
        if cvd is not None:
            if _spells_no_device(cvd) is not None:
                return "no-device", "CUDA_VISIBLE_DEVICES", cvd
            return "selects", "CUDA_VISIBLE_DEVICES", cvd.strip()
        rocr = env.get("ROCR_VISIBLE_DEVICES")
        if rocr is not None and rocr.strip():
            f = _first_entry(rocr)
            return ("selects" if (f.isdigit() or f.startswith("GPU-")) else "no-device"), "ROCR_VISIBLE_DEVICES", rocr
        return "unset", None, None
    if backend == "xpu":
        ze = env.get("ZE_AFFINITY_MASK")
        if ze is not None and ze.strip():
            f = _first_entry(ze)
            ok = all(p.isdigit() for p in f.split(".")) and 1 <= len(f.split(".")) <= 2
            return ("selects" if ok else "no-device"), "ZE_AFFINITY_MASK", ze.strip()
        return "unset", None, None
    return "unset", None, None


def _result(backend, basis, *, display_shared=False, pin=None, candidates=(), notes=(), declared=None,
            ram=None, confirmed=None) -> dict:
    return {"backend": backend, "torch_device": TORCH_DEVICE[backend], "basis": basis,
            "display_shared": bool(display_shared), "ram_bytes": ram, "label": backend_label(backend),
            "degraded": "degraded:" in basis, "refused": "refused" in basis, "pin": pin,
            "candidates": list(candidates), "notes": list(notes), "declared": declared, "confirmed": confirmed}


def _cpu(basis, **kw) -> dict:
    return _result("cpu", basis, **kw)


def _plan_cuda(why: str, kw: dict) -> dict:
    value, basis = resolve_cuda_device()
    if value is None and basis in ("ambient-empty", "ambient-no-device"):
        return _cpu("%s; CUDA_VISIBLE_DEVICES selects no device (%s)" % (why, basis), **kw)
    if is_refused(basis):
        return _cpu("%s; refused: the CUDA pin was refused (%s)" % (why, basis), **kw)
    if is_degraded(basis):
        return _cpu("%s; degraded:cuda-index:%s" % (why, basis), **kw)
    if value == "":
        return _cpu("%s; the compute card declares no GPU (%s)" % (why, basis), **kw)
    sel, swhy = cuda_selects_a_device(dict(os.environ, **{_ENV_KEY: value}) if value is not None else None)
    if not sel:
        return _cpu("%s; %s" % (why, swhy), **kw)
    first = _first_entry(value) if value else ""
    table = _card_table()
    disp, _src = declared_display()
    if disp == "none":
        shared = False
    elif first.isdigit() and disp is not None and disp.isdigit():
        shared = first == disp
    elif table and first.isdigit():
        hit = [a for i, _u, a in table if i == int(first)]
        shared = bool(hit) and hit[0].strip().lower() == "enabled"
    else:
        shared = True                    # cannot be told: say the display may be shared
    return _result("cuda", "%s; card %s (%s)" % (why, value if value is not None else "<all>", basis),
                   display_shared=shared, pin={"var": _ENV_KEY, "value": value, "basis": basis}, **kw)


def _plan_indexed(backend: str, why: str, kw: dict) -> dict:
    """ROCm / XPU: the pin, the display rule and the multi-device refusal (FABLE_PLAN §3 A-core).

    The compute index is the one the owner declares in WIZARD_CUDA_DEVICE (layers 2-5, the same reader): it names
    the compute card on every GPU backend, and each runtime takes it in its own visibility variable. Two or more
    devices with NOTHING declared: refuse to pick (the installer already refuses to guess on multi-GPU NVIDIA). An
    AMBIENT pin that reaches the declared display card on a multi-device host: refused, as for CUDA. A declared
    compute index equal to the declared display index is the owner's typed confirmation and is honoured."""
    devs = _drm_devices(_VENDOR_PCI[backend])
    count = len(devs) if devs is not None else None
    disp, _src = declared_display()
    var = _PIN_WRITE[backend]
    state, avar, araw = _ambient_pin(backend)
    if state == "no-device":
        return _cpu("%s; %s=%r selects no device" % (why, avar, araw), **kw)
    if state == "selects":
        first = _first_entry(araw).split(".")[0]
        if disp is not None and disp.isdigit() and first == disp and count is not None and count >= 2:
            return _cpu("%s; refused: %s=%s reaches device %s, the declared display, on a host with %d devices"
                        % (why, avar, araw, disp, count), **kw)
        shared = (first == disp) if (disp or "").isdigit() else (disp != "none" and count in (None, 1))
        return _result(backend, "%s; ambient %s=%s honoured" % (why, avar, araw), display_shared=shared,
                       pin={"var": avar, "value": araw, "basis": "ambient", "write": False}, **kw)
    value, ibasis = _declared_layers()
    if value == "":
        return _cpu("%s; the compute card declares no GPU (%s)" % (why, ibasis), **kw)
    if is_degraded(ibasis):
        return _cpu("%s; degraded:compute-index:%s" % (why, ibasis), **kw)
    undeclared = value is None or ibasis.endswith("default")
    if undeclared and count is not None and count >= 2 and disp != "none":
        return _cpu("%s; refused: %d %s devices and no compute index declared (declare WIZARD_CUDA_DEVICE, or "
                    "WIZARD_DISPLAY_INDEX=none on a headless host)" % (why, count, backend), **kw)
    first = _first_entry(value).split(".")[0] if value else "0"
    if (disp or "").isdigit():
        shared = first == disp
    else:
        shared = disp != "none" and count in (None, 1)
    notes = []
    if count is None:
        notes.append("device count not observable through sysfs; the display rule could not be checked")
    return _result(backend, "%s; device %s (%s)" % (why, value if value is not None else "<all>", ibasis),
                   display_shared=shared, pin={"var": var, "value": value, "basis": ibasis, "write": value is not None},
                   notes=notes, **kw)


def _plan(backend: str, why: str, kw: dict) -> dict:
    system, machine = _platform()
    if backend == "cuda":
        return _plan_cuda(why, kw)
    if backend == "mps":
        if system != "Darwin":
            return _cpu("%s; degraded:declared-unavailable: MPS exists only on macOS (this is %s)" % (why, system), **kw)
        return _result("mps", why, display_shared=True,
                       notes=["Apple unified memory: the GPU shares RAM with the desktop; MPS's own cap is "
                              "PYTORCH_MPS_HIGH_WATERMARK_RATIO (left at its default)"], **kw)
    if backend == "rocm" and system != "Linux":
        return _cpu("%s; degraded:declared-unavailable: ROCm wheels are resolved for Linux only (this is %s)"
                    % (why, system), **kw)
    if backend == "xpu" and system not in ("Linux", "Windows"):
        return _cpu("%s; degraded:declared-unavailable: Intel XPU wheels exist for Linux and Windows (this is %s)"
                    % (why, system), **kw)
    return _plan_indexed(backend, why, kw)


def _observe() -> Tuple[str, str, List[str]]:
    """Under 'auto': (backend, why, candidates) from what stdlib can observe. NVIDIA is observed through nvidia-smi
    (tested here); Apple Silicon by platform; an AMD or Intel GPU only as a CANDIDATE for torch to confirm."""
    system, machine = _platform()
    listed = _listed_indices()
    if listed:
        return "cuda", "auto:nvidia-smi lists card(s) %s" % " ".join(str(i) for i in listed), ["cuda"]
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "mps", "auto:Apple Silicon", ["mps"]
        return ("cpu", "auto:Intel Mac -- MPS only if torch confirms it (PyTorch's last x86_64 macOS wheels are the "
                "2.2 line)", ["mps"])
    cands = []
    seen = []
    if system == "Linux":
        if os.path.exists(DEV_KFD) or _drm_devices(_VENDOR_PCI["rocm"]):
            cands.append("rocm")
            seen.append("AMD GPU (%s)" % (DEV_KFD if os.path.exists(DEV_KFD) else "sysfs"))
        if _drm_devices(_VENDOR_PCI["xpu"]):
            cands.append("xpu")
            seen.append("Intel GPU (sysfs)")
    if cands:
        return "cpu", "auto:cpu until torch confirms a candidate (%s)" % ", ".join(seen), cands
    return "cpu", "auto:no accelerator observed", []


def _resolve_stdlib() -> dict:
    ram = ram_bytes()
    decl, dbasis = declared_accelerator()
    kw = {"ram": ram, "declared": {"value": decl, "basis": dbasis}}
    if decl is None:
        return _cpu("degraded:accelerator:%s" % dbasis, **kw)
    if decl == "cpu":
        return _cpu("declared:cpu (%s)" % dbasis, **kw)
    _v, cbasis = resolve_cuda_device()
    if "declared-no-gpu" in cbasis:
        if decl == "auto":
            return _cpu("declared-no-gpu (%s)" % cbasis, **kw)
        return _cpu("degraded:conflict: %s=%s beside a no-GPU WIZARD_CUDA_DEVICE (%s)" % (ACCEL_KEY, decl, cbasis),
                    **kw)
    if decl == "auto":
        backend, why, cands = _observe()
        if backend == "cpu":
            # The ONE promotable answer: nothing declared, nothing refused, nothing observed that torch must not
            # override. Every other CPU answer is final (a no-device pin is never second-guessed).
            return dict(_cpu(why, candidates=cands, **kw), promotable=True)
        return _plan(backend, why, dict(kw, candidates=cands))
    return _plan(decl, "declared:%s (%s)" % (decl, dbasis), dict(kw, candidates=[decl]))


def _as_tuple(r: dict) -> Tuple[str, str, str, bool, Optional[int]]:
    return r["backend"], r["torch_device"], r["basis"], r["display_shared"], r["ram_bytes"]


def _apply_pin(r: dict) -> None:
    """Force the planned pin (never setdefault). CUDA goes through apply_cuda_pin(), unchanged."""
    if r["backend"] == "cuda":
        apply_cuda_pin()
        return
    pin = r.get("pin") or {}
    if pin.get("write") and pin.get("value") is not None:
        # Spelled out per variable, never os.environ[<expr>]: tools/gpu_pin_gate.py inventories every pin EMITTER by
        # its literal key and proves a literal edit of each would be caught.
        if pin["var"] == "HIP_VISIBLE_DEVICES":
            os.environ["HIP_VISIBLE_DEVICES"] = pin["value"]
        elif pin["var"] == "ZE_AFFINITY_MASK":
            os.environ["ZE_AFFINITY_MASK"] = pin["value"]


def apply_accelerator_pin() -> Tuple[str, str, str, bool, Optional[int]]:
    """The stdlib resolution, with its pin APPLIED. Call before `import torch`, in place of apply_cuda_pin() --
    which it still calls first, so every CUDA behaviour is byte-identical."""
    apply_cuda_pin()
    r = _accel.get("confirmed") or _accel.get("stdlib")
    if r is None:
        r = _accel["stdlib"] = _resolve_stdlib()
    _apply_pin(r)
    return _as_tuple(r)


def _import_torch(torch_mod):
    if torch_mod is not None:
        return torch_mod
    try:
        import importlib
        import importlib.util
        if importlib.util.find_spec("torch") is None:
            return None
        return importlib.import_module("torch")
    except Exception:
        return None


def _xpu_ok(torch) -> Tuple[bool, str]:
    if _call(_torch_attr(torch, "xpu.is_available")):
        return True, "torch.xpu.is_available()"
    try:
        import importlib.util
        if importlib.util.find_spec("intel_extension_for_pytorch") is not None:
            import importlib
            importlib.import_module("intel_extension_for_pytorch")   # registers torch.xpu on pre-2.5 torch
            if _call(_torch_attr(torch, "xpu.is_available")):
                return True, "torch.xpu.is_available() after intel_extension_for_pytorch"
    except Exception:
        pass
    return False, "torch.xpu.is_available() is not True"


def _backend_ok(backend: str, torch) -> Tuple[bool, str]:
    hip = _torch_attr(torch, "version.hip")
    if backend == "cuda":
        if hip:
            return False, "torch is a ROCm build (torch.version.hip=%s)" % hip
        return cuda_available(torch), "torch.cuda.is_available() via cuda_available()"
    if backend == "rocm":
        if not hip:
            return False, "torch is not a ROCm build (torch.version.hip is unset)"
        return cuda_available(torch), "torch.cuda.is_available() via cuda_available() on HIP %s" % hip
    if backend == "mps":
        return bool(_call(_torch_attr(torch, "backends.mps.is_available"))), "torch.backends.mps.is_available()"
    if backend == "xpu":
        return _xpu_ok(torch)
    return True, "cpu"


def _confirm(r: dict, torch_mod=None) -> dict:
    """Stage two: apply the pin, then ask the backend's own API. Unconfirmed -> 'cpu', said in the basis."""
    if r["backend"] != "cpu":
        _apply_pin(r)
        torch = _import_torch(torch_mod)
        if torch is None:
            return dict(_cpu(r["basis"] + "; torch is not importable", ram=r["ram_bytes"], declared=r["declared"],
                             candidates=r["candidates"]), confirmed=False)
        ok, how = _backend_ok(r["backend"], torch)
        if ok:
            return dict(r, confirmed=True, basis=r["basis"] + "; confirmed by " + how)
        return dict(_cpu(r["basis"] + "; unconfirmed: " + how, ram=r["ram_bytes"], declared=r["declared"],
                         candidates=r["candidates"]), confirmed=False)
    # A CPU answer is promoted only under 'auto', and only when nothing declared, refused or degraded it.
    if not r.get("promotable"):
        return dict(r, confirmed=None)
    torch = _import_torch(torch_mod)
    if torch is None:
        return dict(r, confirmed=None)
    system, _m = _platform()
    order = []
    if _torch_attr(torch, "version.hip"):
        order.append("rocm")
    if system in ("Linux", "Windows"):
        order.append("xpu")
    if system == "Darwin":
        order.append("mps")
    kw = {"ram": r["ram_bytes"], "declared": r["declared"], "candidates": r["candidates"]}
    for cand in order:
        plan = _plan(cand, "auto:torch build offers %s" % cand, dict(kw))
        if plan["backend"] != cand:
            continue
        _apply_pin(plan)
        ok, how = _backend_ok(cand, torch)
        if ok:
            return dict(plan, confirmed=True, basis=plan["basis"] + "; confirmed by " + how)
    return dict(r, confirmed=None)


def resolve_accelerator(confirm: bool = False, torch_mod=None) -> Tuple[str, str, str, bool, Optional[int]]:
    """C1. (backend, torch_device, basis, display_shared, ram_bytes).

    confirm=False (default): stdlib only -- no torch import, no environment write; safe before `import torch`. Once a
    confirmation has run in this process, that answer is returned.
    confirm=True: also APPLIES the pin and asks torch (what torch_device() does). Never raises."""
    try:
        if confirm:
            if "confirmed" not in _accel:
                base = _accel.get("stdlib") or _resolve_stdlib()
                _accel["stdlib"] = base
                _accel["confirmed"] = _confirm(base, torch_mod)
            return _as_tuple(_accel["confirmed"])
        if "confirmed" in _accel:
            return _as_tuple(_accel["confirmed"])
        if "stdlib" not in _accel:
            _accel["stdlib"] = _resolve_stdlib()
        return _as_tuple(_accel["stdlib"])
    except Exception as exc:                      # the resolver never takes an organ down
        r = _cpu("degraded:resolver-fault:%s" % type(exc).__name__)
        _accel.setdefault("stdlib", r)
        return _as_tuple(r)


def torch_device(torch_mod=None) -> str:
    """C1. The device string every organ hands to torch: 'cuda:0' | 'mps' | 'xpu:0' | 'cpu'.

    Applies the backend's pin, imports torch lazily and asks the backend's own API; 'cpu' on any degraded, refused or
    unconfirmed basis. Call it where the organ used to write `"cuda:0" if _cuda_usable(torch) else "cpu"`."""
    return resolve_accelerator(confirm=True, torch_mod=torch_mod)[1]


def empty_cache(torch_mod=None) -> bool:
    """Release the resolved backend's cached allocator blocks (the OOM retry and the every-N-batches hygiene that used
    to call torch.cuda.empty_cache() whatever the device). True when a cache was released; never raises."""
    backend = resolve_accelerator()[0]
    path = _EMPTY_CACHE.get(backend)
    if path is None:
        return False
    torch = _import_torch(torch_mod)
    fn = _torch_attr(torch, path) if torch is not None else None
    if not callable(fn):
        return False
    _call(fn)
    return True


def is_out_of_memory(exc: BaseException) -> bool:
    """An allocator OOM on ANY backend ('CUDA out of memory', 'MPS backend out of memory', 'XPU out of memory'...)."""
    return isinstance(exc, MemoryError) or "out of memory" in str(exc).lower()


def backend_label(backend: str, osname: Optional[str] = None, arch: Optional[str] = None) -> str:
    """The proof label the CODE can claim for a backend ON THIS OS (an install's own record may say more -- C4).

    Keyed by (backend, OS[, arch]) exactly as install/accelerator_plan.PROOF_LABELS: PROVEN on Linux for cuda and cpu
    (the reference host and its clean room), and on an Apple Silicon Mac for mps and cpu (installed and used end to
    end, owner-witnessed, 2026-10). An Intel Mac, Windows and every other OS read SUPPORTED-UNPROVEN until evidence
    moves the row. osname and arch default to this machine's."""
    sysname, machine = _platform()
    osname = osname or sysname
    a = (arch if arch is not None else (machine if osname == sysname else "")).strip().lower()
    if osname == "Darwin":
        if backend in ("mps", "cpu") and a in ("arm64", "aarch64"):    # Apple Silicon; an Intel Mac or an
            return "PROVEN"                                            # unknown chip claims nothing
        return "SUPPORTED-UNPROVEN"
    if osname != "Linux":
        return "SUPPORTED-UNPROVEN"
    return CODE_LABELS.get(backend, "SUPPORTED-UNPROVEN")


def proof_label_for(backend: str, recorded_label: Optional[str] = None, recorded_backend: Optional[str] = None,
                    osname: Optional[str] = None, failed_backend: Optional[str] = None) -> str:
    """The ONE proof label a reader (doctor, the session banner, detect) prints for ``backend`` on this machine.

    The install's own record (INSTALL_RECORD ``proof_label=`` / ``accelerator=`` / ``accelerator_failed=``) wins only
    when the label is a C4 word AND it describes this same backend. An EMPTY label (an Intel Mac's record) is absent.
    FAILED-ON-THIS-MACHINE describes ``failed_backend`` when the record names one: after the installer's MPS->CPU flip
    the record says accelerator=cpu, proof_label=FAILED-ON-THIS-MACHINE, accelerator_failed=mps, and the label belongs
    to mps, never printed beside 'cpu'. Otherwise the code's OS-aware claim, backend_label(backend, osname).
    Stdlib only; never raises."""
    try:
        b = (backend or "").strip().lower()
        rl = (recorded_label or "").strip()
        rb = (recorded_backend or "").strip().lower()
        fb = (failed_backend or "").strip().lower()
        if rl == "FAILED-ON-THIS-MACHINE" and fb:
            rb = fb
        if rl in PROOF_LABEL_WORDS and (not rb or rb == b):
            return rl
        return backend_label(backend, osname)
    except Exception:
        return "SUPPORTED-UNPROVEN"


def accelerator_report(confirm: bool = False, torch_mod=None) -> dict:
    """Everything the resolver decided and why -- for doctor, the banner and the installer's dry run."""
    resolve_accelerator(confirm=confirm, torch_mod=torch_mod)
    r = dict(_accel.get("confirmed") or _accel.get("stdlib") or {})
    r["ram_gib"] = round(r["ram_bytes"] / float(1 << 30), 2) if r.get("ram_bytes") else None
    r["accelerator_key"] = ACCEL_KEY
    return r


def directml_probe(dml_mod=None) -> dict:
    """DirectML (torch-directml, Windows): a CANDIDATE row, never a resolver backend in round 1.

    C2 freezes WIZARD_ACCELERATOR to {auto,cuda,rocm,mps,xpu,cpu}, and torch-directml's newest wheel (0.2.5.dev240914,
    PyPI) lags torch. API per Microsoft Learn "Enable PyTorch with DirectML on Windows": torch_directml.device();
    device_count()/default_device() are read through getattr and may be absent."""
    out = {"backend": "directml", "label": "SUPPORTED-UNPROVEN", "selectable": False, "present": False,
           "count": None, "device": None,
           "why": "candidate row: C2 has no directml value; enabling it is a contract change for a later round"}
    mod = dml_mod
    if mod is None:
        try:
            import importlib.util
            if importlib.util.find_spec("torch_directml") is None:
                return out
            import importlib
            mod = importlib.import_module("torch_directml")
        except Exception:
            return out
    out["present"] = True
    cnt = _call(getattr(mod, "device_count", None))
    out["count"] = cnt if isinstance(cnt, int) else None
    dev = _call(getattr(mod, "device", None))
    out["device"] = str(dev) if dev is not None else None
    return out


if __name__ == "__main__":
    import sys
    argv = sys.argv[1:]
    if "--declared" in argv:
        print(declared_device())
    elif "--accelerator" in argv:
        print(json.dumps(accelerator_report(confirm="--confirm" in argv), indent=2, sort_keys=True))
    elif "--json" in argv or "--health" in argv:
        print(json.dumps(health_report(), indent=2))
    else:
        print(describe())
