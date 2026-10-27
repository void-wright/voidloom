#!/usr/bin/env python3
"""accelerator_plan.py -- what the installer will do with THIS machine's compute, decided once, said in plain words.

Handoff round 1, lane L4 (the compute-agnostic install route). STDLIB ONLY, python >= 3.6 syntax, callable before
any torch exists: install.sh runs it with the system python on a fresh machine to choose the torch wheel CHANNEL
before the venv has a torch to ask.

It answers four questions, one function each, and `plan()` puts them together:

  1. backend   which accelerator this machine has -- through the resolver every organ asks (contract C1,
               organs/voidloom_device.resolve_accelerator, lane L1). A package that does not carry C1 yet is
               served by `_stub_resolve()`, which keeps C1's frozen signature exactly and observes only what a
               stdlib process can: nvidia-smi for NVIDIA, Apple Silicon by platform, /dev/kfd for ROCm. It never
               guesses an Intel GPU (every Intel laptop has an iGPU; XPU is requested, not inferred).
  2. channel   where the torch wheel for that backend comes from (TORCH_CHANNELS; every row cites the official
               page it was read from and was RESOLVED on 2026-10-15 -- see docs/ONBOARDING_GUIDE.md, "Which torch").
  3. label     how proven that backend is (PROOF_LABELS, contract C4's vocabulary). A label is promoted only by
               evidence read by a human: Mac CI, the friend's preflight.json, an INSTALL_RECORD.
  4. reranker  the RAM gate (Drew's ruling, 2026-10-14): ON only when a non-CPU accelerator is present AND MemTotal
               >= 15.0 GiB (the RAM a 16 GB machine presents). Never on CPU-only. Contract C3 is the shape it is
               recorded in (wizard-config.json: reranker_enabled + reranker_gate).

CLI (every verb prints ONE JSON object on stdout):
    accelerator_plan.py plan --organs DIR [--request auto|cuda|rocm|mps|xpu|cpu] [--python X.Y]
                             [--os Linux|Darwin] [--arch x86_64|arm64] [--reranker-override on|off|""]
        rc 0 a plan; rc 2 REFUSED (the request contradicts the machine, or no torch wheel exists for it) -- the
        JSON's `refusal` says why in a sentence the owner can act on; rc 64 usage.
    accelerator_plan.py fields < PLAN.json
        the plan as KEY<TAB>VALUE lines, for install.sh to read with `read` (no eval)
    accelerator_plan.py on-proof-failure --backend B --reason TEXT [--owner-line VALUE]
        what install.sh does when P6/P7 fail on B (flip | keep-owner | fail), as KEY<TAB>VALUE lines
    accelerator_plan.py write-gate --config PATH --plan FILE|- [--dry-run]
        merge reranker_enabled + reranker_gate (C3) into wizard-config.json, every other key kept; backup before
        (PATH.bak), atomic replace, read back. rc 0 written (or would write); rc 3 the file is unreadable JSON
        (NOTHING written -- a config is never clobbered); rc 64 usage.
"""
from __future__ import print_function

import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True          # never leave a __pycache__ in the package this runs from

BACKENDS = ("cuda", "rocm", "mps", "xpu", "cpu")
REQUESTS = ("auto",) + BACKENDS
THRESHOLD_GIB = 15.0                    # contract C3: threshold_gib (Drew's ruling 2 in SYNTHESIS §0)
GIB = 1024.0 ** 3

# contract C4's vocabulary. The words are the product's: doctor, the README table and the installer print them.
PROVEN = "PROVEN"
PROVEN_BY_PROXY = "PROVEN-BY-PROXY"
UNPROVEN = "SUPPORTED-UNPROVEN"
FAILED_HERE = "FAILED-ON-THIS-MACHINE"
LABELS = (PROVEN, PROVEN_BY_PROXY, UNPROVEN, FAILED_HERE)

# (backend, os) -> the label the README prints. Only evidence moves a row: PROVEN = our battery executed on that
# hardware; PROVEN-BY-PROXY = a hosted runner or a governed canary whose record a human read; else UNPROVEN.
# A row may carry the machine architecture as a third key; proof_label() tries (backend, os, arch) first.
PROOF_LABELS = {
    ("cuda", "Linux"): PROVEN,          # the reference host, card 1, every round
    ("cpu", "Linux"): PROVEN,           # the 2026-10-14 clean room (venv, whl/cpu torch, nomic-768)
    ("mps", "Darwin", "arm64"): PROVEN, # an Apple Silicon Mac installed and used end to end (owner-witnessed,
                                        # 2026-10), after the macOS-15 arm64 CI probe ran green
    ("cpu", "Darwin", "arm64"): PROVEN, # the same Apple Silicon machine on the CPU route
    ("mps", "Darwin"): UNPROVEN,        # an Intel Mac's MPS (AMD graphics) or an unknown chip: never executed
    ("cpu", "Darwin"): UNPROVEN,        # an Intel Mac on the CPU, or an unknown chip: never executed
    ("rocm", "Linux"): UNPROVEN,        # decision D3: built, resolved, faked -- never executed on AMD silicon
    ("xpu", "Linux"): UNPROVEN,         # decision D3: built, resolved, faked -- never executed on Intel silicon
}

# The torch wheel channel per (backend, os, arch). `index` None = PyPI's default for that platform; `spec` is the pip
# requirement list installed BEFORE install/requirements.txt (torch is then already satisfied, so the semantic stack
# never replaces it). Sources (read and resolved 2026-10-15, cited in docs/ONBOARDING_GUIDE.md "Which torch"):
#   PyTorch "Start Locally" matrix      https://pytorch.org/get-started/locally/
#   ROCm (HIP) semantics                https://docs.pytorch.org/docs/stable/notes/hip.html
#   AMD's PyTorch-on-ROCm install page  https://rocm.docs.amd.com/projects/install-on-linux/en/latest/install/3rd-party/pytorch-install.html
#   Intel XPU getting started           https://docs.pytorch.org/docs/stable/notes/get_start_xpu.html
#   Apple MPS backend                   https://docs.pytorch.org/docs/stable/notes/mps.html
# Resolved (uv pip compile --python-platform ..., metadata only): rocm7.2 -> torch 2.14.1+rocm7.2; xpu -> 2.14.1+xpu;
# cpu -> 2.14.1+cpu; macOS arm64 (14+) -> 2.14.1 from PyPI; macOS x86_64 -> 2.2.2 is the LAST torch with Intel-Mac
# wheels (cp38-cp312), and it was built against numpy 1.x, so numpy<2 rides with it; Python 3.13 on an Intel Mac has
# NO torch wheel at all.
TORCH_CPU_INDEX = os.environ.get("VOIDLOOM_TORCH_CPU_INDEX") or "https://download.pytorch.org/whl/cpu"
TORCH_ROCM_INDEX = os.environ.get("VOIDLOOM_TORCH_ROCM_INDEX") or "https://download.pytorch.org/whl/rocm7.2"
TORCH_XPU_INDEX = os.environ.get("VOIDLOOM_TORCH_XPU_INDEX") or "https://download.pytorch.org/whl/xpu"
INTEL_MAC_TORCH = ("torch==2.2.2", "numpy<2")
INTEL_MAC_MAX_PY = (3, 12)


def torch_channel(backend, osname, arch, py=None):
    """The channel row for this backend on this platform, or a dict with `refusal` when none exists."""
    arch = (arch or "").lower()
    if osname == "Darwin":
        if backend in ("cuda", "rocm", "xpu"):
            return {"refusal": "macOS has no %s torch build (Apple's GPU is MPS)" % backend}
        if arch in ("arm64", "aarch64"):
            return {"index": None, "spec": ["torch"], "build": "macos",
                    "desc": "PyPI's macOS arm64 torch (it carries MPS and the CPU in one build)"}
        if backend == "mps":
            return {"refusal": "MPS needs Apple Silicon; this Mac is %s" % (arch or "of unknown architecture")}
        if py is not None and tuple(py) > INTEL_MAC_MAX_PY:
            return {"refusal": "an Intel Mac has torch wheels only up to Python %d.%d (torch 2.2.2 is the last "
                               "x86_64 macOS build) and this Python is %d.%d -- install Python 3.12 (Miniforge, "
                               "docs/ONBOARDING_GUIDE.md 'On a Mac') and re-run with it" % (INTEL_MAC_MAX_PY + tuple(py[:2]))}
        return {"index": None, "spec": list(INTEL_MAC_TORCH), "build": "macos",
                "desc": "PyPI's last Intel-Mac torch (2.2.2, CPU) with numpy<2, the numpy it was built against"}
    if osname == "Linux":
        if backend == "cuda":
            return {"index": None, "spec": ["torch"], "build": "cuda",
                    "desc": "PyPI's default torch, which on Linux is the CUDA build"}
        if backend == "cpu":
            return {"index": TORCH_CPU_INDEX, "spec": ["torch"], "build": "cpu",
                    "desc": "the PyTorch CPU index (no CUDA wheels: about 1.5 GB instead of 6 GB, measured 2026-10-13)"}
        if backend == "rocm":
            return {"index": TORCH_ROCM_INDEX, "spec": ["torch"], "build": "rocm",
                    "desc": "the PyTorch ROCm index (AMD GPUs through HIP; torch.cuda answers for them)"}
        if backend == "xpu":
            return {"index": TORCH_XPU_INDEX, "spec": ["torch"], "build": "xpu",
                    "desc": "the PyTorch XPU index (Intel GPUs through torch.xpu)"}
        return {"refusal": "Linux has no %s torch build" % backend}
    return {"refusal": "this installer is for Linux and macOS; on Windows run install/install.ps1 (%s)" % osname}


# --------------------------------------------------------------------------- machine facts (stdlib)
def is_wsl():
    try:
        with open("/proc/sys/kernel/osrelease", "r") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def ram_bytes():
    """MemTotal as the OS presents it: /proc/meminfo, `sysctl hw.memsize`, or GlobalMemoryStatusEx. None if unread."""
    try:
        with open("/proc/meminfo", "r") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, universal_newlines=True, timeout=5).stdout.strip()
            return int(out)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    if sys.platform == "win32":
        try:
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return int(m.ullTotalPhys)
        except Exception:  # noqa: BLE001
            return None
    return None


def _stub_resolve(organs=None, osname=None, arch=None):
    """C1's FROZEN signature -- (backend, torch_device, basis, display_shared, ram_bytes) -- for a package whose
    organs/voidloom_device.py does not carry resolve_accelerator() yet. Observes; never imports torch."""
    ram = ram_bytes()
    declared = (os.environ.get("WIZARD_ACCELERATOR") or "").strip().lower()
    if declared == "cpu":
        return ("cpu", "cpu", "declared: WIZARD_ACCELERATOR=cpu", False, ram)
    sysname, machine = osname or platform.system(), (arch or platform.machine()).lower()
    if sysname == "Darwin" and machine in ("arm64", "aarch64"):
        # the GPU IS the display's GPU on every Mac: unified memory, shared by construction
        return ("mps", "mps", "observed: Apple Silicon (stub; the organs' resolver confirms MPS through torch)", True, ram)
    smi = None
    if organs and sysname != "Darwin":          # no Mac has shipped an NVIDIA driver since macOS 10.13
        try:
            if organs not in sys.path:
                sys.path.insert(0, organs)
            import voidloom_device  # noqa: WPS433 - stdlib-only by contract
            smi = voidloom_device.nvidia_smi()
        except Exception:  # noqa: BLE001
            smi = None
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=index", "--format=csv,noheader"], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, universal_newlines=True, timeout=8).stdout
            cards = [x.strip() for x in out.splitlines() if x.strip().isdigit()]
        except (OSError, subprocess.SubprocessError):
            cards = []
        if cards:
            return ("cuda", "cuda:0", "observed: nvidia-smi lists %d card(s) (stub)" % len(cards), len(cards) == 1, ram)
    if sysname == "Linux" and not is_wsl() and os.path.exists("/dev/kfd"):
        # /dev/kfd is the ROCm kernel driver's device node (AMD's install docs: the user needs access to /dev/kfd)
        return ("rocm", "cuda:0", "observed: /dev/kfd (the ROCm kernel driver; stub)", False, ram)
    return ("cpu", "cpu", "observed: no accelerator a stdlib process can see (stub)", False, ram)


def resolve(organs=None, osname=None, arch=None):
    """C1 through the organs' own resolver when the package carries it; the stub otherwise. -> (tuple, source)."""
    if organs:
        try:
            if organs not in sys.path:
                sys.path.insert(0, organs)
            import voidloom_device
            f = getattr(voidloom_device, "resolve_accelerator", None)
            if callable(f):
                r = tuple(f())
                if len(r) == 5:
                    return r, "C1"
        except Exception:  # noqa: BLE001 - the stub below still answers, and says it did
            pass
    return _stub_resolve(organs, osname, arch), "stub"


# --------------------------------------------------------------------------- the decisions (pure functions)
def proof_label(backend, osname, arch=None):
    a = (arch or "").strip().lower()
    a = "arm64" if a == "aarch64" else a
    return PROOF_LABELS.get((backend, osname, a), PROOF_LABELS.get((backend, osname), UNPROVEN))


def reranker_gate(backend, ram, override=""):
    """(enabled, reason) -- Drew's gate. An owner's WIZARD_RERANKER outside the managed block can only turn it OFF:
    the gate's "never on CPU-only, never under 15.0 GiB" is the owner's ruling, and the recall-time gate
    (organs.wizard_config.reranker_gate) ignores an "on" line the same way."""
    gib = (ram / GIB) if ram else None
    ov = (override or "").strip().lower()
    if backend == "cpu":
        on, why = False, ("no accelerator: this machine computes on the CPU, and the reranker is never on CPU-only "
                          "(it would make every recall many times slower)")
    elif gib is None:
        on, why = False, "the machine's RAM could not be read, and the gate opens only on a measured %.1f GiB" % THRESHOLD_GIB
    elif gib >= THRESHOLD_GIB:
        on, why = True, "an accelerator (%s) is present and %.1f GiB of RAM is at least %.1f GiB" % (backend, gib, THRESHOLD_GIB)
    else:
        # %.2f, not %.1f: at 14.99 GiB a one-decimal reason read "15.0 GiB of RAM is under 15.0 GiB" (round 2, l1)
        on, why = False, ("%.2f GiB of RAM is under %.1f GiB (the RAM a 16 GB machine presents): the reranker and the "
                          "embedder together would crowd this machine" % (gib, THRESHOLD_GIB))
    if ov in ("on", "1", "true", "yes") and not on:
        return False, "your WIZARD_RERANKER=on line cannot open the gate (it said: %s)" % why
    if ov in ("off", "0", "false", "no") and on:
        return False, "your WIZARD_RERANKER=off line overrides the gate (which said: %s)" % why
    return on, why


def machine_words(osname, arch, wsl, ram):
    if osname == "Darwin":
        what = "an Apple Silicon Mac" if arch in ("arm64", "aarch64") else "an Intel Mac"
    elif osname == "Linux":
        what = "a Linux machine under WSL2" if wsl else "a Linux machine"
    else:
        what = "a %s machine" % osname
    return "%s (%s) with %s of RAM" % (what, arch or "unknown architecture",
                                       ("%.1f GiB" % (ram / GIB)) if ram else "an unreadable amount")


BACKEND_WORDS = {"cuda": "an NVIDIA GPU (CUDA)", "rocm": "an AMD GPU (ROCm)", "mps": "the Apple GPU (MPS)",
                 "xpu": "an Intel GPU (XPU)", "cpu": "the CPU"}


def plan(resolved, request="auto", osname=None, arch=None, wsl=None, py=None, override="", source="C1", pin_from=""):
    """The whole decision as one dict. `resolved` is C1's 5-tuple. Pure: every machine fact is an argument.
    `pin_from` names where the card pin the resolver read as "(ambient)" came from when it was not the shell -- install.sh
    passes the owner's --gpu answer to the planning child as CUDA_VISIBLE_DEVICES, so "ambient" would be untrue."""
    backend_r, tdev, basis, display_shared, ram = resolved
    if pin_from and "(ambient)" in (basis or ""):
        basis = basis.replace("(ambient)", "(%s)" % pin_from)
    osname = osname or platform.system()
    arch = (arch or platform.machine()).lower()
    wsl = (is_wsl() if osname == "Linux" else False) if wsl is None else wsl
    out = {"resolver": source, "resolved_backend": backend_r, "torch_device": tdev, "basis": basis,
           "display_shared": bool(display_shared), "ram_bytes": ram,
           "ram_gib": round(ram / GIB, 1) if ram else None, "os": osname, "arch": arch, "wsl": bool(wsl),
           "request": request, "refusal": None, "notes": []}
    req = (request or "auto").strip().lower()
    if req not in REQUESTS:
        out["refusal"] = "--accelerator %r is not one of %s" % (request, "|".join(REQUESTS))
        return out
    if backend_r not in BACKENDS:
        out["notes"].append("the resolver answered %r, which is not a backend; the CPU is used" % backend_r)
        backend_r = "cpu"
    if req == "auto":
        backend = backend_r
    elif req == "cpu" or req == backend_r:
        backend = req
    elif backend_r != "cpu":
        out["refusal"] = ("--accelerator %s contradicts this machine: it shows %s (%s). Pass --accelerator auto (or "
                          "%s), or --force-cpu" % (req, BACKEND_WORDS[backend_r], basis, backend_r))
        return out
    else:
        backend = req
        out["notes"].append("you asked for %s; the installer could not observe it before torch exists (%s) -- the "
                            "organs' resolver confirms it through torch after the install" % (req, basis))
    if wsl and backend in ("rocm", "xpu"):
        # not refused: AMD documents ROCm under WSL2 for a list of Radeon cards (rocm.docs.amd.com, "ROCm on WSL") and
        # Intel documents its GPU driver under WSL2 -- but the stdlib resolver cannot see either card there, and nothing
        # here has executed on one, so it is said
        out["notes"].append("%s under WSL2 is supported by its vendor for some cards only, and is unproven here" % backend)
    ch = torch_channel(backend, osname, arch, py)
    out["backend"] = backend
    if backend != backend_r:
        # the basis says why THIS backend: the owner's word, and what the machine showed beside it
        out["basis"] = "requested: --accelerator %s (the machine shows %s: %s)" % (backend, backend_r, basis)
    out["channel"] = ch
    if backend == "cpu":
        out["torch_device"] = "cpu"
    # The label, the gate and the sentence are decided BEFORE a channel refusal returns (round 2, m1): an Intel Mac on
    # Python > 3.12 has no torch wheel, and the plan used to return first -- "Compute: cpu [no label]", "Reranker: OFF
    # -- " with no reason, and an INSTALL_RECORD with proof_label= (which the doctor and banner then read as absent).
    out["proof_label"] = proof_label(backend, osname, arch)
    on, why = reranker_gate(backend, ram, override)
    out["reranker"] = {"enabled": on, "reason": why, "threshold_gib": THRESHOLD_GIB,
                       "override": (override or "").strip() or None}
    out["sentence"] = ("This machine: %s. Compute: %s [%s]. Torch: %s. Reranker %s: %s."
                       % (machine_words(osname, arch, wsl, ram), BACKEND_WORDS[backend], out["proof_label"],
                          ch.get("desc") or ("none -- %s" % ch.get("refusal")), "ON" if on else "OFF", why))
    if ch.get("refusal"):
        out["refusal"] = ch["refusal"]
    return out


# --------------------------------------------------------------------------- a failed device proof (C4)
def on_proof_failure(backend, reason, owner_line="", date=None):
    """What install.sh does when P6/P7 fail on `backend` (Vael's correction in SYNTHESIS §3: warn AND flip). Pure.
    -> {"action": "flip" | "keep-owner" | "fail", "label", "comment", "announce"}
      flip        an MPS/ROCm/XPU machine with no owner line: record WIZARD_ACCELERATOR=cpu under `comment`, print
                  `announce` once (gpu-safety-002's shape: the value, the reason, the result, the opt-out), label the
                  install FAILED-ON-THIS-MACHINE, prove the CPU instead. Nothing is embedded on the failed backend.
      keep-owner  the owner's own WIZARD_ACCELERATOR line names a backend: the named opt-out, honoured -- the label is
                  still FAILED-ON-THIS-MACHINE, and `announce` says what the owner's line keeps.
      fail        CUDA or the CPU: the PROVEN rows. A failure there is a Tier-1 failure, said -- never a quiet flip."""
    date = date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    why = (reason or "unknown").replace("\n", " ").strip()
    step = why.split(":", 1)[0] if ":" in why else "device"
    own = (owner_line or "").strip().lower()
    if backend not in ("mps", "rocm", "xpu"):
        return {"action": "fail", "label": None, "comment": "",
                "announce": "the %s device failed its proof: %s" % (backend, why)}
    if own in ("cuda", "rocm", "mps", "xpu"):          # an owner's explicit backend; 'auto' names none
        return {"action": "keep-owner", "label": FAILED_HERE, "comment": "",
                "announce": ("the %s proof FAILED on this machine (%s), and your own WIZARD_ACCELERATOR=%s line keeps "
                             "it -- the opt-out, honoured: this install continues on %s, recorded as %s. Vectors "
                             "embedded now may not match vectors made elsewhere." % (backend, why, own, backend, FAILED_HERE))}
    return {"action": "flip", "label": FAILED_HERE,
            "comment": ("WIZARD_ACCELERATOR: written by install.sh %s -- the %s proof FAILED on this machine (%s), so every "
                        "job computes on the CPU. To try %s again: delete this line and its comment, then re-run "
                        "install.sh." % (date, backend, step, backend)),
            "announce": ("SAFETY: %s REFUSED for this brain -- the %s proof failed on this machine (%s); result: every "
                         "job computes on the CPU (WIZARD_ACCELERATOR=cpu recorded; proof_label %s in INSTALL_RECORD; "
                         "nothing was embedded on %s); opt-out: write WIZARD_ACCELERATOR=%s yourself outside the managed "
                         "block and re-run." % (backend, step, why, FAILED_HERE, backend, backend))}


# --------------------------------------------------------------------------- C3: the gate, recorded
def gate_record(p, now=None):
    """The two wizard-config.json keys contract C3 freezes, from a plan."""
    now = now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"reranker_enabled": bool(p["reranker"]["enabled"]),
            "reranker_gate": {"accelerator": p["backend"], "ram_gib": p["ram_gib"],
                              "threshold_gib": THRESHOLD_GIB, "decided_at": now}}


def write_gate(config_path, p, dry_run=False):
    """Merge the C3 keys into wizard-config.json. Returns (rc, message). Never clobbers: an unreadable or non-object
    file refuses (rc 3) with nothing written; a readable one is backed up, replaced atomically and read back."""
    data = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            return 3, ("%s is not readable JSON (%s); the reranker decision was NOT recorded and the file was not "
                       "touched -- fix or move it, then re-run" % (config_path, e))
        if not isinstance(data, dict):
            return 3, "%s holds a JSON %s, not an object; NOT touched" % (config_path, type(data).__name__)
    rec = gate_record(p)
    if dry_run:
        return 0, "would write reranker_enabled=%s and reranker_gate into %s" % (
            str(rec["reranker_enabled"]).lower(), config_path)
    merged = dict(data)
    merged.update(rec)
    d = os.path.dirname(os.path.abspath(config_path))
    if not os.path.isdir(d):
        os.makedirs(d, mode=0o700)
    if os.path.exists(config_path):
        shutil.copy2(config_path, config_path + ".bak")
    tmp = "%s.tmp.%d" % (config_path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, config_path)
    with open(config_path, "r", encoding="utf-8") as fh:
        back = json.load(fh)
    if any(back.get(k) != v for k, v in merged.items()):
        return 3, "%s did not read back as written" % config_path
    return 0, "wrote reranker_enabled=%s and reranker_gate into %s" % (str(rec["reranker_enabled"]).lower(), config_path)


# --------------------------------------------------------------------------- CLI
def _opts(argv, names, flags=()):
    o, i = {n: None for n in names}, 0
    for f in flags:
        o[f] = False
    while i < len(argv):
        if argv[i] in flags:
            o[argv[i]] = True
            i += 1
        elif argv[i] in o and i + 1 < len(argv):
            o[argv[i]] = argv[i + 1]
            i += 2
        else:
            return None
    return o


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 64
    verb, rest = argv[0], argv[1:]
    if verb == "plan":
        o = _opts(rest, ("--organs", "--request", "--python", "--reranker-override", "--os", "--arch", "--pin-from"))
        if o is None:
            sys.stderr.write(__doc__)
            return 64
        py = None
        if o["--python"]:
            m = re.match(r"^(\d+)\.(\d+)", o["--python"])
            py = (int(m.group(1)), int(m.group(2))) if m else None
        osname, arch = o["--os"] or platform.system(), o["--arch"] or platform.machine()
        resolved, source = resolve(o["--organs"], osname, arch)
        p = plan(resolved, o["--request"] or "auto", osname=osname, arch=arch, py=py,
                 override=o["--reranker-override"] or "", source=source, pin_from=o["--pin-from"] or "")
        print(json.dumps(p, sort_keys=True))
        return 2 if p.get("refusal") else 0
    if verb == "fields":
        # the plan (JSON on stdin) as KEY<TAB>VALUE lines a shell reads with `read`, no eval: one value per line
        p = json.load(sys.stdin)
        ch = p.get("channel") or {}
        rr = p.get("reranker") or {}
        rows = [("backend", p.get("backend") or ""), ("label", p.get("proof_label") or ""),
                ("basis", p.get("basis") or ""), ("resolver", p.get("resolver") or ""),
                ("index", ch.get("index") or ""), ("spec", " ".join(ch.get("spec") or [])),
                ("build", ch.get("build") or ""), ("desc", ch.get("desc") or ""),
                ("rerank", "1" if rr.get("enabled") else "0"), ("rerank_why", rr.get("reason") or ""),
                ("ram_gib", "" if p.get("ram_gib") is None else str(p["ram_gib"])),
                ("sentence", p.get("sentence") or ""), ("refusal", p.get("refusal") or ""),
                ("notes", " ".join(p.get("notes") or []))]
        for k, v in rows:
            print("%s\t%s" % (k, str(v).replace("\n", " ").replace("\t", " ")))
        return 0
    if verb == "on-proof-failure":
        o = _opts(rest, ("--backend", "--reason", "--owner-line"))
        if o is None or not o["--backend"]:
            sys.stderr.write(__doc__)
            return 64
        d = on_proof_failure(o["--backend"], o["--reason"] or "", o["--owner-line"] or "")
        for k in ("action", "label", "comment", "announce"):
            print("%s\t%s" % (k, (d.get(k) or "").replace("\t", " ")))
        return 0
    if verb == "write-gate":
        o = _opts(rest, ("--config", "--plan"), flags=("--dry-run",))
        if o is None or not o["--config"] or not o["--plan"]:
            sys.stderr.write(__doc__)
            return 64
        if o["--plan"] == "-":
            p = json.load(sys.stdin)
        else:
            with open(o["--plan"], "r", encoding="utf-8") as fh:
                p = json.load(fh)
        rc, msg = write_gate(o["--config"], p, dry_run=o["--dry-run"])
        print(json.dumps({"rc": rc, "message": msg}))
        return rc
    sys.stderr.write(__doc__)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
