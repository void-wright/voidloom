#!/usr/bin/env python3
"""voidloom_manifest.py — the single-source-of-truth writer + schema + defensive
reader for `voidloom_manifest.json` (L5-M0a.2).

The manifest replaces try/except-import capability selection with a DECLARED
fact: hardware tier, the model registry + enable flags, the gpu_guard_policy
(carrying the display_uuid PRIMARY pin that closes gpu_guard's last residual
risk), per-task device_routing, and a `probed` block (model_garden.doctor
output). The priority is: manifest > parallel scripts > try/except.

WRITER discipline (this module is the SOLE writer):
  * MERGE-ON-UPGRADE — read the existing file, update the known blocks, write
    back. NEVER clobber an unknown pre-existing key (forward-compat: a newer
    installer may have written blocks this version does not know about).
  * DISCOVER, never hardcode — `discover=True` probes nvidia-smi and pins
    `gpu_guard_policy.display_uuid` = the UUID of the card whose nvidia-smi index
    is NOT the compute index (compute_index, default "1"). On Drew's box the
    compute card is index 1 (CUDA_VISIBLE_DEVICES=1) → the display card is index
    0 → its UUID gets pinned. The known live UUID is NEVER a literal in the
    discovery logic; it is discovered at install time, machine-local.
  * Machine-LOCAL — the SCHEMA federates across machines, the PROBED values do
    not. The manifest is never shipped as federated rows.

READER discipline (every consumer reads, none writes):
  * `read_manifest(path)` returns the file's dict, or DEFAULT_MANIFEST on a
    missing/malformed file. NEVER raises.

The M0a.1 contract (the headline): the `gpu_guard_policy` block carries EXACTLY
the keys the live gpu_guard.load_policy() honors (display_uuid, compute_index,
mem_busy_mb, util_busy_pct, ram_headroom_mb, burst_window, samples, gap_s,
honor_sunset_clause, require_nvlink, lock_path — plus the extra
min_baseline_gap_mb + refuse_dual_if_any_noncompute_busy_when_unpinned that
gpu_guard's DEFAULT_POLICY also reads). Feeding a discover=True manifest to
gpu_guard.load_policy() then makes gpu_guard's display discovery flip to
basis=manifest-pin (AUTHORITATIVE), the durable independent pin that overrides
its config-derived heuristic entirely.

Confinement: generated in an ACE workspace. The orchestrator runs the install-
time write_manifest against the LIVE tree after close; this module NEVER writes
the live file itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# rho-2, the CONFIGURED-DEFAULT layer. declared_device() reads the durable
# declarations (WIZARD_CUDA_DEVICE, the env file, the config file) and
# IGNORES the ambient variable -- which is what a separate process needs,
# because torch renumbers the visible card to cuda:0 after a pin. Its own
# docstring names this module as an intended reader.
import voidloom_device
from typing import Any, Dict, List, Optional, Tuple

MANIFEST_VERSION = 1


# ============================================================
# DEFAULT_MANIFEST — the full schema with sane GPU-MID defaults.
# ============================================================
# GPU-MID = the dual-2080-Ti tier this brain runs on. Every block has a safe
# default so a machine with NO probe (no nvidia-smi) still gets a usable manifest
# that gpu_guard reads as DEFAULT_POLICY (display_uuid=None → discover at runtime).

DEFAULT_MANIFEST: Dict[str, Any] = {
    "manifest_version": MANIFEST_VERSION,
    "hardware_tier": "GPU-MID",  # GPU-MID | GPU-HIGH | GPU-LOW | CPU-ONLY
    # ---- gpu_guard_policy: EXACTLY the keys gpu_guard.load_policy honors -------
    # (mirrors gpu_guard.DEFAULT_POLICY so the manifest is a drop-in policy source.)
    "gpu_guard_policy": {
        # PRIMARY display pin. None until discover=True probes + pins it. A pinned
        # UUID present in the live probe → gpu_guard basis=manifest-pin (authoritative).
        "display_uuid": None,
        # The CVD-pinned physical compute index (claude_memory.py sets CVD=1).
        # String to mirror wizard_config.gpu_device_index.
        "compute_index": voidloom_device.declared_device(),
        # Display card "busy" thresholds.
        "mem_busy_mb": 4500,
        "util_busy_pct": 25,
        # System-RAM pre-flight headroom on top of the model footprint.
        "ram_headroom_mb": 6144,
        # Heavy dual-card bursts only in this local-hour window [start, end).
        "burst_window": [13, 16],
        # Display-clear sampling.
        "samples": 2,
        "gap_s": 3.0,
        # A single >= 24GB card disables dual (5090 sunset clause).
        "honor_sunset_clause": True,
        # Require NVLink for a dual-card grant.
        "require_nvlink": True,
        # Advisory burst lock path (None → gpu_guard's tempdir default).
        "lock_path": None,
        # The extra DEFAULT_POLICY keys gpu_guard also honors (defense-in-depth).
        "min_baseline_gap_mb": 1500,
        "refuse_dual_if_any_noncompute_busy_when_unpinned": True,
    },
    # ---- models: registry + per-model enable flags ----------------------------
    # Populated from model_garden.MODEL_REGISTRY at write time (discover or not).
    "models": {},
    # ---- device_routing: task → device-class ----------------------------------
    "device_routing": {
        "embed": "cuda:0-single",
        "rerank": "cuda:0-single",
        "nli": "cuda:0-single",          # belief-engine contradiction edges
        "ner": "cuda:0-single",          # same_as entity linking
        "salience": "cuda:0-single",     # zero-shot gate
        "affect": "cuda:0-single",       # go_emotions
        "dream_synthesis": "dual-card-burst",  # the M0b burst LLM (gpu_guard-gated)
        "default": "cloud-claude-p",
    },
    # ---- probed: model_garden.doctor output (machine-local) -------------------
    "probed": {},
    # ---- discovery audit (how display_uuid was pinned; machine-local) ----------
    "_discovery": {
        "display_discovered": False,
        "basis": "not-yet-discovered",
        "detail": "",
    },
}


# ============================================================
# nvidia-smi probe (stdlib subprocess; fail-graceful → [] on any failure).
# ============================================================

_SMI_QUERY = "index,uuid,name,memory.total,memory.used,utilization.gpu,compute_cap"


def _run_smi(args: List[str], timeout: float = 8.0) -> Optional[str]:
    """Run nvidia-smi, return stdout or None on ANY failure. Never raises."""
    # R7: PATH first, then the driver's own directory (voidloom_device.nvidia_smi) -- a
    # systemd unit's PATH does not carry /usr/lib/wsl/lib, and "no GPUs visible" was the
    # probe failing to FIND the tool, not the host lacking the cards.
    exe = voidloom_device.nvidia_smi()
    if not exe:
        return None
    try:
        proc = subprocess.run([exe] + args, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def probe_gpus(_smi_text: Optional[str] = None) -> List[Dict[str, Any]]:
    """Parse nvidia-smi into [{index, uuid, name, mem_total_mb, mem_used_mb,
    util_pct, compute_cap}]. `_smi_text` is a TEST SEAM (canned CSV). Returns []
    on any probe/parse failure (so discovery degrades, never crashes).
    """
    text = _smi_text
    if text is None:
        text = _run_smi([f"--query-gpu={_SMI_QUERY}", "--format=csv,noheader,nounits"])
    if not text:
        return []
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 7:
            continue
        try:
            m = re.search(r"(\d+)", parts[0])
            if not m:
                continue
            idx = int(m.group(1))
            uuid = parts[1]
            if not uuid.startswith("GPU-"):
                continue
            mt = re.search(r"(\d+)", parts[3])
            mu = re.search(r"(\d+)", parts[4])
            ut = re.search(r"(\d+)", parts[5])
            if not (mt and mu and ut):
                continue
            out.append({
                "index": idx,
                "uuid": uuid,
                "name": parts[2],
                "mem_total_mb": int(mt.group(1)),
                "mem_used_mb": int(mu.group(1)),
                "util_pct": int(ut.group(1)),
                "compute_cap": parts[6],
            })
        except Exception:
            continue
    return out


# ============================================================
# Display-UUID discovery (DISCOVER, never hardcode — the M0a.1 contract).
# ============================================================

def discover_display_uuid(
    gpus: List[Dict[str, Any]],
    compute_index: Optional[str] = None,
) -> Tuple[Optional[str], str, str]:
    """Pin the display card's UUID = the card whose nvidia-smi index is NOT the
    compute index. Returns (display_uuid|None, basis, detail).

    The discovery rule (mirrors gpu_guard's config-derived-complement, but here
    we RESOLVE it to a concrete UUID to write as the durable pin):
      * compute_index parsed to int; the DISPLAY card is the single card whose
        index != compute_index on a >=2-GPU box.
      * 0 cards → (None, 'no-gpus', ...).
      * 1 card → (None, 'single-gpu', ...): no second card to protect; leave the
        pin None (single-GPU topology; gpu_guard handles it as single-gpu).
      * compute_index absent from the probe, OR != exactly one complement card →
        (None, 'indeterminate', ...): refuse to guess; leave the pin None so
        gpu_guard falls back to its own ladder rather than trusting a bad pin.

    The known live UUID is referenced nowhere in this logic — it is DISCOVERED
    from the probe. The value only ever appears as a documentation comment.
    """
    if compute_index is None:
        compute_index = voidloom_device.declared_device()
    if not gpus:
        return None, "no-gpus", "nvidia-smi returned no parseable GPUs"
    # R-8 (model-choice round 1): the OWNER'S declaration (WIZARD_DISPLAY_INDEX) is the pin when present -- declared,
    # never inferred. The complement below runs only when nothing is declared, and says so.
    try:
        declared, src = voidloom_device.declared_display() if len(gpus) >= 2 else (None, "one GPU: not consulted")
    except Exception as e:
        declared, src = None, "degraded: the declaration could not be read (%s)" % type(e).__name__
    if declared is None and src.startswith(("unusable", "degraded")):
        return None, "indeterminate", ("the owner's display declaration cannot be used (%s) -> no pin (refuse to "
                                       "infer past it)" % src)
    if declared == "none":
        return None, "owner-declaration", ("DECLARED: no card drives a monitor (WIZARD_DISPLAY_INDEX=none, %s) -> "
                                           "no display pin" % src)
    if declared is not None:
        hit = [g for g in gpus if g["index"] == int(declared)]
        if not hit:
            return None, "indeterminate", ("DECLARED display card %s (%s) is absent from the live probe (indices %s) "
                                           "-> no pin" % (declared, src, sorted(g["index"] for g in gpus)))
        return hit[0]["uuid"], "owner-declaration", ("DECLARED: display = card %s (%s); UUID %s pinned"
                                                     % (declared, src, hit[0]["uuid"]))
    if len(gpus) == 1:
        return None, "single-gpu", (
            "only one GPU visible → single-GPU topology; no display pin written "
            "(gpu_guard treats a 1-card box as single-gpu)"
        )

    # Parse the compute index (string mirroring wizard_config.gpu_device_index).
    cidx: Optional[int] = None
    if compute_index is not None:
        s = str(compute_index).strip()
        if re.fullmatch(r"\d+", s):
            cidx = int(s)
    if cidx is None:
        return None, "indeterminate", (
            f"compute_index {compute_index!r} not a single integer → cannot "
            "identify the complement → no pin (refuse to guess)"
        )

    present = {g["index"] for g in gpus}
    if cidx not in present:
        return None, "indeterminate", (
            f"configured compute_index {cidx} is ABSENT from the live probe "
            f"(indices present: {sorted(present)}) → no pin (refuse to guess)"
        )
    complement = [g for g in gpus if g["index"] != cidx]
    if len(complement) != 1:
        return None, "indeterminate", (
            f"compute_index {cidx} has {len(complement)} complement cards (need "
            "exactly 1) → ambiguous → no pin (refuse to guess)"
        )
    chosen = complement[0]
    return chosen["uuid"], "config-derived-complement", (
        f"INFERRED (no WIZARD_DISPLAY_INDEX declared): display = the card whose index ({chosen['index']}) != configured "
        f"compute_index ({cidx}); discovered UUID {chosen['uuid']} pinned as the "
        "PRIMARY display pin (overrides gpu_guard's heuristic; basis→manifest-pin)"
    )


# ============================================================
# Deep merge — MERGE-ON-UPGRADE, never clobber unknown keys.
# ============================================================

def _deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """Return base updated with `updates`, recursing into nested dicts so that an
    unknown pre-existing key at ANY depth survives. `updates` wins on conflict;
    keys present only in `base` are preserved (forward-compat)."""
    out = dict(base)
    for k, v in updates.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# ============================================================
# Build the `models` block from the registry (enable flags + routing facts).
# ============================================================

def _models_block_from_registry(existing_models: Optional[dict] = None) -> Dict[str, Any]:
    """Build the models block from model_garden.MODEL_REGISTRY. If an existing
    models block is supplied (merge path), PRESERVE any operator-set `enabled`
    overrides (don't reset a deliberately-disabled model to its default)."""
    existing_models = existing_models or {}
    try:
        import model_garden  # sibling on sys.path when installed; ACE: same dir
        registry = model_garden.MODEL_REGISTRY
    except Exception:
        # Graceful: no model_garden importable → keep whatever was there, or {}.
        return dict(existing_models)
    block: Dict[str, Any] = {}
    for key, spec in registry.items():
        entry = {
            "repo_id": spec.repo_id,
            "role": spec.role,
            "loader_type": spec.loader_type,
            "device": spec.device,
            "footprint_mb": spec.footprint_mb,
            "kill_switch_env": spec.kill_switch_env,
            "requires_packages": list(spec.requires_packages),
            # enabled: preserve an operator override if one exists, else default.
            "enabled": spec.enable_default,
        }
        prev = existing_models.get(key)
        if isinstance(prev, dict) and "enabled" in prev:
            entry["enabled"] = bool(prev["enabled"])
        block[key] = entry
    # Preserve any models the registry does not know about (forward-compat).
    for key, prev in existing_models.items():
        if key not in block:
            block[key] = prev
    return block


def _probed_block(existing: Optional[dict] = None) -> Dict[str, Any]:
    """Run model_garden.doctor() (cheap, no model load) to fill `probed`. Graceful:
    if model_garden is absent, keep the existing probed block (or {})."""
    try:
        import model_garden
        return model_garden.doctor()
    except Exception:
        return dict(existing or {})


# ============================================================
# read_manifest — the defensive reader.
# ============================================================

def read_manifest(path: str) -> Dict[str, Any]:
    """Return the manifest dict at `path`, or a copy of DEFAULT_MANIFEST on a
    missing / malformed file. NEVER raises (a bad manifest must not break a
    consumer; the defaults are always usable)."""
    fallback = json.loads(json.dumps(DEFAULT_MANIFEST))  # deep copy
    try:
        p = Path(path)
        if not p.is_file():
            return fallback
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return fallback
        return data
    except Exception:
        return fallback


# ============================================================
# write_manifest — the SOLE writer (merge-on-upgrade + discover).
# ============================================================

def write_manifest(
    path: str,
    *,
    merge: bool = True,
    discover: bool = True,
    _smi_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Write `voidloom_manifest.json` to `path`. Returns the written dict.

    merge=True   : read the existing file (if any) and deep-merge the new blocks
                   OVER it, so an unknown pre-existing key at any depth survives
                   (never clobber). merge=False writes a fresh DEFAULT_MANIFEST
                   base (still discovers + fills models/probed).
    discover=True: probe nvidia-smi, pin gpu_guard_policy.display_uuid = the
                   non-compute card's UUID (the M0a.1 PRIMARY pin), and run
                   model_garden.doctor() to fill `probed`. discover=False leaves
                   display_uuid as-is and does not re-probe.

    `_smi_text` is a TEST SEAM (canned nvidia-smi CSV) so discovery is provable
    with no GPU. Production: None → live probe.

    CONFINEMENT NOTE: this writes wherever `path` says. The orchestrator points
    it at the LIVE tree at install time; in the ACE loop it is pointed ONLY at
    the workspace sample. This function never hardcodes the live path.
    """
    # 1. Establish the base: existing file (merge) or fresh defaults.
    base = json.loads(json.dumps(DEFAULT_MANIFEST))  # deep copy
    existing: Dict[str, Any] = {}
    if merge:
        existing = read_manifest(path)
        # If read_manifest returned the fallback (no file), `existing` == defaults;
        # deep-merging defaults over defaults is a no-op, which is correct.
        base = _deep_merge(base, existing)

    # 2. models block (preserve operator enable overrides; add unknowns forward).
    base["models"] = _models_block_from_registry(base.get("models") or {})

    # 3. compute_index drives discovery; read it from the (possibly merged) policy.
    policy = base.setdefault("gpu_guard_policy", {})
    compute_index = policy.get("compute_index", DEFAULT_MANIFEST["gpu_guard_policy"]["compute_index"])

    # 4. discover the display pin (or leave it as-is).
    if discover:
        gpus = probe_gpus(_smi_text=_smi_text)
        display_uuid, basis, detail = discover_display_uuid(gpus, compute_index=compute_index)
        if display_uuid is not None:
            policy["display_uuid"] = display_uuid
        # If discovery was indeterminate/single/no-gpu, we DO NOT clobber an
        # existing good pin (merge-safety) — only overwrite when we found one.
        base["_discovery"] = {
            "display_discovered": display_uuid is not None,
            "basis": basis,
            "detail": detail,
            "probed_indices": [g["index"] for g in gpus],
            "probed_uuids": [g["uuid"] for g in gpus],
            "compute_index": compute_index,
        }
        base["probed"] = _probed_block(base.get("probed"))

    # 5. manifest_version is the writer's, never the merged file's (upgrade marker).
    base["manifest_version"] = MANIFEST_VERSION

    # 6. Atomic-ish write: serialize first (so a serialization error never leaves
    #    a half-written file), then write.
    text = json.dumps(base, indent=2, ensure_ascii=False, sort_keys=False)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    os.replace(tmp, p)
    return base


# ============================================================
# CLI — `python3 voidloom_manifest.py write <path> [--no-merge] [--no-discover]`
# ============================================================

def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="voidloom_manifest — the SOLE manifest writer + reader.")
    sub = parser.add_subparsers(dest="cmd")

    w = sub.add_parser("write", help="Write (merge-on-upgrade) voidloom_manifest.json.")
    w.add_argument("path", help="Destination path (orchestrator points this at the live tree).")
    w.add_argument("--no-merge", action="store_true", help="Do not merge an existing file.")
    w.add_argument("--no-discover", action="store_true", help="Skip nvidia-smi discovery + doctor.")

    r = sub.add_parser("read", help="Read a manifest defensively (prints JSON).")
    r.add_argument("path")

    args = parser.parse_args(argv)
    if args.cmd == "write":
        data = write_manifest(args.path, merge=not args.no_merge, discover=not args.no_discover)
        pol = data.get("gpu_guard_policy", {})
        disc = data.get("_discovery", {})
        print(f"wrote {args.path}")
        print(f"  display_uuid = {pol.get('display_uuid')}")
        print(f"  compute_index = {pol.get('compute_index')}")
        print(f"  discovery basis = {disc.get('basis')} ({disc.get('detail')})")
        return 0
    if args.cmd == "read":
        print(json.dumps(read_manifest(args.path), indent=2))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
