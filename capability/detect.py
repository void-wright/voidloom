#!/usr/bin/env python3
"""Voidloom L5-X "The Adaptive Loom" -- capability/detect.py (stdlib-only).

The hardware-detection brain of the self-installer. Promotes the ~4-line binary
``detect_embed_model`` (install.sh) into a stdlib detector that emits a rich
``--json`` capability probe and -- via a DECLARATIVE first-match-wins tier
table + a ``model_fits(probe)`` predicate -- picks the heaviest embedding model
that *fits* the detected hardware.

Council ruling (memory #5835, #6078, #5848): install != onboard. This module is
part of the INSTALL act -- a deterministic, stdlib-only, no-recall, no-LLM
hardware probe. It writes nothing and reads nothing from the brain. The
memory-driven enrichment is ONBOARDING (a fresh Claude's first recall), a
separate clean act, and is NOT touched here.

Design invariants (all enforced by the test-suite):

  1. STDLIB-ONLY. The Tier-0 guarantee is "runs on any python3, no venv". We
     import only the standard library at module scope. ``torch`` is consulted
     ONLY when a venv that ships it already exists, inside a guarded import that
     fail-softs to None -- so a bare ``python3 detect.py`` never needs torch.

  2. EMERGENT TIERS. The tier table is a declarative, ordered list of TierSpec
     rows. Each row carries a ``fits(probe)`` predicate computed from probe
     FACTS (gpu memory, compute capability, torch-cuda-usable). The chosen tier
     is the first row whose predicate is satisfied. There is NO hardcoded
     ``if gpu: jina else: nomic`` ladder -- new hardware slots into the right
     tier purely by satisfying a predicate, so the table survives new GPUs
     (sm_86 Ampere laptop, sm_121 Blackwell, ...) without edits.

  3. NEVER lspci. ``lspci`` does not enumerate GPUs reliably under WSL2 (the
     D3 lesson). The GPU probe uses ONLY ``nvidia-smi --query-gpu`` (a stable,
     verified signal on the target hardware). The source carries NO live
     ``lspci`` token (the test tokenizes the source and asserts this).

  4. ADVISORY gpu_features. compute_cap -> {bf16, fp8, fa2, sm} is recorded as
     ADVISORY ``gpu_features`` for M0's GPU-routing/burst policy. These do NOT
     gate the embedding-model choice (embed + cross-encoder rerank need no
     FlashAttention / FP8). The tier predicates key on MEMORY + cuda-usability,
     never on fa2/fp8.

  5. FAIL-SOFT EVERYWHERE. Every probe is wrapped so any failure (missing
     nvidia-smi, a driver hiccup, a broken torch, a malformed line) degrades to
     the safe default -- a CPU/nomic probe -- and NEVER raises. An installer
     must never crash on detection.

Usage:
    python3 capability/detect.py            # human summary to stderr, json to stdout
    python3 capability/detect.py --json     # json only to stdout (machine / shell shim)
    python3 capability/detect.py --quiet     # json only, no stderr summary

Exit status is always 0 on a completed probe (even an all-fail-soft CPU probe);
non-zero only on an unexpected internal error after the fail-soft net (which the
suite asserts cannot happen for the supported paths).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Callable, Dict, List, Optional


# --------------------------------------------------------------------------- #
# Tunables (declarative; no behavioural branches live here).
# --------------------------------------------------------------------------- #

# nvidia-smi is the ONLY GPU enumeration signal (NEVER lspci -- WSL2-unreliable).
_NVIDIA_SMI_QUERY = "count,memory.total,compute_cap"
_NVIDIA_SMI_TIMEOUT_S = 8

# The safe default the whole module degrades toward. CPU-only, nomic 768-dim --
# the Tier-0 floor that runs anywhere.
SAFE_DEFAULT_MODEL = "nomic"

# Embedding model catalogue (name -> repo_id + embedding dimension). Mirrors
# wizard_config.py profiles + the per-tier decision table (memory #5848/#6078).
EMBED_MODELS: Dict[str, Dict[str, Any]] = {
    "nomic": {"repo_id": "nomic-ai/nomic-embed-text-v1.5", "embed_dim": 768},
    "qwen3-0.6b": {"repo_id": "Qwen/Qwen3-Embedding-0.6B", "embed_dim": 1024},
    "jina": {"repo_id": "jinaai/jina-embeddings-v5-text-small", "embed_dim": 1024},
}

# The reranker's RAM rule (contract C3; Drew's ruling 2026-10-14): ON only with a non-CPU accelerator AND MemTotal >=
# 15.0 GiB -- the RAM a 16 GB machine presents (a Mac reports 16.0; a 16 GB PC 15.5-15.9 after firmware reservations).
# detect.py REPORTS the fit; the gate itself is lane L2's (organs/wizard_config + the live re-check).
RERANKER_RAM_THRESHOLD_GIB = 15.0

# The torch wheel channel per backend -- what the installer (lane L4) installs. Resolved 2026-10-15 from the simple
# indexes (no wheel downloaded): whl/rocm7.2 -> torch 2.14.1 manylinux_2_28_x86_64; whl/xpu -> 2.14.1 linux + win_amd64;
# whl/cpu -> 2.14.1. ROCm wheels track a ROCm driver line: the index names the userspace the host must match.
TORCH_CHANNELS: Dict[str, Dict[str, Any]] = {
    "cuda": {"index_url": None, "package": "torch",
             "how": "PyPI's default torch wheel (the CUDA build on Linux x86_64; Windows needs the cu index)"},
    "rocm": {"index_url": "https://download.pytorch.org/whl/rocm7.2", "package": "torch",
             "how": "PyTorch's ROCm index (Linux x86_64; the host's ROCm userspace must match the index's line)"},
    "xpu": {"index_url": "https://download.pytorch.org/whl/xpu", "package": "torch",
            "how": "PyTorch's XPU index (Linux and Windows; Intel GPU driver first)"},
    "mps": {"index_url": None, "package": "torch",
            "how": "PyPI's macOS arm64 wheel (MPS is built in; x86_64 macOS wheels end at the 2.2 line)"},
    "cpu": {"index_url": "https://download.pytorch.org/whl/cpu", "package": "torch",
            "how": "PyTorch's CPU index (no GPU runtime downloaded)"},
    "directml": {"index_url": None, "package": "torch-directml",
                 "how": "PyPI torch-directml (Windows; public preview, lags torch) -- candidate row, not selectable"},
}


# --------------------------------------------------------------------------- #
# Low-level probes -- each is independently fail-soft.
# --------------------------------------------------------------------------- #

def probe_platform() -> Dict[str, Any]:
    """OS + arch + WSL2 flag. Lightest, most-reliable probe -- runs first.

    WSL2 is detected from the kernel release/version string (the canonical
    'microsoft' marker), never from a heavier signal. Fail-soft: any failure
    yields unknowns, never raises.
    """
    info: Dict[str, Any] = {"os": "unknown", "arch": "unknown", "wsl2": False}
    try:
        info["os"] = platform.system() or "unknown"          # Linux | Darwin | Windows
    except Exception:
        pass
    try:
        info["arch"] = platform.machine() or "unknown"        # x86_64 | arm64 | aarch64
    except Exception:
        pass
    info["wsl2"] = _detect_wsl2()
    return info


def _detect_wsl2() -> bool:
    """True iff running under WSL2. Checks the kernel 'microsoft' marker.

    Order, all fail-soft: platform.release() (cheapest) -> platform.version()
    -> /proc/version (Linux only). Any one carrying 'microsoft' is conclusive.
    """
    markers = []
    try:
        markers.append(platform.release() or "")
    except Exception:
        pass
    try:
        markers.append(platform.version() or "")
    except Exception:
        pass
    for blob in markers:
        if "microsoft" in blob.lower():
            return True
    # /proc/version is Linux-only; guarded read, never raises.
    try:
        if os.path.exists("/proc/version"):
            with open("/proc/version", "r", encoding="utf-8", errors="ignore") as fh:
                if "microsoft" in fh.read().lower():
                    return True
    except Exception:
        pass
    return False


def probe_gpu() -> Dict[str, Any]:
    """GPU enumeration via ``nvidia-smi --query-gpu`` ONLY (NEVER lspci).

    Returns ``{count, memory_total, compute_cap, available, reason}``.
      - ``memory_total`` is MiB of the SMALLEST card (the binding constraint for
        a resident model across a homogeneous pack).
      - ``compute_cap`` is a float (e.g. 7.5) -- the MIN across cards (the
        capability floor that gates advisory features).
    Fail-soft: no nvidia-smi / a non-zero exit / unparseable output all yield
    ``available=False`` with a human ``reason`` -- and NEVER raise.
    """
    result: Dict[str, Any] = {
        "count": 0,
        "memory_total": 0,
        "compute_cap": 0.0,
        "available": False,
        "reason": "",
    }

    smi = _which_nvidia_smi()
    if not smi:
        result["reason"] = "nvidia-smi not on PATH"
        return result

    try:
        proc = subprocess.run(
            [smi, "--query-gpu=" + _NVIDIA_SMI_QUERY, "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=_NVIDIA_SMI_TIMEOUT_S,
        )
    except Exception as exc:  # FileNotFoundError, TimeoutExpired, OSError, ...
        result["reason"] = "nvidia-smi invocation failed: %s" % type(exc).__name__
        return result

    if proc.returncode != 0:
        # GPU present-but-broken driver, or no GPU. Degrade to CPU.
        stderr = (proc.stderr or "").strip().splitlines()
        result["reason"] = "nvidia-smi exit %d%s" % (
            proc.returncode,
            (": " + stderr[0]) if stderr else "",
        )
        return result

    parsed = _parse_nvidia_smi(proc.stdout or "")
    if parsed["count"] <= 0:
        result["reason"] = "nvidia-smi returned no usable GPU rows"
        return result

    result.update(parsed)
    result["available"] = True
    result["reason"] = "ok"
    return result


def _which_nvidia_smi() -> Optional[str]:
    """Locate the nvidia-smi binary, fail-soft. NEVER reaches for lspci."""
    try:
        found = shutil.which("nvidia-smi")
        if found:
            return found
    except Exception:
        pass
    # WSL2 sometimes hosts it at a Windows-side path not on PATH.
    for cand in ("/usr/lib/wsl/lib/nvidia-smi", "/usr/bin/nvidia-smi"):
        try:
            if os.path.exists(cand) and os.access(cand, os.X_OK):
                return cand
        except Exception:
            pass
    return None


def _parse_nvidia_smi(stdout: str) -> Dict[str, Any]:
    """Parse ``count,memory.total,compute_cap`` CSV rows.

    nvidia-smi emits ONE row PER GPU; ``count`` repeats on each. We take the row
    count as the authoritative GPU count, the MIN memory (binding constraint)
    and the MIN compute_cap (capability floor). Fully fail-soft per-row: a
    malformed row is skipped, never fatal.
    """
    rows: List[List[str]] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) >= 3:
            rows.append(cols)

    if not rows:
        return {"count": 0, "memory_total": 0, "compute_cap": 0.0}

    mems: List[int] = []
    caps: List[float] = []
    for cols in rows:
        mems.append(_to_int(cols[1]))
        caps.append(_to_float(cols[2]))

    mems = [m for m in mems if m > 0]
    caps = [c for c in caps if c > 0.0]

    return {
        "count": len(rows),
        "memory_total": min(mems) if mems else 0,
        "compute_cap": min(caps) if caps else 0.0,
    }


def probe_torch_cuda(venv_only: bool = True) -> Dict[str, Any]:
    """OPTIONAL corroborating probe: is torch.cuda actually usable here?

    Fixes the GPU-present-but-CPU-torch trap: a box can have a GPU yet ship a
    CPU-only torch wheel, in which case the heavy CUDA models cannot load and we
    must degrade to nomic. This is a CORROBORATING signal (the #5888 lesson:
    carry a stable primary, demote a fragile signal to corroborating), never the
    sole authority.

    To honour the Tier-0 stdlib guarantee we DO NOT import torch unless a venv
    that already ships it exists (``_torch_venv_present()``). When skipped or on
    any failure we return ``usable=None`` (unknown) -- never raise.
    """
    out: Dict[str, Any] = {"torch_present": False, "cuda_usable": None, "reason": ""}

    if venv_only and not _torch_venv_present():
        out["reason"] = "no torch venv present -- skipped (stdlib-only path)"
        return out

    try:
        import importlib
        torch = importlib.import_module("torch")
    except Exception as exc:
        out["reason"] = "torch import failed: %s" % type(exc).__name__
        return out

    out["torch_present"] = True
    # R-6 (EW4-05): ask torch ONLY when CUDA_VISIBLE_DEVICES selects a device. Under a value that selects none
    # (empty, blank, '-1', an index the host lacks) torch.cuda.is_available() is a cuInit that SEGFAULTS this
    # interpreter on WSL2 -- rc 139, no exception to catch. The rule is organs/voidloom_device.cuda_selects_a_device.
    selects, why = _cuda_selection()
    if not selects:
        out["cuda_usable"] = False
        out["reason"] = why
        return out
    try:
        out["cuda_usable"] = bool(torch.cuda.is_available())
        out["reason"] = "torch.cuda.is_available()=%s" % out["cuda_usable"]
    except Exception as exc:
        out["cuda_usable"] = None
        out["reason"] = "torch.cuda probe raised: %s" % type(exc).__name__
    return out


def _cuda_selection():
    """(selects, why) from organs/voidloom_device.cuda_selects_a_device -- the sibling of this directory in the package
    and in a laid brain alike. If that module cannot be imported, the minimum of the rule holds here: an EMPTY (or
    blank) CUDA_VISIBLE_DEVICES selects no device; anything else is left to torch."""
    try:
        organs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "organs")
        if organs not in sys.path:
            sys.path.insert(0, organs)
        import voidloom_device
        return voidloom_device.cuda_selects_a_device()
    except Exception:
        raw = os.environ.get("CUDA_VISIBLE_DEVICES")
        if raw is not None and raw.strip() == "":
            return False, "CUDA_VISIBLE_DEVICES=%r selects no device (empty): CUDA is not initialised" % raw
        return True, "voidloom_device not importable; torch decides"


def _torch_venv_present() -> bool:
    """True iff torch appears importable WITHOUT actually importing it.

    Uses importlib.util.find_spec, which inspects sys.path metadata only and
    never executes torch's heavy ``__init__``. Fail-soft -> False.
    """
    try:
        import importlib.util
        return importlib.util.find_spec("torch") is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# The accelerator (handoff round 1, lane L1) -- ONE resolver, asked, not re-derived.
# --------------------------------------------------------------------------- #

def _device_module():
    """organs/voidloom_device -- the sibling of this directory in the package and in a laid brain alike."""
    organs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "organs")
    if organs not in sys.path:
        sys.path.insert(0, organs)
    import voidloom_device
    return voidloom_device


def probe_accelerator(confirm: bool = False) -> Dict[str, Any]:
    """The resolver's decision (organs/voidloom_device.accelerator_report): backend, torch device, basis, proof label,
    candidates, RAM. confirm=False is stdlib only (the Tier-0 path); confirm=True lets torch confirm a candidate and is
    used only when a torch venv already exists. Fail-soft: a resolver that cannot be imported reports CPU and says so."""
    try:
        rep = _device_module().accelerator_report(confirm=confirm)
        rep = {k: rep.get(k) for k in ("backend", "torch_device", "basis", "label", "display_shared", "ram_bytes",
                                       "ram_gib", "candidates", "degraded", "refused", "confirmed", "notes")}
        return rep
    except Exception as exc:
        # The resolver is missing: nothing proven runs here, so nothing is claimed (round 2, M2).
        return {"backend": "cpu", "torch_device": "cpu", "label": "SUPPORTED-UNPROVEN", "display_shared": False,
                "resolver_ok": False,
                "basis": "voidloom_device not importable (%s): CPU" % type(exc).__name__, "ram_bytes": _ram_fallback(),
                "ram_gib": None, "candidates": [], "degraded": True, "refused": False, "confirmed": None, "notes": []}


def _ram_fallback() -> Optional[int]:
    try:
        return int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except Exception:
        return None


def _directml_present() -> bool:
    """find_spec only: importing torch_directml imports torch, which the Tier-0 path never does."""
    try:
        import importlib.util
        return importlib.util.find_spec("torch_directml") is not None
    except Exception:
        return False


def accelerator_rows(acc: Dict[str, Any], os_name: str) -> List[Dict[str, Any]]:
    """One row per backend Voidloom knows: its proof label, whether it is SELECTED, a CANDIDATE, or neither, and the
    torch channel the installer would use for it. DirectML is listed on Windows as a candidate row only (C2)."""
    # Round 2 (M2): a row is PROVEN only where the resolver's OS-aware backend_label() says so for THIS os_name
    # (Linux cuda/cpu); the fallback, when the resolver cannot be asked, claims nothing.
    labels = {b: "SUPPORTED-UNPROVEN" for b in ("cuda", "cpu", "mps", "rocm", "xpu", "directml")}
    try:
        labels.update({b: _device_module().backend_label(b, os_name or None)
                       for b in ("cuda", "rocm", "mps", "xpu", "cpu")})
    except Exception:
        pass
    chosen = acc.get("backend", "cpu")
    cands = set(acc.get("candidates") or [])
    rows = []
    for b in ("cuda", "rocm", "mps", "xpu", "cpu"):
        rows.append({"backend": b, "label": labels[b], "selected": b == chosen,
                     "candidate": b in cands and b != chosen, "selectable": True,
                     "torch_channel": TORCH_CHANNELS[b]})
    if os_name == "Windows" or _directml_present():
        rows.append({"backend": "directml", "label": labels["directml"], "selected": False,
                     "candidate": _directml_present(), "selectable": False, "torch_channel": TORCH_CHANNELS["directml"],
                     "why": "C2 has no directml value: a candidate row for a later round"})
    return rows


def reranker_fits(acc: Dict[str, Any]) -> Dict[str, Any]:
    """C3's rule as a FACT about this machine: a non-CPU accelerator AND RAM >= 15.0 GiB. Never on CPU-only."""
    ram = acc.get("ram_bytes")
    gib = (ram / float(1 << 30)) if ram else None
    backend = acc.get("backend", "cpu")
    if backend == "cpu":
        why = "no accelerator (CPU-only): the reranker is never on"
        ok = False
    elif gib is None:
        why = "RAM could not be read: off"
        ok = False
    elif gib < RERANKER_RAM_THRESHOLD_GIB:
        why = "%.2f GiB RAM < %.1f GiB: off" % (gib, RERANKER_RAM_THRESHOLD_GIB)
        ok = False
    else:
        why = "%s accelerator and %.2f GiB RAM >= %.1f GiB: on" % (backend, gib, RERANKER_RAM_THRESHOLD_GIB)
        ok = True
    return {"fits": ok, "accelerator": backend, "ram_gib": round(gib, 2) if gib else None,
            "threshold_gib": RERANKER_RAM_THRESHOLD_GIB, "why": why}


# --------------------------------------------------------------------------- #
# Advisory GPU features (compute_cap -> capabilities). NOT gates on the model.
# --------------------------------------------------------------------------- #

def derive_gpu_features(compute_cap: float) -> Dict[str, Any]:
    """Map a CUDA compute capability to ADVISORY hardware features.

    These inform M0's GPU-routing / burst-precision policy. They are explicitly
    NOT consulted by the tier predicates -- the embedding + rerank models need
    no FlashAttention or FP8. Thresholds (from the L5 hardware roadmap, #5847):
      - bf16 : Ampere+ (sm_80+). Turing (sm_75) lacks native bf16 throughput.
      - fp8  : Ada / Hopper+ (sm_89+). WSL2 emulates FP8 -> still flag the cap.
      - fa2  : FlashAttention-2 -> Ampere+ (sm_80+). Refuses consumer-Blackwell.
    A zero/unknown cap yields all-False (a CPU box has no GPU features).
    """
    cap = compute_cap if compute_cap and compute_cap > 0 else 0.0
    return {
        "sm": _fmt_sm(cap),
        "compute_cap": cap,
        "bf16": cap >= 8.0,
        "fp8": cap >= 8.9,
        "fa2": cap >= 8.0,
    }


def _fmt_sm(cap: float) -> Optional[str]:
    """Render a compute capability float as an ``sm_XY`` string (7.5 -> sm_75)."""
    if not cap or cap <= 0:
        return None
    major = int(cap)
    minor = int(round((cap - major) * 10))
    return "sm_%d%d" % (major, minor)


# --------------------------------------------------------------------------- #
# THE DECLARATIVE TIER TABLE + model_fits(probe) predicate (emergent tiers).
# --------------------------------------------------------------------------- #

class TierSpec:
    """One row of the declarative tier table.

    ``fits`` is a predicate over the assembled ``probe`` dict; it computes from
    FACTS (gpu memory MiB, compute capability, torch-cuda usability) -- never
    from a hand-assigned tier label. The detector walks the table in order and
    selects the FIRST row whose ``fits`` returns True (first-match-wins). Order
    the table heaviest-first so the richest fitting model wins.
    """

    def __init__(
        self,
        name: str,
        embed_model: str,
        min_gpu_mem_mb: int,
        fits: Callable[[Dict[str, Any]], bool],
        rationale: str,
    ) -> None:
        self.name = name
        self.embed_model = embed_model
        self.min_gpu_mem_mb = min_gpu_mem_mb
        self.fits = fits
        self.rationale = rationale


def _cuda_truly_usable(probe: Dict[str, Any]) -> bool:
    """A GPU tier is reachable iff a CUDA-capable GPU is enumerated AND torch's
    CUDA is not KNOWN-broken.

    The GPU-present-but-CPU-torch trap: ``torch_cuda.cuda_usable is False`` is a
    POSITIVE "torch ran and said no CUDA" -> that tier is NOT reachable. But
    ``None`` (unknown -- no venv probed, the stdlib path) must NOT veto: install
    runs stdlib-only at Tier-0, so an unknown torch state trusts the nvidia-smi
    enumeration. This keeps the install-time floor honest (#5835) while still
    catching the real CPU-torch trap when a venv exists to reveal it.
    """
    gpu = probe.get("gpu", {})
    if not gpu.get("available"):
        return False
    acc = probe.get("accelerator") or {}
    if acc.get("backend") == "cpu" and acc.get("resolver_ok", True):
        # nvidia-smi lists cards and the ONE resolver still answered CPU: WIZARD_ACCELERATOR=cpu (the --force-cpu
        # spelling), the no-GPU ruling, an empty CUDA_VISIBLE_DEVICES, a refusal or a degradation. The organs will run
        # on CPU, so a GPU tier would choose an embedder for a device nothing will use. (A resolver that could not be
        # imported vetoes nothing: the old nvidia-smi reading stands.)
        return False
    cuda_usable = probe.get("torch_cuda", {}).get("cuda_usable")
    if cuda_usable is False:        # torch positively reports no CUDA -> trap
        return False
    return True                      # True or None (unknown) -> trust nvidia-smi


def _accel(probe: Dict[str, Any]) -> str:
    return (probe.get("accelerator") or {}).get("backend") or "cpu"


def _gpu_mem(probe: Dict[str, Any]) -> int:
    try:
        return int(probe.get("gpu", {}).get("memory_total", 0) or 0)
    except Exception:
        return 0


# Heaviest-first. Each predicate keys ONLY on memory + cuda-usability (capability
# FACTS) -- never on fa2/fp8 (advisory) and never on a pre-assigned tier name.
# New hardware (a 24GB sm_86 laptop, a 48GB sm_121 card) lands in the correct
# row by satisfying a memory threshold -- no table edit required. That is what
# makes the tiers EMERGENT.
TIER_TABLE: List[TierSpec] = [
    TierSpec(
        name="GPU-HIGH",
        embed_model="jina",
        min_gpu_mem_mb=20000,
        fits=lambda p: _cuda_truly_usable(p) and _gpu_mem(p) >= 20000,
        rationale=">=20GB CUDA GPU -> full resident pack + a resident big embedder.",
    ),
    TierSpec(
        name="GPU-MID",
        embed_model="jina",
        min_gpu_mem_mb=8000,
        fits=lambda p: _cuda_truly_usable(p) and _gpu_mem(p) >= 8000,
        rationale=">=8GB CUDA GPU -> jina v5 1024-dim + the small-encoder pack.",
    ),
    TierSpec(
        name="GPU-LOW",
        embed_model="qwen3-0.6b",
        min_gpu_mem_mb=4000,
        fits=lambda p: _cuda_truly_usable(p) and _gpu_mem(p) >= 4000,
        rationale=">=4GB CUDA GPU -> Qwen3-Embedding-0.6B 1024-dim (fits a small card).",
    ),
    # Non-NVIDIA accelerators (handoff round 1, lane L1). Each is SUPPORTED-UNPROVEN, so each keeps the Tier-0
    # embedder: if the install's own proof fails on that silicon and the machine flips to CPU (C4
    # FAILED-ON-THIS-MACHINE), the brain's vectors are still the ones CPU can carry at the CPU speed bar. A larger
    # embedder on these backends is a promotion for after the proof, not a guess before it. The tier NAMES the
    # backend so doctor and the installer say what will run.
    TierSpec(
        name="MPS",
        embed_model="nomic",
        min_gpu_mem_mb=0,
        fits=lambda p: _accel(p) == "mps",
        rationale="Apple Silicon (MPS, SUPPORTED-UNPROVEN) -> nomic 768-dim on MPS until this Mac's proof passes.",
    ),
    TierSpec(
        name="ROCM",
        embed_model="nomic",
        min_gpu_mem_mb=0,
        fits=lambda p: _accel(p) == "rocm",
        rationale="AMD GPU via ROCm (SUPPORTED-UNPROVEN) -> nomic 768-dim on the GPU until a proof passes.",
    ),
    TierSpec(
        name="XPU",
        embed_model="nomic",
        min_gpu_mem_mb=0,
        fits=lambda p: _accel(p) == "xpu",
        rationale="Intel GPU via torch.xpu (SUPPORTED-UNPROVEN) -> nomic 768-dim on the GPU until a proof passes.",
    ),
    TierSpec(
        name="CPU",
        embed_model="nomic",
        min_gpu_mem_mb=0,
        fits=lambda p: True,  # terminal catch-all -> the Tier-0 floor (nomic/768)
        rationale="No usable CUDA GPU -> nomic 768-dim on CPU (the Tier-0 floor).",
    ),
]


def model_fits(probe: Dict[str, Any], tier: TierSpec) -> bool:
    """The fit predicate, named per the spec. True iff ``tier`` can host its
    embedding model on the hardware described by ``probe``. Thin, total wrapper
    around ``tier.fits`` so a buggy predicate degrades to "does not fit" rather
    than raising mid-walk.
    """
    try:
        return bool(tier.fits(probe))
    except Exception:
        return False


def select_tier(probe: Dict[str, Any], table: Optional[List[TierSpec]] = None) -> TierSpec:
    """First-match-wins walk over the (heaviest-first) tier table.

    The table's terminal CPU row fits unconditionally, so this ALWAYS returns a
    TierSpec -- there is no "no tier" failure mode. That terminal guarantee is
    the structural backbone of fail-soft: whatever the probe says, we land on at
    worst the nomic/CPU floor.
    """
    tbl = table if table is not None else TIER_TABLE
    for tier in tbl:
        if model_fits(probe, tier):
            return tier
    # Unreachable while the table keeps its terminal catch-all, but stay safe.
    return TierSpec(
        name="CPU",
        embed_model=SAFE_DEFAULT_MODEL,
        min_gpu_mem_mb=0,
        fits=lambda p: True,
        rationale="synthetic floor (no tier matched -- table lost its catch-all)",
    )


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #

def detect(probe_torch: bool = True, table: Optional[List[TierSpec]] = None) -> Dict[str, Any]:
    """Run the full probe chain (lightest-reliable-first) and choose a tier.

    Probe order: platform/proc (OS+arch+WSL2) -> nvidia-smi --query-gpu (NEVER
    lspci) -> OPTIONAL torch.cuda (only if a torch venv exists). Then derive
    advisory gpu_features and walk the declarative tier table. The ENTIRE
    function is wrapped so an unexpected fault still returns a valid CPU/nomic
    capability dict -- detection never raises.
    """
    try:
        plat = probe_platform()
        gpu = probe_gpu()
        torch_cuda = (
            probe_torch_cuda(venv_only=True)
            if probe_torch
            else {"torch_present": False, "cuda_usable": None, "reason": "torch probe disabled"}
        )

        acc = probe_accelerator(confirm=bool(probe_torch and torch_cuda.get("torch_present")))
        probe: Dict[str, Any] = {
            "os": plat["os"],
            "arch": plat["arch"],
            "wsl2": plat["wsl2"],
            "gpu": gpu,
            "torch_cuda": torch_cuda,
            "accelerator": acc,
            "accelerators": accelerator_rows(acc, plat["os"]),
            "ram_bytes": acc.get("ram_bytes"),
            "reranker_fits": reranker_fits(acc),
        }
        probe["gpu_features"] = derive_gpu_features(gpu.get("compute_cap", 0.0))

        tier = select_tier(probe, table=table)
        model_key = tier.embed_model
        model_meta = EMBED_MODELS.get(model_key, EMBED_MODELS[SAFE_DEFAULT_MODEL])

        probe["tier"] = tier.name
        probe["chosen_embed_model"] = model_key
        probe["chosen_embed_repo"] = model_meta["repo_id"]
        probe["chosen_embed_dim"] = model_meta["embed_dim"]
        probe["tier_rationale"] = tier.rationale
        probe["degraded"] = not gpu.get("available", False) and acc.get("backend", "cpu") == "cpu"
        return probe
    except Exception as exc:  # the absolute last line of fail-soft defence
        return _emergency_floor(repr(exc))


def _emergency_floor(reason: str) -> Dict[str, Any]:
    """The all-fail-soft floor: a CPU/nomic capability dict shaped exactly like
    a normal probe, so consumers never branch on "did detection blow up". Used
    only if the orchestrator itself faults after the per-probe nets.
    """
    meta = EMBED_MODELS[SAFE_DEFAULT_MODEL]
    return {
        "os": "unknown",
        "arch": "unknown",
        "wsl2": False,
        "gpu": {"count": 0, "memory_total": 0, "compute_cap": 0.0,
                "available": False, "reason": "emergency floor: " + reason},
        "torch_cuda": {"torch_present": False, "cuda_usable": None,
                       "reason": "emergency floor"},
        "gpu_features": derive_gpu_features(0.0),
        "accelerator": {"backend": "cpu", "torch_device": "cpu", "label": "SUPPORTED-UNPROVEN", "basis": "emergency floor",
                        "candidates": [], "ram_bytes": None},
        "accelerators": [],
        "ram_bytes": None,
        "reranker_fits": {"fits": False, "accelerator": "cpu", "ram_gib": None,
                          "threshold_gib": RERANKER_RAM_THRESHOLD_GIB, "why": "emergency floor: off"},
        "tier": "CPU",
        "chosen_embed_model": SAFE_DEFAULT_MODEL,
        "chosen_embed_repo": meta["repo_id"],
        "chosen_embed_dim": meta["embed_dim"],
        "tier_rationale": "emergency fail-soft floor",
        "degraded": True,
    }


# --------------------------------------------------------------------------- #
# Manifest-block projection (what X4 folds into voidloom_manifest.json).
# --------------------------------------------------------------------------- #

def capability_block(probe: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Project a probe into the ADDITIVE ``capability`` block for the ONE
    manifest (X4). Carries the detected tier + advisory gpu_features + the
    active-embedding selection + a per-task embedding-tier map. Pure data; the
    actual manifest write is M0's act (this only shapes the block).
    """
    p = probe if probe is not None else detect()
    # Robustness: a caller may hand us a RAW probe (facts only, no tier yet).
    # Resolve the tier from the table rather than silently defaulting to CPU,
    # so capability_block is correct whether fed a full detect() result or a
    # bare fact-probe. Fail-soft -> the existing per-key defaults still hold.
    if "tier" not in p or "chosen_embed_model" not in p:
        tier = select_tier(p)
        meta = EMBED_MODELS.get(tier.embed_model, EMBED_MODELS[SAFE_DEFAULT_MODEL])
        p = dict(p)
        p.setdefault("tier", tier.name)
        p.setdefault("chosen_embed_model", tier.embed_model)
        p.setdefault("chosen_embed_repo", meta["repo_id"])
        p.setdefault("chosen_embed_dim", meta["embed_dim"])
        p.setdefault("tier_rationale", tier.rationale)
        p.setdefault("degraded", not p.get("gpu", {}).get("available", False))
    return {
        "_comment": (
            "L5-X Adaptive Loom capability probe. ADDITIVE block (X4): the "
            "install-time hardware detection result. M0 owns the manifest; this "
            "is folded in additively and never disturbs M0's hardware/model/"
            "routing blocks or M3's organs block. Modules read active_embedding "
            "+ tier here instead of probing per-module. Regenerate at install "
            "via: python3 capability/detect.py --json"
        ),
        "schema_version": 1,
        "detector": "capability/detect.py",
        "tier": p.get("tier", "CPU"),
        "os": p.get("os", "unknown"),
        "arch": p.get("arch", "unknown"),
        "wsl2": p.get("wsl2", False),
        "gpu": {
            "count": p.get("gpu", {}).get("count", 0),
            "memory_total_mb": p.get("gpu", {}).get("memory_total", 0),
            "compute_cap": p.get("gpu", {}).get("compute_cap", 0.0),
            "available": p.get("gpu", {}).get("available", False),
        },
        "gpu_features": p.get("gpu_features", derive_gpu_features(0.0)),
        "torch_cuda": p.get("torch_cuda", {}),
        "accelerator": p.get("accelerator", {}),
        "accelerators": p.get("accelerators", []),
        "ram_bytes": p.get("ram_bytes"),
        "reranker_fits": p.get("reranker_fits", {}),
        "active_embedding": {
            "model": p.get("chosen_embed_model", SAFE_DEFAULT_MODEL),
            "repo_id": p.get("chosen_embed_repo", EMBED_MODELS[SAFE_DEFAULT_MODEL]["repo_id"]),
            "embed_dim": p.get("chosen_embed_dim", EMBED_MODELS[SAFE_DEFAULT_MODEL]["embed_dim"]),
        },
        "active_embedding_table": _embedding_tier_map(p),
        "degraded": p.get("degraded", True),
        "tier_rationale": p.get("tier_rationale", ""),
    }


def _embedding_tier_map(probe: Dict[str, Any]) -> Dict[str, str]:
    """Per-task embedding-tier keys. Today every task rides the single active
    embedder; the map exists so a future split (a heavy embedder for recall, a
    light one for dedup) is a manifest edit, not a code change.
    """
    active = probe.get("chosen_embed_model", SAFE_DEFAULT_MODEL)
    return {"recall": active, "store": active, "dedup": active, "bridge": active}


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #

def _to_int(text: str) -> int:
    try:
        digits = re.findall(r"-?\d+", text)
        return int(digits[0]) if digits else 0
    except Exception:
        return 0


def _to_float(text: str) -> float:
    try:
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        return float(m.group(0)) if m else 0.0
    except Exception:
        return 0.0


def _human_summary(probe: Dict[str, Any]) -> str:
    g = probe.get("gpu", {})
    f = probe.get("gpu_features", {})
    feats = ",".join(k for k in ("bf16", "fp8", "fa2") if f.get(k)) or "none"
    lines = [
        "[detect] Voidloom Adaptive Loom -- capability probe",
        "  os/arch    : %s %s%s" % (probe.get("os"), probe.get("arch"),
                                    " (WSL2)" if probe.get("wsl2") else ""),
        "  gpu        : count=%s mem=%sMiB cap=%s available=%s" % (
            g.get("count"), g.get("memory_total"), g.get("compute_cap"), g.get("available")),
        "  gpu_features: sm=%s [%s]  (ADVISORY -- not an embedder gate)" % (
            f.get("sm"), feats),
        "  torch_cuda : %s" % probe.get("torch_cuda", {}).get("reason", ""),
        "  accelerator: %s -> %s [%s]  (%s)" % (
            probe.get("accelerator", {}).get("backend"), probe.get("accelerator", {}).get("torch_device"),
            probe.get("accelerator", {}).get("label"), probe.get("accelerator", {}).get("basis")),
        "  reranker   : %s" % probe.get("reranker_fits", {}).get("why", ""),
        "  -> tier     : %s" % probe.get("tier"),
        "  -> embedder : %s (%s, %s-dim)" % (
            probe.get("chosen_embed_model"), probe.get("chosen_embed_repo"),
            probe.get("chosen_embed_dim")),
        "  why         : %s" % probe.get("tier_rationale"),
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    json_only = "--json" in args or "--quiet" in args
    no_torch = "--no-torch" in args
    want_block = "--manifest-block" in args

    probe = detect(probe_torch=not no_torch)

    if not json_only:
        sys.stderr.write(_human_summary(probe) + "\n")

    payload = capability_block(probe) if want_block else probe
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
