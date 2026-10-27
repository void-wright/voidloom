#!/usr/bin/env python3
"""gpu_guard.py — the Dual-GPU Doctrine Gatekeeper (Voidloom L5-M0a.1).

The SINGLE sanctioned entry point to the second / display GPU. Pure-logic
gatekeeper: it decides whether Voidloom may borrow BOTH RTX 2080 Ti cards for a
spectacular short-burst ritual (e.g. a tensor-parallel INT4 14B-30B local LLM
for nightly dream-synthesis) — and otherwise degrades cleanly down a ladder to
single-card, cloud, or defer.

This module is STDLIB ONLY. It parses `nvidia-smi` and `free -m`, holds an
advisory filelock, and reasons about a policy. It NEVER imports torch, NEVER
downloads a model, NEVER runs GPU compute. `model_garden` (the next loop) CALLS
this; gpu_guard never imports model_garden.

THE DOCTRINE (Council-ratified — the BEND CRITERIA, each a checkable predicate;
touch the second/display card ONLY when ALL hold):

  1. RITUAL — the caller declares a `profile` (footprint_mb, est_duration_s,
     purpose). gpu_guard is for bursts, not daemons (recorded, not enforced).
  2. DISPLAY CARD CLEAR — probe the display card BY UUID (discovered, never by
     index, never hardcoded in logic): busy if memory.used > 4500 MiB
     (idle baseline ~2949) OR utilization.gpu > 25%. Sampled 2x ~3s apart,
     BOTH must be clear. monitor() re-checks mid-burst so the caller can abort.
  3. RAM PRE-FLIGHT — `free -m` AVAILABLE column >= footprint_mb + headroom
     (default headroom 6144 MiB).
  4. BURST FILELOCK FREE — a single advisory flock so two bursts never overlap;
     acquired on grant, released on release().
  5. SCHEDULING WINDOW — heavy dual-card bursts only in the daytime window
     (default 13:00-16:00 local, policy-configurable around when the owner uses the GPUs).
     force=True overrides the window for manual daytime testing.

VERDICT + DEGRADE LADDER:
  acquire(profile, allow_dual, force) -> Verdict{granted, device_plan, reasons,
  lock_handle}. device_plan in {'dual-card-burst', 'cuda:0-single',
  'cloud-claude-p', 'defer'}. Ladder: dual -> cuda:0-single -> cloud-claude-p
  -> defer. Auto-degrade to single when device_count < 2 OR no NVLink OR the
  display card cannot be UNAMBIGUOUSLY identified. 5090 sunset clause: a single
  >= 24 GB card disables dual. DEFAULT-REFUSE on ANY ambiguity (parse failure,
  unknown UUID, probe error, OR display-card identification ambiguity ->
  degrade DOWN the ladder, never up).

DISPLAY-CARD DISCOVERY (the load-bearing safety decision — iteration 2 rebuild):
  The protected card is the one driving Drew's display. Mis-identifying it is
  the SINGLE catastrophe the doctrine exists to prevent (probe the wrong card,
  read it headless-clear, grant dual WHILE THE OWNER USES the real display card).
  discover_display() resolves it through a refuse-on-ambiguity ladder, never a
  bare proxy guess:
    1. PRIMARY  — manifest pin. policy['display_uuid'] (install-discovered by
       M0a.2). Pinned AND present in the live probe -> authoritative. Pinned but
       ABSENT from the probe (card swapped/reseated) -> INDETERMINATE -> REFUSE
       dual (never fall through to a heuristic on a stale pin).
    2. SECONDARY — config-derived complement. The COMPUTE card is canonically
       the CVD-pinned physical index (claude_memory.py:101 sets
       CUDA_VISIBLE_DEVICES=1; wizard_config.gpu_device_index='1'). So on a
       2-GPU box where the compute index is known, the DISPLAY card is the
       complement. policy['compute_index'] (default '1') carries it. Robust on
       a machine with no manifest. (display_active is probed opportunistically
       to corroborate IF a future driver returns Enabled for exactly one card;
       it is DEPRECATED on Drew's driver -> Disabled/[deprecated] for BOTH cards
       -> it is NEVER the load-bearing signal.)
    3. TERTIARY — hardened baseline heuristic (generic/unknown machines only):
       highest idle memory.used, BUT require the top-two memory.used to differ
       by >= policy['min_baseline_gap_mb'] (default 1500). Sub-margin gap ->
       display INDETERMINATE -> REFUSE dual. Never grant dual on a sub-margin
       proxy guess (the encoder pack ~5 GB normally resides on the compute card,
       so a memory-baseline proxy INVERTS under steady state -> unsafe alone).
    4. Any discovery failure / 0 candidates / probe error -> INDETERMINATE ->
       REFUSE dual.
  INVARIANT (statically guaranteed below): there is NO reachable code path where
  device_plan='dual-card-burst' is granted while the true display card is busy.

GROUNDED HARDWARE (verified live 2026-06-10 — DISCOVERED at runtime, the UUID is
NEVER hardcoded in logic; the value below is a documentation comment + a
last-resort fallback only):
  - Display/gaming card : index 0, UUID GPU-eb02b9aa-8211-8a96-bc37-02c884e919e9,
    idle ~2949 MiB / ~4% util. PROBE THIS for "is Drew using it?".
  - Compute card        : index 1, UUID GPU-c7b2f5ec-f0eb-3bc3-d3ee-1d5bc85830f0,
    pinned via CUDA_VISIBLE_DEVICES=1 -> torch addresses it as cuda:0. The
    encoder pack (~5 GB) lives here permanently and is NOT subject to the
    doctrine. gpu_guard governs the SECOND card, never this one. (gpu_guard runs
    as a SEPARATE process where CUDA_VISIBLE_DEVICES is EMPTY -> a runtime
    CVD-exclusion would never fire, so display discovery uses the CONFIGURED
    compute index from policy, not the live CVD env.)
  - display_active is DEPRECATED on this driver: `nvidia-smi
    --query-gpu=display_active` returns 'Disabled' + '[Requested functionality
    has been deprecated]' for BOTH cards. It is therefore probed only
    opportunistically and is never load-bearing.
  - NVLink up ~51.5 GB/s. Both Turing sm_75 (no FP8/FA2). RAM: WSL ~16 GB total,
    often drops to 4-5 GB available (#1537) -> the RAM gate correctly refuses
    the big LLM most nights until a RAM upgrade.

Confinement: this file is generated in an ACE workspace; the orchestrator
applies it to the live tree after close. It is side-effect-free except for the
advisory lockfile under the system temp dir.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# rho-2, the CONFIGURED-DEFAULT layer. declared_device() reads the durable
# declarations (WIZARD_CUDA_DEVICE, the env file, the config file) and
# IGNORES the ambient variable -- which is what a separate process needs,
# because torch renumbers the visible card to cuda:0 after a pin. Its own
# docstring names this module as an intended reader.
import voidloom_device
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator, List, Optional, Tuple

# fcntl is POSIX-only (present on Linux / WSL). Import defensively so that even
# on a platform without it the module loads and DEGRADES rather than crashing.
try:
    import fcntl  # type: ignore
    _HAVE_FCNTL = True
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore
    _HAVE_FCNTL = False


# ============================================================
# Policy (a parameter with a sane default — never a literal in logic)
# ============================================================

# The known-current display UUID, for reference + last-resort fallback ONLY.
# The CODE PATH discovers the display card; it must never branch on this string.
_KNOWN_DISPLAY_UUID_FALLBACK = "GPU-eb02b9aa-8211-8a96-bc37-02c884e919e9"

# A single 2080 Ti is 11264 MiB. The 5090 sunset clause triggers on a single
# card with >= this much VRAM (24 GB) -> dual disabled, heavy synthesis single.
SUNSET_SINGLE_CARD_MIN_MIB = 24 * 1024  # 24576

# Advisory burst lock. One per machine; two bursts never overlap.
BURST_LOCK_PATH = os.path.join(tempfile.gettempdir(), "voidloom_gpu_burst.lock")

DEFAULT_POLICY: dict = {
    # None -> discover at runtime. A manifest (M0a.2) may pin the real UUID.
    # PRIMARY discovery signal when present: a pinned UUID that is also present
    # in the live probe is authoritative; a pinned UUID ABSENT from the probe
    # (stale pin) REFUSES dual rather than falling through to a heuristic.
    "display_uuid": None,
    # SECONDARY discovery signal: the canonical CVD-pinned physical compute index
    # (claude_memory.py sets CUDA_VISIBLE_DEVICES=1). On a 2-GPU box where this
    # index is present in the probe, the DISPLAY card is the complement. Stored
    # as a string to mirror wizard_config.gpu_device_index; parsed defensively.
    # None disables the config-derived path (forces pin-or-heuristic).
    "compute_index": voidloom_device.declared_device(),
    # TERTIARY (generic machines): the highest-idle-memory.used heuristic only
    # fires when the top-two cards' memory.used differ by AT LEAST this margin.
    # A sub-margin gap -> display INDETERMINATE -> refuse dual (the encoder pack
    # on the compute card can invert a bare baseline proxy, so we demand a clear
    # separation before trusting it).
    "min_baseline_gap_mb": 1500,
    # Display card is "busy" above either threshold.
    "mem_busy_mb": 4500,          # idle ~2949; 4500 leaves comfortable margin
    "util_busy_pct": 25,
    # System-RAM pre-flight headroom added on top of the model footprint.
    "ram_headroom_mb": 6144,
    # Heavy dual-card bursts allowed only in this local-hour window [start, end).
    "burst_window": (13, 16),     # 13:00-16:00 local
    # Display-clear sampling: take `samples` reads, `gap_s` apart, ALL must clear.
    "samples": 2,
    "gap_s": 3.0,
    # When True, treat a single >= SUNSET_SINGLE_CARD_MIN_MIB card as the 5090
    # sunset: dual disabled by config (not a refactor).
    "honor_sunset_clause": True,
    # Filelock location (override for tests).
    "lock_path": BURST_LOCK_PATH,
    # Require NVLink for a dual-card grant (tensor-parallel wants the bridge).
    "require_nvlink": True,
    # Defense in depth: when display discovery is NOT authoritative (i.e. not a
    # manifest pin or config-derived complement, but a heuristic guess), ALSO
    # refuse dual if ANY non-compute card reads busy. Guarantees no dual grant
    # while the true display card is busy even on a generic machine.
    "refuse_dual_if_any_noncompute_busy_when_unpinned": True,
}


def load_policy(manifest_path: Optional[str] = None) -> dict:
    """Return a policy dict: voidloom_manifest.json['gpu_guard_policy'] merged
    over DEFAULT_POLICY if present, else DEFAULT_POLICY (a copy).

    Fail-graceful: a missing / unreadable / malformed manifest -> DEFAULT_POLICY,
    never a raise. Only known keys are honored; `burst_window` lists become
    tuples. This is the contract M0a.2's manifest writer must supply.
    """
    policy = dict(DEFAULT_POLICY)
    if not manifest_path:
        return policy
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        block = data.get("gpu_guard_policy") or {}
        if not isinstance(block, dict):
            return policy
        for key, val in block.items():
            if key not in DEFAULT_POLICY:
                continue  # ignore unknown keys (default-refuse on ambiguity)
            if key == "burst_window" and isinstance(val, (list, tuple)) and len(val) == 2:
                policy[key] = (int(val[0]), int(val[1]))
            else:
                policy[key] = val
        return policy
    except Exception:
        # Malformed manifest is ambiguity -> fall back to the safe default.
        return policy


# ============================================================
# Data structures
# ============================================================

@dataclass(frozen=True)
class GpuInfo:
    index: int
    uuid: str
    name: str
    mem_total_mb: int
    mem_used_mb: int
    util_pct: int
    compute_cap: str  # e.g. "7.5"


@dataclass(frozen=True)
class BurstProfile:
    """The caller's declared ritual. footprint_mb is the model+kv footprint in
    system RAM staged before GPU load (drives the RAM pre-flight)."""
    footprint_mb: int
    est_duration_s: int
    purpose: str


@dataclass
class Verdict:
    granted: bool
    device_plan: str                       # dual-card-burst|cuda:0-single|cloud-claude-p|defer
    reasons: List[str] = field(default_factory=list)
    lock_handle: Optional["_BurstLock"] = None
    profile: Optional[BurstProfile] = None
    display_uuid: Optional[str] = None      # the discovered display UUID (probe target)

    def to_dict(self) -> dict:
        return {
            "granted": self.granted,
            "device_plan": self.device_plan,
            "reasons": list(self.reasons),
            "locked": self.lock_handle is not None and self.lock_handle.held,
            "display_uuid": self.display_uuid,
            "profile": (
                {
                    "footprint_mb": self.profile.footprint_mb,
                    "est_duration_s": self.profile.est_duration_s,
                    "purpose": self.profile.purpose,
                }
                if self.profile
                else None
            ),
        }


# The four rungs of the degrade ladder, highest -> lowest.
PLAN_DUAL = "dual-card-burst"
PLAN_SINGLE = "cuda:0-single"
PLAN_CLOUD = "cloud-claude-p"
PLAN_DEFER = "defer"
_LADDER = [PLAN_DUAL, PLAN_SINGLE, PLAN_CLOUD, PLAN_DEFER]


# ============================================================
# nvidia-smi probing (stdlib subprocess; default-refuse on any parse failure)
# ============================================================

_SMI_QUERY = (
    "index,uuid,name,memory.total,memory.used,utilization.gpu,compute_cap"
)


def _run_smi(args: List[str], timeout: float = 8.0) -> Optional[str]:
    """Run nvidia-smi with the given args, return stdout text or None on ANY
    failure (missing binary, nonzero exit, timeout). Never raises."""
    # R7: PATH first, then the driver's own directory (voidloom_device.nvidia_smi) -- a
    # systemd unit's PATH does not carry /usr/lib/wsl/lib, and "no GPUs visible" was the
    # probe failing to FIND the tool, not the host lacking the cards.
    exe = voidloom_device.nvidia_smi()
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_int_mib(token: str) -> Optional[int]:
    """Parse '2947 MiB' / '11264 MiB' / '2947' -> int MiB. None on failure."""
    if token is None:
        return None
    m = re.search(r"(\d+)", token)
    return int(m.group(1)) if m else None


def _parse_pct(token: str) -> Optional[int]:
    """Parse '4 %' / '4%' / '4' -> int percent. None on failure."""
    if token is None:
        return None
    m = re.search(r"(\d+)", token)
    return int(m.group(1)) if m else None


def probe_gpus(_smi_text: Optional[str] = None) -> List[GpuInfo]:
    """Parse `nvidia-smi --query-gpu=<_SMI_QUERY> --format=csv,noheader`.

    `_smi_text` is a test seam: pass canned CSV to bypass the subprocess. In
    production it is None and we shell out. Returns [] on ANY probe / parse
    failure (default-refuse: an empty list degrades the ladder, never crashes).
    """
    text = _smi_text
    if text is None:
        text = _run_smi(
            [f"--query-gpu={_SMI_QUERY}", "--format=csv,noheader,nounits"]
        )
        # nounits drops the " MiB" / " %" suffixes; our parsers tolerate both,
        # so a parser that gets units (older driver / mocked text) still works.
    if not text:
        return []
    gpus: List[GpuInfo] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 7:
            # Wrong column count is ambiguity -> skip this row (default-refuse).
            continue
        try:
            idx = int(re.search(r"(\d+)", parts[0]).group(1))
            uuid = parts[1]
            if not uuid.startswith("GPU-"):
                continue  # not a real UUID line
            name = parts[2]
            mem_total = _parse_int_mib(parts[3])
            mem_used = _parse_int_mib(parts[4])
            util = _parse_pct(parts[5])
            ccap = parts[6]
            if None in (mem_total, mem_used, util):
                continue
            gpus.append(
                GpuInfo(
                    index=idx,
                    uuid=uuid,
                    name=name,
                    mem_total_mb=mem_total,
                    mem_used_mb=mem_used,
                    util_pct=util,
                    compute_cap=ccap,
                )
            )
        except Exception:
            # A single unparseable row never sinks the whole probe; skip it.
            continue
    return gpus


def has_nvlink(_smi_text: Optional[str] = None) -> bool:
    """True iff `nvidia-smi nvlink --status` reports at least one active link.

    `_smi_text` is a test seam. None on a real machine shells out. Conservative:
    any failure / no link lines -> False (which degrades dual -> single, the
    safe direction).
    """
    text = _smi_text
    if text is None:
        text = _run_smi(["nvlink", "--status"])
    if not text:
        return False
    # Active links print lines like "Link 0: 25.781 GB/s". A bridge-down state
    # prints "inactive" / no GB/s lines.
    for line in text.splitlines():
        low = line.lower()
        if "link" in low and "gb/s" in low and "inactive" not in low:
            return True
    return False


def probe_display_active(_smi_text: Optional[str] = None) -> dict:
    """Opportunistic, NON-load-bearing probe of `nvidia-smi
    --query-gpu=uuid,display_active`. Returns {uuid: True/False} mapping a card
    to whether the driver claims it actively drives a display.

    DEPRECATED ON DREW'S DRIVER: this query returns 'Disabled' +
    '[Requested functionality has been deprecated]' for BOTH cards, so it cannot
    distinguish them here. We only USE the result when EXACTLY ONE card reports
    Enabled (a future / different driver); otherwise it contributes nothing and
    discovery falls through to the config-derived / heuristic ladder. It can
    therefore only ever ADD safety (corroborate), never remove it.

    `_smi_text` is a test seam. Returns {} on any failure (never raises).
    """
    text = _smi_text
    if text is None:
        text = _run_smi(
            ["--query-gpu=uuid,display_active", "--format=csv,noheader"]
        )
    if not text:
        return {}
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        uuid = parts[0]
        if not uuid.startswith("GPU-"):
            continue
        val = parts[1].lower()
        # A deprecated driver prints 'disabled' for everything; only an explicit
        # 'enabled'/'active' is treated as a positive signal.
        out[uuid] = val.startswith("enabled") or val.startswith("active")
    return out


# ============================================================
# Display-card discovery (refuse-on-ambiguity ladder — never a bare proxy guess)
# ============================================================

# Discovery "basis" tags (for the audit trail + acquire()'s authority decision).
BASIS_DECLARED = "owner-declaration"
BASIS_PIN = "manifest-pin"
BASIS_DISPLAY_ACTIVE = "display-active"
BASIS_CONFIG_COMPLEMENT = "config-derived-complement"
BASIS_HEURISTIC = "idle-baseline-heuristic"
BASIS_SINGLE_GPU = "single-gpu-topology"
BASIS_INDETERMINATE = "indeterminate"

# A discovery is AUTHORITATIVE (safe to grant dual on, given the other gates)
# only when the display card was identified by pin, an Enabled display_active
# signal, or the config-derived complement. The bare idle-baseline heuristic is
# NOT authoritative -> defense-in-depth busy-check applies.
_AUTHORITATIVE_BASES = frozenset(
    {BASIS_DECLARED, BASIS_PIN, BASIS_DISPLAY_ACTIVE, BASIS_CONFIG_COMPLEMENT}
)


@dataclass(frozen=True)
class DisplayDiscovery:
    """Result of discover_display(). `uuid` is the display card to PROBE, or None.
    `basis` records HOW it was found (audit). `indeterminate` is True when the
    display card could NOT be unambiguously identified on a multi-GPU box
    (stale pin, sub-margin heuristic, ambiguous probe) -> acquire() must REFUSE
    dual and degrade to single. `single_gpu` distinguishes a genuine 1-card
    topology from indeterminate (different refusal reason)."""
    uuid: Optional[str]
    basis: str
    indeterminate: bool = False
    single_gpu: bool = False
    detail: str = ""

    @property
    def authoritative(self) -> bool:
        return self.basis in _AUTHORITATIVE_BASES and self.uuid is not None


def _compute_index_from_policy(policy: dict) -> Optional[int]:
    """The CONFIGURED compute index (policy['compute_index'], a string mirroring
    wizard_config.gpu_device_index). Parsed defensively. We deliberately do NOT
    read the live CUDA_VISIBLE_DEVICES env: gpu_guard runs as a separate process
    where CVD is empty, so the env tells us nothing about which card is compute.
    The configured index is the durable fact."""
    raw = policy.get("compute_index")
    if raw is None:
        return None
    try:
        s = str(raw).strip()
    except Exception:
        return None
    # CVD-style "1" only; a list like "0,1" is ambiguous -> ignore (no exclusion).
    if re.fullmatch(r"\d+", s):
        return int(s)
    return None


def _declared_display(policy: dict) -> Tuple[Optional[str], str]:
    """The owner's WIZARD_DISPLAY_INDEX, read at CALL time (voidloom_device.declared_display). A test seam may put a
    (value, source) pair at policy['_declared_display']; a manifest cannot (load_policy ignores keys the default
    policy does not carry)."""
    seam = policy.get("_declared_display")
    if seam is not None:
        return seam
    try:
        return voidloom_device.declared_display()
    except Exception as e:  # never raise from discovery: an unreadable declaration is named, not guessed past
        return None, "degraded: the declaration could not be read (%s)" % type(e).__name__


def discover_display(
    gpus: List[GpuInfo],
    policy: dict,
    _display_active: Optional[dict] = None,
) -> DisplayDiscovery:
    """Identify the card driving Drew's display through a refuse-on-ambiguity
    ladder. Returns a DisplayDiscovery (see its docstring). NEVER raises.

    Ladder (degrade DOWN on ambiguity, never up):
      1. PRIMARY  — manifest pin present in probe -> authoritative.
                    Pinned but ABSENT from probe -> INDETERMINATE (refuse dual).
      2. SECONDARY — config-derived complement (display = the card that is NOT
                    the configured compute index) on a 2-GPU box -> authoritative.
                    Opportunistically corroborated by display_active if EXACTLY
                    ONE card reports Enabled (never load-bearing; deprecated here).
      3. TERTIARY — idle-baseline heuristic, gated by min_baseline_gap_mb: pick
                    the highest idle memory.used ONLY if it leads the runner-up by
                    >= the margin; sub-margin -> INDETERMINATE (refuse dual).
      4. 0 cards -> INDETERMINATE.  1 card -> single-GPU topology.

    The known live UUID is referenced ONLY in a comment + the unread fallback
    constant; no branch below compares against it (discover-don't-hardcode).
    """
    if not gpus:
        return DisplayDiscovery(
            uuid=None, basis=BASIS_INDETERMINATE, indeterminate=True,
            detail="no GPUs visible",
        )

    da = _display_active if _display_active is not None else probe_display_active()
    # Only a SINGLE Enabled card is usable as a corroborating signal.
    enabled = [u for u, on in (da or {}).items() if on]
    display_active_uuid = enabled[0] if len(enabled) == 1 else None

    # ---- 0. THE OWNER'S DECLARATION (R-8, model-choice round 1; EW5-10) ------
    # WIZARD_DISPLAY_INDEX is what the owner SAID (the installer's GPU question records it, outside the env file's
    # managed block). Declared, never inferred: it wins over every inference below -- the complement of the compute
    # card, display_active (which reads Disabled on EVERY card while the monitor sleeps), the idle heuristic -- and
    # over a manifest pin, which is itself an inference written earlier. Only when nothing is declared does the
    # ladder infer, and every result names which of the two it used.
    # One GPU (or none) is unchanged: the single-GPU topology below answers it, declared or not.
    declared, declared_src = _declared_display(policy) if len(gpus) >= 2 else (None, "one GPU: not consulted")
    if declared is None and declared_src.startswith(("unusable", "degraded")):
        return DisplayDiscovery(
            uuid=None, basis=BASIS_INDETERMINATE, indeterminate=True,
            detail="the owner's display declaration cannot be used (%s) -> refuse dual, never infer past it"
                   % declared_src,
        )
    if declared == "none":
        return DisplayDiscovery(
            uuid=None, basis=BASIS_DECLARED,
            detail="DECLARED: no card drives a monitor (WIZARD_DISPLAY_INDEX=none, %s); no display card to probe, "
                   "so no dual grant rests on one" % declared_src,
        )
    if declared is not None:
        idx = int(declared)
        hit = [g for g in gpus if g.index == idx]
        if not hit:
            return DisplayDiscovery(
                uuid=None, basis=BASIS_INDETERMINATE, indeterminate=True,
                detail="DECLARED display card %d (%s) is not in the live probe (indices %s) -> refuse dual, do not "
                       "guess another card" % (idx, declared_src, sorted(g.index for g in gpus)),
            )
        pinned = policy.get("display_uuid")
        notes = []
        if pinned and pinned != hit[0].uuid:
            notes.append("the manifest pin names another card (an earlier inference; the declaration wins)")
        cidx = _compute_index_from_policy(policy)
        if cidx is not None and len(gpus) >= 2:
            comp = [g for g in gpus if g.index != cidx]
            if len(comp) == 1 and comp[0].index != idx:
                notes.append("the complement of compute index %d would have said card %d" % (cidx, comp[0].index))
        if display_active_uuid is not None and display_active_uuid != hit[0].uuid:
            notes.append("display_active names another card")
        return DisplayDiscovery(
            uuid=hit[0].uuid, basis=BASIS_DECLARED,
            detail="DECLARED: display = card %d (%s)%s (authoritative)"
                   % (idx, declared_src, ("; " + "; ".join(notes)) if notes else ""),
        )

    # ---- 1. PRIMARY: manifest pin (nothing declared: from here on the ladder INFERS) --
    pinned = policy.get("display_uuid")
    if pinned:
        if any(g.uuid == pinned for g in gpus):
            return DisplayDiscovery(
                uuid=pinned, basis=BASIS_PIN,
                detail="pinned UUID present in live probe (authoritative)",
            )
        # Stale pin: the pinned card is NOT in the probe (swapped / reseated).
        # Honor the doctrine: do NOT fall through to a heuristic on a stale pin.
        return DisplayDiscovery(
            uuid=None, basis=BASIS_INDETERMINATE, indeterminate=True,
            detail=(
                "pinned display_uuid is ABSENT from the live probe "
                "(stale/swapped) -> refuse dual, do not guess another card"
            ),
        )

    # Single-GPU topology is a clean, distinct outcome (not indeterminate).
    if len(gpus) == 1:
        return DisplayDiscovery(
            uuid=gpus[0].uuid, basis=BASIS_SINGLE_GPU, single_gpu=True,
            detail="only one GPU visible -> single-GPU topology",
        )

    # ---- 2. SECONDARY: config-derived complement -----------------------------
    compute_idx = _compute_index_from_policy(policy)
    if compute_idx is not None:
        present_idxs = {g.index for g in gpus}
        complement = [g for g in gpus if g.index != compute_idx]
        # Need the configured compute index to actually be present, and exactly
        # one complement card, for an unambiguous identification.
        if compute_idx in present_idxs and len(complement) == 1:
            chosen = complement[0]
            # Opportunistic corroboration: if display_active named exactly one
            # card and it DISAGREES with the complement, that is a contradiction
            # -> refuse rather than pick the wrong card.
            if display_active_uuid is not None and display_active_uuid != chosen.uuid:
                return DisplayDiscovery(
                    uuid=None, basis=BASIS_INDETERMINATE, indeterminate=True,
                    detail=(
                        "config-derived complement disagrees with the single "
                        "display_active=Enabled card -> ambiguous, refuse dual"
                    ),
                )
            corro = (
                " (corroborated by display_active=Enabled)"
                if display_active_uuid == chosen.uuid
                else ""
            )
            return DisplayDiscovery(
                uuid=chosen.uuid, basis=BASIS_CONFIG_COMPLEMENT,
                detail=(
                    f"INFERRED (no WIZARD_DISPLAY_INDEX declared): display = complement of configured compute index "
                    f"{compute_idx}{corro} (authoritative)"
                ),
            )

    # If display_active gave us exactly one Enabled card and we had no usable
    # config complement, that Enabled card IS authoritative on its own.
    if display_active_uuid is not None:
        return DisplayDiscovery(
            uuid=display_active_uuid, basis=BASIS_DISPLAY_ACTIVE,
            detail="exactly one card reports display_active=Enabled (authoritative)",
        )

    # ---- 3. TERTIARY: hardened idle-baseline heuristic (generic machines) -----
    # Highest idle memory.used wins, but ONLY if it leads the runner-up by the
    # configured margin. A sub-margin gap is INDETERMINATE: the encoder pack can
    # invert a bare baseline proxy, so we never grant dual on a close guess.
    ranked = sorted(gpus, key=lambda g: (-g.mem_used_mb, g.index))
    top, runner_up = ranked[0], ranked[1]
    gap = top.mem_used_mb - runner_up.mem_used_mb
    margin = int(policy.get("min_baseline_gap_mb", 1500) or 0)
    if gap < margin:
        return DisplayDiscovery(
            uuid=None, basis=BASIS_INDETERMINATE, indeterminate=True,
            detail=(
                f"idle-baseline gap {gap}MiB < margin {margin}MiB between the "
                f"top-two cards -> display INDETERMINATE -> refuse dual"
            ),
        )
    return DisplayDiscovery(
        uuid=top.uuid, basis=BASIS_HEURISTIC,
        detail=(
            f"idle-baseline heuristic: card {top.index} leads by {gap}MiB "
            f">= margin {margin}MiB (NON-authoritative -> busy-check applies)"
        ),
    )


def discover_display_uuid(gpus: List[GpuInfo], policy: dict) -> Optional[str]:
    """Back-compat thin wrapper returning just the UUID (or None). Prefer
    discover_display() for the full basis/indeterminate signal. Kept so any
    external caller of the iter-1 name keeps working."""
    return discover_display(gpus, policy).uuid


# ============================================================
# Display-card probe (BY UUID — the heart of "is Drew using it?")
# ============================================================

def probe_display_card(uuid: str, _smi_text: Optional[str] = None) -> Optional[dict]:
    """Single sample of one card identified BY UUID. Returns
    {'uuid','mem_used_mb','util_pct'} or None on any failure / unknown UUID.

    `_smi_text` is a test seam (canned full-probe CSV). Production: None -> live
    probe_gpus(), then match the row whose uuid == `uuid`.
    """
    if not uuid:
        return None
    gpus = probe_gpus(_smi_text)
    for g in gpus:
        if g.uuid == uuid:
            return {
                "uuid": g.uuid,
                "mem_used_mb": g.mem_used_mb,
                "util_pct": g.util_pct,
            }
    # Unknown UUID -> None (default-refuse: caller treats as "cannot confirm
    # clear" -> not clear).
    return None


def _sample_is_clear(sample: Optional[dict], policy: dict) -> Tuple[bool, str]:
    """One sample's verdict + a human reason."""
    if sample is None:
        return False, "display-probe-failed (unknown UUID or smi error)"
    mem = sample["mem_used_mb"]
    util = sample["util_pct"]
    mem_busy = mem > policy["mem_busy_mb"]
    util_busy = util > policy["util_busy_pct"]
    if mem_busy or util_busy:
        why = []
        if mem_busy:
            why.append(f"mem.used={mem}MiB > {policy['mem_busy_mb']}")
        if util_busy:
            why.append(f"util={util}% > {policy['util_busy_pct']}%")
        return False, "display-BUSY (" + ", ".join(why) + ")"
    return True, f"display-clear (mem.used={mem}MiB, util={util}%)"


def is_display_clear(
    uuid: str,
    samples: int = 2,
    gap_s: float = 3.0,
    policy: Optional[dict] = None,
    _sample_provider=None,
) -> Tuple[bool, List[str]]:
    """Probe the display card BY UUID `samples` times, `gap_s` apart. Clear iff
    EVERY sample is clear (a single busy reading -> BUSY).

    `_sample_provider` is the test seam: a callable(i) -> sample-dict|None that
    feeds canned samples (so tests need no GPU and no sleeping). Production: None
    -> probe_display_card(uuid) per sample with a real time.sleep(gap_s) between.

    Default-refuse: a None sample (probe failure / unknown UUID) counts as BUSY.
    """
    policy = policy or DEFAULT_POLICY
    samples = max(1, int(samples))
    reasons: List[str] = []
    all_clear = True
    for i in range(samples):
        if _sample_provider is not None:
            sample = _sample_provider(i)
        else:
            sample = probe_display_card(uuid)
        clear, why = _sample_is_clear(sample, policy)
        reasons.append(f"sample{i + 1}: {why}")
        if not clear:
            all_clear = False
        # Sleep between samples (not after the last one). Skipped under the test
        # seam so the suite is instant + deterministic.
        if _sample_provider is None and i < samples - 1:
            time.sleep(gap_s)
    return all_clear, reasons


def monitor(
    uuid: str,
    poll_s: float = 5.0,
    policy: Optional[dict] = None,
    _sample_provider=None,
) -> Iterator[Tuple[bool, str]]:
    """A generator the caller drives DURING a burst to detect Drew arriving.

    Yields (still_clear: bool, reason: str) once per poll. The caller loops over
    it and, on the first `still_clear is False`, aborts-and-releases the burst.
    A probe failure yields (False, ...) -> default-refuse: ambiguity aborts the
    ritual (cheaper than crashing Drew's display).

    `_sample_provider` (test seam) is a callable(i)->sample feeding canned reads;
    when provided, no real sleep happens and the generator stops when the
    provider returns the sentinel StopIteration-by-None-twice (tests pass a
    finite provider). Production: polls forever with time.sleep(poll_s) until the
    caller stops iterating.
    """
    policy = policy or DEFAULT_POLICY
    i = 0
    while True:
        if _sample_provider is not None:
            sample = _sample_provider(i)
            if sample is StopIteration:
                return
        else:
            sample = probe_display_card(uuid)
        clear, why = _sample_is_clear(sample, policy)
        yield clear, why
        if not clear:
            # The caller is responsible for releasing; we stop the generator so
            # a naive `for` loop terminates on the abort signal.
            return
        i += 1
        if _sample_provider is None:
            time.sleep(poll_s)


# ============================================================
# System-RAM pre-flight (free -m AVAILABLE column)
# ============================================================

def _read_available_mb(_free_text: Optional[str] = None) -> Optional[int]:
    """Return `free -m` AVAILABLE column for Mem, or None on any failure.

    `_free_text` is a test seam (canned `free -m` output). Production: shells out.
    """
    text = _free_text
    if text is None:
        exe = shutil.which("free")
        if not exe:
            return None
        try:
            proc = subprocess.run(
                [exe, "-m"], capture_output=True, text=True, timeout=5.0
            )
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        text = proc.stdout
    if not text:
        return None
    lines = text.splitlines()
    header = None
    avail_col = None
    for line in lines:
        toks = line.split()
        if not toks:
            continue
        if toks[0].lower().startswith("total") or "available" in [t.lower() for t in toks]:
            # Header row: locate the 'available' column position.
            header = [t.lower() for t in toks]
            if "available" in header:
                avail_col = header.index("available")
            continue
        if toks[0].rstrip(":").lower() == "mem":
            # The Mem row. Columns AFTER the label align with the header tokens.
            # `free -m` header has no leading label, so header index N maps to
            # data index N+1 (the label shifts data right by one).
            if avail_col is not None:
                data_idx = avail_col + 1
                if data_idx < len(toks):
                    try:
                        return int(toks[data_idx])
                    except ValueError:
                        return None
            # Fallback: the classic 6-field `free -m` layout puts available last.
            try:
                return int(toks[-1])
            except ValueError:
                return None
    return None


def preflight_ram(
    footprint_mb: int,
    headroom_mb: int = 6144,
    _free_text: Optional[str] = None,
) -> Tuple[bool, Optional[int]]:
    """Pass iff `free -m` AVAILABLE >= footprint_mb + headroom_mb.

    Returns (ok, available_mb). available_mb is None if the probe failed, in
    which case ok is False (default-refuse: cannot confirm RAM -> refuse).
    """
    available = _read_available_mb(_free_text)
    if available is None:
        return False, None
    need = int(footprint_mb) + int(headroom_mb)
    return available >= need, available


# ============================================================
# Advisory burst filelock (fcntl.flock, non-blocking)
# ============================================================

class _BurstLock:
    """A non-blocking advisory flock. acquire() -> True iff we got it; if another
    burst holds it, returns False (the caller degrades). release() is idempotent.
    """

    def __init__(self, path: str):
        self.path = path
        self._fh = None
        self.held = False

    def acquire(self) -> bool:
        if not _HAVE_FCNTL:
            # No fcntl (non-POSIX) -> we cannot prove exclusivity. Default-refuse
            # the LOCK gate: report not-acquired so dual degrades, never falsely
            # claim exclusivity.
            return False
        try:
            self._fh = open(self.path, "w")
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            # Held by another burst (or open failed) -> not acquired.
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
            return False
        except Exception:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
            return False
        # Stamp who/when for diagnostics (advisory; not load-bearing).
        try:
            self._fh.write(
                json.dumps(
                    {"pid": os.getpid(), "acquired_at": datetime.now().isoformat()}
                )
            )
            self._fh.flush()
        except Exception:
            pass
        self.held = True
        return True

    def release(self) -> None:
        if self._fh is not None:
            try:
                if _HAVE_FCNTL:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        self.held = False


# ============================================================
# The verdict assembler — acquire() walks the BEND CRITERIA + degrade ladder
# ============================================================

def _degrade(
    reasons: List[str],
    profile: BurstProfile,
    display_uuid: Optional[str],
    plan: str,
) -> Verdict:
    """Build a NON-granted verdict at the given rung (single/cloud/defer)."""
    return Verdict(
        granted=(plan == PLAN_SINGLE),  # single-card is still a usable plan
        device_plan=plan,
        reasons=reasons,
        lock_handle=None,
        profile=profile,
        display_uuid=display_uuid,
    )


def acquire(
    profile: BurstProfile,
    allow_dual: bool = True,
    force: bool = False,
    policy: Optional[dict] = None,
    *,
    _gpus: Optional[List[GpuInfo]] = None,
    _nvlink: Optional[bool] = None,
    _display_sample_provider=None,
    _free_text: Optional[str] = None,
    _now_hour: Optional[int] = None,
    _display_active: Optional[dict] = None,
) -> Verdict:
    """Walk the five BEND criteria + the degrade ladder and return a Verdict.

    Underscore-prefixed kwargs are TEST SEAMS so the whole decision runs with no
    GPU, no sleeping, and no real clock:
      _gpus                    -> bypass probe_gpus()
      _nvlink                  -> bypass has_nvlink()
      _display_sample_provider -> callable(i)->sample for is_display_clear()
      _free_text               -> canned `free -m` text for preflight_ram()
      _now_hour                -> local hour for the scheduling-window gate
      _display_active          -> canned {uuid: bool} for discover_display()

    DEFAULT-REFUSE on ambiguity: any probe that returns None/[]/raises pushes the
    verdict DOWN the ladder (dual -> single -> cloud -> defer), never up. The
    only path to a dual grant is ALL five criteria passing AND the display card
    being UNAMBIGUOUSLY identified (a stale pin / sub-margin heuristic refuses
    dual). There is no reachable path to a dual grant while the true display
    card is busy (see the static argument in generation_report.md).
    """
    policy = policy or DEFAULT_POLICY
    reasons: List[str] = []

    # --- BEND #1: the ritual itself is declared (a typed profile) ------------
    if not isinstance(profile, BurstProfile):
        # No declared ritual is total ambiguity -> defer.
        return Verdict(
            granted=False,
            device_plan=PLAN_DEFER,
            reasons=["BEND#1 FAIL: no BurstProfile declared -> defer"],
            profile=None,
        )
    reasons.append(
        f"BEND#1 OK: ritual declared (footprint={profile.footprint_mb}MiB, "
        f"est={profile.est_duration_s}s, purpose={profile.purpose!r})"
    )

    # --- Probe the topology (default-refuse on probe failure) ----------------
    gpus = _gpus if _gpus is not None else probe_gpus()
    if not gpus:
        # Cannot see ANY GPU -> we cannot even run single-card with confidence;
        # degrade to cloud (CPU-free path) rather than risk a bad local plan.
        reasons.append("TOPOLOGY FAIL: no GPUs visible -> cloud-claude-p")
        return _degrade(reasons, profile, None, PLAN_CLOUD)

    device_count = len(gpus)

    # --- Identify the DISPLAY card through the refuse-on-ambiguity ladder -----
    disc = discover_display(gpus, policy, _display_active=_display_active)
    display_uuid = disc.uuid
    reasons.append(f"DISCOVERY: basis={disc.basis} uuid={display_uuid} ({disc.detail})")

    # Three distinct multi-GPU outcomes, each its own honest refusal reason:
    #   - single_gpu        : a genuine 1-card topology (cuda:0-single).
    #   - indeterminate     : >=2 cards but the display card could NOT be safely
    #                         identified (stale pin / sub-margin) -> refuse dual.
    #   - authoritative/heuristic uuid : a usable display card to probe.
    display_indeterminate = disc.indeterminate

    # --- Sunset clause: a single >= 24 GB card disables dual by config -------
    sunset = False
    if policy.get("honor_sunset_clause", True):
        big_cards = [g for g in gpus if g.mem_total_mb >= SUNSET_SINGLE_CARD_MIN_MIB]
        if device_count == 1 and big_cards:
            sunset = True
            reasons.append(
                f"SUNSET clause: single {big_cards[0].mem_total_mb}MiB card "
                f">= {SUNSET_SINGLE_CARD_MIN_MIB}MiB -> dual disabled (config event)"
            )

    # --- Auto-degrade-to-single topology checks ------------------------------
    nvlink = _nvlink if _nvlink is not None else (
        has_nvlink() if policy.get("require_nvlink", True) else True
    )

    dual_possible = (
        allow_dual
        and not sunset
        and device_count >= 2
        and (nvlink or not policy.get("require_nvlink", True))
        and not display_indeterminate
        and display_uuid is not None
    )

    if not allow_dual:
        reasons.append("DUAL not requested (allow_dual=False) -> single-card")
    if device_count < 2:
        reasons.append(f"AUTO-SINGLE: device_count={device_count} < 2 (single-GPU topology)")
    if policy.get("require_nvlink", True) and not nvlink and device_count >= 2:
        reasons.append("AUTO-SINGLE: NVLink absent/down -> no tensor-parallel")
    if display_indeterminate and device_count >= 2:
        reasons.append(
            "AUTO-SINGLE: display card INDETERMINATE (could not safely identify "
            "which card drives the display) -> refuse dual (default-refuse on "
            "discovery ambiguity)"
        )

    if not dual_possible:
        # We cannot bend to dual on topology/config/identification grounds.
        # We can still run single-card iff we have at least one usable card we
        # are NOT at risk of confusing with the display target. With >= 1 GPU
        # visible, single-card on the compute card is always safe (the doctrine
        # governs only the SECOND/display card; single-card never touches it).
        if device_count >= 1:
            reasons.append("PLAN: cuda:0-single (dual not possible by topology/config/identification)")
            return _degrade(reasons, profile, display_uuid, PLAN_SINGLE)
        # No cards usable -> cloud.
        reasons.append("PLAN: cloud-claude-p (no usable GPU plan)")
        return _degrade(reasons, profile, display_uuid, PLAN_CLOUD)

    # === From here, dual is topologically possible. Walk BEND #2,#3,#4,#5. ===

    # --- BEND #5: scheduling window (checked early; cheap; force overrides) ---
    win_start, win_end = policy["burst_window"]
    now_hour = _now_hour if _now_hour is not None else datetime.now().hour
    in_window = win_start <= now_hour < win_end
    if not in_window and not force:
        reasons.append(
            f"BEND#5 FAIL: hour={now_hour} outside burst window "
            f"[{win_start},{win_end}) and force=False -> degrade to single"
        )
        return _degrade(reasons, profile, display_uuid, PLAN_SINGLE)
    reasons.append(
        f"BEND#5 OK: hour={now_hour}"
        + (
            f" in window [{win_start},{win_end})"
            if in_window
            else f" outside window but force=True override"
        )
    )

    # --- BEND #2: display card CLEAR (probe BY UUID, 2 samples ~3s apart) -----
    clear, clear_reasons = is_display_clear(
        display_uuid,
        samples=policy["samples"],
        gap_s=policy["gap_s"],
        policy=policy,
        _sample_provider=_display_sample_provider,
    )
    reasons.extend([f"BEND#2 {r}" for r in clear_reasons])
    if not clear:
        reasons.append(
            "BEND#2 FAIL: display card busy/unconfirmed -> NOT touching it -> single"
        )
        return _degrade(reasons, profile, display_uuid, PLAN_SINGLE)
    reasons.append(f"BEND#2 OK: display {display_uuid} clear across all samples")

    # --- BEND #2b: DEFENSE IN DEPTH — topology-snapshot busy cross-check -------
    # A burst uses BOTH cards tensor-parallel, so the INVARIANT we must guarantee
    # is: NO non-compute card is busy when we grant dual. Cross-check every
    # non-compute card in the SAME topology snapshot discovery was computed from.
    # If ANY non-compute card reads busy, refuse dual. This runs for EVERY basis
    # (not only the heuristic): it is a free corroboration against the data we
    # already hold, and it closes two distinct holes —
    #   (1) a NON-authoritative heuristic that mis-picked the display card: the
    #       TRUE (busy) display is also a non-compute card, so its busy state is
    #       caught here even though we probed the wrong UUID at BEND#2;
    #   (2) an AUTHORITATIVE discovery whose chosen card the snapshot ALREADY
    #       shows busy: we refuse on the snapshot rather than depending solely on
    #       the live BEND#2 re-probe agreeing with it.
    # The ONLY card exempted is the configured compute index (the encoder's home,
    # not governed by the doctrine). With compute_index unknown (None), NO card
    # is exempt -> strictly safer.
    if policy.get("refuse_dual_if_any_noncompute_busy_when_unpinned", True):
        compute_idx = _compute_index_from_policy(policy)
        mem_busy_mb = policy["mem_busy_mb"]
        util_busy_pct = policy["util_busy_pct"]
        busy_others = [
            g for g in gpus
            if g.index != compute_idx
            and (g.mem_used_mb > mem_busy_mb or g.util_pct > util_busy_pct)
        ]
        if busy_others:
            b = busy_others[0]
            reasons.append(
                f"BEND#2b FAIL (defense-in-depth snapshot cross-check, "
                f"basis={disc.basis}): non-compute card index={b.index} "
                f"uuid={b.uuid} reads BUSY in the topology snapshot "
                f"(mem={b.mem_used_mb}MiB util={b.util_pct}%) -> a non-compute "
                f"card being busy means a display card is in use (or a mis-pick "
                f"is hiding one) -> refuse dual, degrade to single"
            )
            return _degrade(reasons, profile, display_uuid, PLAN_SINGLE)
        reasons.append(
            f"BEND#2b OK (basis={disc.basis}): no non-compute card reads busy in "
            "the topology snapshot (defense-in-depth satisfied)"
        )

    # --- BEND #3: system-RAM pre-flight --------------------------------------
    ram_ok, available = preflight_ram(
        profile.footprint_mb, policy["ram_headroom_mb"], _free_text=_free_text
    )
    need = profile.footprint_mb + policy["ram_headroom_mb"]
    if not ram_ok:
        avail_str = "PROBE-FAILED" if available is None else f"{available}MiB"
        reasons.append(
            f"BEND#3 FAIL: RAM available={avail_str} < need={need}MiB "
            f"(footprint {profile.footprint_mb} + headroom {policy['ram_headroom_mb']}) "
            f"-> degrade to single"
        )
        return _degrade(reasons, profile, display_uuid, PLAN_SINGLE)
    reasons.append(
        f"BEND#3 OK: RAM available={available}MiB >= need={need}MiB"
    )

    # --- BEND #4: burst filelock free (acquire on grant) ---------------------
    lock = _BurstLock(policy.get("lock_path", BURST_LOCK_PATH))
    if not lock.acquire():
        reasons.append(
            "BEND#4 FAIL: burst filelock held by another ritual (or no fcntl) "
            "-> defer (a second concurrent dual-burst is never allowed)"
        )
        return _degrade(reasons, profile, display_uuid, PLAN_DEFER)
    reasons.append(f"BEND#4 OK: acquired burst lock {lock.path}")

    # === ALL FIVE BEND CRITERIA PASSED -> grant the dual-card burst. =========
    reasons.append("VERDICT: GRANTED dual-card-burst (all 5 BEND criteria pass)")
    return Verdict(
        granted=True,
        device_plan=PLAN_DUAL,
        reasons=reasons,
        lock_handle=lock,
        profile=profile,
        display_uuid=display_uuid,
    )


def release(verdict: Verdict) -> None:
    """Release the burst lock held by a granted dual verdict. Idempotent; safe
    on a non-granted / lockless verdict. Never raises."""
    if verdict is None:
        return
    lock = getattr(verdict, "lock_handle", None)
    if lock is not None:
        try:
            lock.release()
        except Exception:
            pass
    verdict.lock_handle = None


# ============================================================
# --live diagnostic (guarded; print-only; machine-state dependent)
# ============================================================

def _live_diagnostic() -> int:
    """Probe the REAL machine and print the verdict for a sample 18 GB dual
    profile. Print-only (no assertions): the outcome depends on live state.
    Returns a process exit code (0 always, since this is a diagnostic)."""
    print("=" * 70)
    print("gpu_guard --live : Dual-GPU Doctrine real-machine diagnostic")
    print("=" * 70)

    policy = DEFAULT_POLICY
    gpus = probe_gpus()
    print(f"\n[probe_gpus] {len(gpus)} card(s) visible:")
    for g in gpus:
        print(
            f"  index={g.index} uuid={g.uuid} {g.name} "
            f"total={g.mem_total_mb}MiB used={g.mem_used_mb}MiB "
            f"util={g.util_pct}% cc={g.compute_cap}"
        )

    nvlink = has_nvlink()
    print(f"\n[has_nvlink] {nvlink}")

    da = probe_display_active()
    print(f"\n[probe_display_active] {da or '{} (deprecated/empty on this driver)'}")

    disc = discover_display(gpus, policy, _display_active=da)
    display_uuid = disc.uuid
    print(
        f"\n[discover_display] basis={disc.basis} uuid={display_uuid} "
        f"authoritative={disc.authoritative} indeterminate={disc.indeterminate}"
    )
    print(f"    detail: {disc.detail}")
    if display_uuid and not disc.indeterminate:
        clear, clear_reasons = is_display_clear(
            display_uuid,
            samples=policy["samples"],
            gap_s=policy["gap_s"],
            policy=policy,
        )
        print(f"[is_display_clear] BY UUID {display_uuid}: clear={clear}")
        for r in clear_reasons:
            print(f"    {r}")

    sample_footprint = 18 * 1024  # 18 GB INT4 14B-class tensor-parallel ritual
    profile = BurstProfile(
        footprint_mb=sample_footprint,
        est_duration_s=600,
        purpose="nightly-dream-synthesis (18GB INT4 tensor-parallel) [diagnostic]",
    )
    ram_ok, available = preflight_ram(sample_footprint, policy["ram_headroom_mb"])
    need = sample_footprint + policy["ram_headroom_mb"]
    print(
        f"\n[preflight_ram] footprint={sample_footprint}MiB + headroom="
        f"{policy['ram_headroom_mb']}MiB = need {need}MiB ; available="
        f"{available}MiB ; pass={ram_ok}"
    )

    now_hour = datetime.now().hour
    print(f"\n[scheduling] local hour={now_hour}, window={policy['burst_window']}")

    verdict = acquire(profile, allow_dual=True, force=False, policy=policy)
    print("\n[acquire] verdict:")
    print(json.dumps(verdict.to_dict(), indent=2))
    print("\n[acquire] reasons (gate-by-gate):")
    for r in verdict.reasons:
        print(f"  - {r}")
    # Always release any lock we may have taken (we won't on a refusal, but be safe).
    release(verdict)

    print(
        "\nExpected on the reference two-card host: display card discovered via the "
        "CONFIG-DERIVED COMPLEMENT (compute=index1 -> display=index0), "
        "AUTHORITATIVE on its own. display_active is treated as opportunistic "
        "only (it is unreliable/deprecated under some query forms -> it can only "
        "corroborate, never decide). The 18GB dual profile is REFUSED tonight "
        "either on the display-busy gate (BEND#2, if the display card is in use) "
        "or the RAM gate (~12GB available < 18+6=24GB) -> device_plan="
        "cuda:0-single. (Print-only diagnostic; exact gate depends on live state.)"
    )
    print("=" * 70)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="gpu_guard — the Dual-GPU Doctrine gatekeeper (diagnostic CLI)."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the real-machine probe + print a sample verdict (print-only).",
    )
    args = parser.parse_args(argv)
    if args.live:
        return _live_diagnostic()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
