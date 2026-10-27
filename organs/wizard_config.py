#!/usr/bin/env python3
"""Wizard Library Federation Configuration — WizardConfig singleton.

Wizard Library Level 4 Piece 8a: Federation Bootstrap-Ready.

This module provides a single ``WizardConfig`` instance that every Wizard
Library module can import and use to resolve paths, embedding model settings,
GPU/device configuration, persona identity, and cross-tier DB locations.

Usage (in any Wizard Library module)::

    from wizard_config import config

    db_path = config.db_path           # Path to the narrative memory DB
    model   = config.embed_model       # "jinaai/jina-embeddings-v5-text-small"
    device  = config.embed_device      # "cuda:0" or "cpu"
    skills  = config.skills_dir        # Path to persona-skill folders

The config loads from three sources, in ascending priority:

    1. **Built-in defaults** (safe for a single-GPU desktop / Tier-1 host).
    2. **Config file**: ``<env-dir>/wizard-config.json`` (or env override),
       where <env-dir> is resolved by :mod:`voidloom_env`.
    3. **Environment variables**: ``WIZARD_*`` prefixed vars override any key.

Profiles
--------
The template config file ships with three profiles:

- ``desktop``  — Jina v5 on GPU, WSL2 cron, full power (Tier 1, Drew's desktop)
- ``laptop``   — Nomic v1.5 on CPU, systemd-only cron, portable (Tier 2, Drew's laptop)
- ``teammate`` — Nomic v1.5 on CPU, no cron, blank persona (Tier 3, teammates)

The active profile is selected via ``WIZARD_PROFILE`` env var or the
``active_profile`` key in the config JSON. Individual keys can still be
overridden on top of a profile.

Design references:
  - Memory #4032 (brain power tiers)
  - Memory #3674 (WSL2 HYBRID cron pattern)
  - Memory #4021 (Jina v5 decision)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, fields
import sqlite3
from pathlib import Path

# rho-1: the ONE env-directory resolver. Hard import by design -- it is
# stdlib-only and is laid by the same `cp -f "${PKG_ROOT}"/*.py` loop that
# lays this module, so its absence means an incomplete install, and an
# incomplete install must SAY SO rather than resolve sidecars elsewhere.
# A per-module inline fallback would recreate the 13-way divergence this
# module exists to end.
import voidloom_env
from typing import Any, Dict, Optional

__all__ = ["WizardConfig", "config", "get_config", "reload_config"]

logger = logging.getLogger(__name__)

# ============================================================
# Profile definitions (frozen — not user-editable at runtime)
# ============================================================

_PROFILES: Dict[str, Dict[str, Any]] = {
    "desktop": {
        "embed_model": "jinaai/jina-embeddings-v5-text-small",
        "embed_dim": 1024,
        "embed_device": "auto",
        "embed_prefix_query": "Query",
        "embed_prefix_document": "Document",
        "reranker_model": "Qwen/Qwen3-Reranker-0.6B",
        "reranker_enabled": True,
        "gpu_device_index": "0",  # PACKAGE_SAFE_DEFAULT; the operator's
                                  # env file or config overrides it.
        "cron_pattern": "wsl2_hybrid",
        "cron_timezone": "",  # NO packaged zone: '' = the host's local time (reference-host round, R3)
    },
    "laptop": {
        "embed_model": "nomic-ai/nomic-embed-text-v1.5",
        "embed_dim": 768,
        "embed_device": "cpu",
        "embed_prefix_query": "search_query",
        "embed_prefix_document": "search_document",
        "reranker_model": "Qwen/Qwen3-Reranker-0.6B",
        "reranker_enabled": False,
        "gpu_device_index": "",
        "cron_pattern": "systemd_only",
        "cron_timezone": "",  # NO packaged zone (the author's America/New_York was here)
    },
    "teammate": {
        "embed_model": "nomic-ai/nomic-embed-text-v1.5",
        "embed_dim": 768,
        "embed_device": "cpu",
        "embed_prefix_query": "search_query",
        "embed_prefix_document": "search_document",
        "reranker_model": "Qwen/Qwen3-Reranker-0.6B",
        "reranker_enabled": False,
        "gpu_device_index": "",
        "cron_pattern": "none",
        "cron_timezone": "UTC",
    },
}

# ============================================================
# Default config values (match Tier-1 desktop today)
# ============================================================

# Where to look for the config file. Override with WIZARD_CONFIG_PATH env var.
_DEFAULT_CONFIG_PATH = voidloom_env.env_path("wizard-config.json")



def _torch_device(torch_mod) -> str:
    """THE device for this organ (handoff round 1, lane L1): voidloom_device.torch_device() -- the backend's pin first,
    then the backend's own API (CUDA, ROCm, MPS, XPU); 'cpu' on any degraded, refused or unconfirmed basis. Replaces
    the per-organ `"cuda:0" if _cuda_usable(torch) else "cpu"`, which could only ever say CUDA or CPU."""
    try:
        from voidloom_device import torch_device as _td
    except ImportError:
        import sys as _s
        _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from voidloom_device import torch_device as _td
    return _td(torch_mod)


def _is_brain(p: Path) -> bool:
    """A file is a brain only if it CARRIES the schema, never merely exists.

    The fallback this replaces accepted `.exists()`. Measured on the authoring
    machine: the path it trusted holds a 4096-byte SQLite file with ZERO
    tables. `-s`/non-empty would have passed it too. Open IMMUTABLE so the
    probe cannot create -shm/-wal sidecars beside the candidate (a `mode=ro`
    open does; measured).
    """
    try:
        if not p.is_file() or p.stat().st_size == 0:
            return False
        c = sqlite3.connect("file:%s?immutable=1" % p, uri=True)
        try:
            c.execute("SELECT 1 FROM memories LIMIT 1").fetchone()
        finally:
            c.close()
        return True
    except (OSError, sqlite3.Error):
        return False

def _resolve_default_db_path() -> str:
    """Resolve the narrative-tier DB path.

    In production this lives next to claude_memory.py. For federation,
    we provide a sane default that can be overridden.
    """
    # Check if we're running from the memory directory (production layout)
    candidate = Path(__file__).parent.parent / "claude_memory.db"
    if candidate.exists():
        return str(candidate)
    # Env-first, matching voidloom/_paths.py: WIZARD_LIBRARY_DB >
    # CLAUDE_MEMORY_DB. The former hardcoded fallback named one machine
    # and one employer AND accepted a file's mere EXISTENCE as proof it
    # was a brain -- measured, that path holds a 4096-byte SQLite file
    # with ZERO tables, so even a non-empty check passes it. Verify the
    # candidate actually carries a `memories` table before returning it.
    for _var in ("WIZARD_LIBRARY_DB", "CLAUDE_MEMORY_DB"):
        _v = (os.environ.get(_var) or "").strip()
        if _v and _is_brain(Path(_v)):
            return _v
    # Last resort: just return the __file__-relative path even if not present
    return str(candidate)


def _resolve_default_module_dir() -> str:
    """Resolve the directory containing all Wizard Library Python modules."""
    return str(Path(__file__).parent)


# ============================================================
# WizardConfig dataclass
# ============================================================

@dataclass
class WizardConfig:
    """Central configuration for the Wizard Library.

    All fields have sane defaults matching the Tier-1 desktop installation.
    Fields are overridden in this order: defaults < profile < config file < env vars.
    """

    # --- Active profile ---
    active_profile: str = "desktop"

    # --- Database paths ---
    db_path: str = ""  # Narrative-tier DB (resolved at load time)
    project_local_db_path: str = ""  # Project-local tier DB
    global_db_path: str = ""  # Global tier DB
    module_dir: str = ""  # Directory containing Wizard Library modules

    # --- Embedding model ---
    embed_model: str = "jinaai/jina-embeddings-v5-text-small"
    embed_dim: int = 1024
    embed_device: str = "auto"  # "auto", "cuda:0", "cpu"
    embed_prefix_query: str = "Query"
    embed_prefix_document: str = "Document"
    embed_batch_size: int = 16

    # --- Reranker ---
    # Qwen3-Reranker-0.6B is THE reranker (hybrid_retrieval.CANONICAL_RERANKER_MODEL); bge-reranker-v2-m3 is the
    # fallback and the VOIDLOOM_RERANKER=bge backend. reranker_enabled can only turn the reranker OFF: whether it runs
    # is hybrid_retrieval.reranker_gate()'s live decision (a non-CPU accelerator AND >= 15.0 GiB RAM).
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    reranker_enabled: bool = True
    # C3: the installer's sweep record {accelerator, ram_gib, threshold_gib: 15.0, decided_at} -- read by doctor,
    # never trusted by the recall path (which re-checks live).
    reranker_gate: Dict[str, Any] = field(default_factory=dict)
    reranker_pool_size: int = 30
    reranker_device: str = ""  # Defaults to resolved_embed_device; explicit override here
    reranker_batch_size: int = 16  # Cross-encoder batch size (tuned for 2080 Ti at 16)

    # --- GPU / device ---
    gpu_device_index: str = "0"  # CUDA_VISIBLE_DEVICES value.
    # PACKAGE_SAFE_DEFAULT. NOT declared_device(): voidloom_device READS
    # this module as its config-file layer, so calling back would be
    # circular. Verified safe here -- no wizard-config.json exists on the
    # authoring machine, and its env file declares WIZARD_CUDA_DEVICE=1 at
    # a higher precedence than any config default.

    # --- Cron / scheduling ---
    cron_pattern: str = "wsl2_hybrid"  # "wsl2_hybrid", "systemd_only", "launchd", "none"
    cron_timezone: str = ""  # '' = no zone suffix: systemd reads OnCalendar in the host's local time
    sleep_loop_time: str = "22:30"  # HH:MM in cron_timezone
    sleep_loop_config_path: str = ""  # Path to sleep-loop-config.json

    # --- Persona / identity ---
    persona_dir: str = ""  # Root dir for persona-skill folders
    owner_persona: str = ""  # The owner's declared persona name, if any -- the package ships none (2026-10-14)
    council_members: str = "orin,vael,dessa,kael-em"  # Comma-separated council member names

    # --- Token / auth ---
    # RETIRED (model-choice unit, Round 1): claude_token_path and agy_path. The overnight thinker's credential lives
    # in ~/.voidloom/thinker/ and is resolved by organs/thinker.py alone (one resolver ends the two Claude homes);
    # agy was a per-organ research switch. A config file still naming either is read and ignored (only known
    # fields are passed, see _build_config).

    # --- Feature flags ---
    multi_hop_default: int = 0  # Default --multi-hop depth for recall
    anti_recency_enabled: bool = True
    dreaming_enabled: bool = True
    gap_mapping_enabled: bool = True
    whisper_digest_enabled: bool = True
    pre_cognitive_enabled: bool = True
    council_enabled: bool = True

    # --- Hybrid retrieval tuning ---
    rrf_k: int = 15  # RRF fusion constant (15 for small corpora < 10K docs)
    fts5_limit: int = 50  # Max FTS5 results before fusion
    vector_limit: int = 50  # Max vector results before fusion

    # --- Lock files ---
    # "" = beside THIS brain (lock_file_path). Until Round 3 the default was "/tmp": a machine-global directory, the
    # shape behind the 2026-10-01 incident (another brain's run held the host's sleep-loop lock). Nothing in the package
    # takes a lock through this field; the organs key their own locks by the brain (sleep_loop / memory_dreaming
    # .run_lock_path: <realpath(db)>.<organ>.lock).
    lock_dir: str = ""

    def __post_init__(self):
        """Resolve empty fields to their defaults."""
        if not self.db_path:
            self.db_path = _resolve_default_db_path()
        if not self.module_dir:
            self.module_dir = _resolve_default_module_dir()
        if not self.project_local_db_path:
            # L2: the retired project-local tier. Operator-supplied via
            # VOIDLOOM_PROJECT_DB; otherwise the one brain.
            self.project_local_db_path = (
                os.environ.get("VOIDLOOM_PROJECT_DB", "").strip()
                or self.global_db_path
                or _resolve_default_db_path())
        if not self.global_db_path:
            self.global_db_path = str(
                Path.home() / ".claude" / "memory" / "claude_memory.db"
            )
        if not self.persona_dir:
            # Default: assume we're in a project with .claude/skills/
            cwd = Path.cwd()
            candidate = cwd / ".claude" / "skills"
            if candidate.is_dir():
                self.persona_dir = str(candidate)
            else:
                self.persona_dir = str(Path.home() / ".claude" / "skills")
        if not self.sleep_loop_config_path:
            self.sleep_loop_config_path = str(
                voidloom_env.env_path("sleep-loop-config.json")
            )

    # --- Derived properties ---

    @property
    def skills_dir(self) -> Path:
        """Path to the persona-skill folder root."""
        return Path(self.persona_dir)

    @property
    def db_dir(self) -> Path:
        """Directory containing the narrative DB and companion modules."""
        return Path(self.db_path).parent

    @property
    def migration_dir(self) -> Path:
        """Directory containing migration SQL files."""
        return Path(self.module_dir)

    @property
    def lock_file_path(self) -> str:
        """The directory for this brain's lock files: beside its database, at the file's real path. A caller appends
        '<brain file name>.<organ>.lock' (the organs' run_lock_path shape). An explicit lock_dir other than the legacy
        machine-global '/tmp' is the owner's choice and is honoured; '/tmp' (the pre-Round-3 default) is set aside."""
        if self.lock_dir and os.path.normpath(self.lock_dir) != "/tmp":
            return self.lock_dir
        return os.path.dirname(os.path.realpath(self.db_path))

    @property
    def tier_db_paths(self) -> Dict[str, str]:
        """All three tier DB paths as a dict."""
        return {
            "narrative": self.db_path,
            "project_local": self.project_local_db_path,
            "global": self.global_db_path,
        }

    @property
    def resolved_embed_device(self) -> str:
        """Resolve 'auto' to actual device string."""
        if self.embed_device != "auto":
            return self.embed_device
        try:
            import torch
            return _torch_device(torch)
        except ImportError:
            return "cpu"

    @property
    def resolved_reranker_device(self) -> str:
        """Resolve the reranker device.

        If reranker_device is explicitly set, use that. Otherwise fall back to
        resolved_embed_device. If reranker is disabled, returns 'cpu' (the
        constant is still loaded at module level even when disabled).
        """
        if self.reranker_device:
            return self.reranker_device
        if not self.reranker_enabled:
            return "cpu"
        return self.resolved_embed_device

    @property
    def cuda_visible_devices(self) -> str:
        """The CUDA_VISIBLE_DEVICES value to set before importing torch."""
        return self.gpu_device_index

    @property
    def is_jina(self) -> bool:
        """Whether the configured embedding model is Jina v5."""
        return "jina" in self.embed_model.lower()

    @property
    def is_nomic(self) -> bool:
        """Whether the configured embedding model is Nomic."""
        return "nomic" in self.embed_model.lower()

    @property
    def council_member_list(self) -> list:
        """Council member names as a list."""
        return [m.strip() for m in self.council_members.split(",") if m.strip()]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for JSON output)."""
        result = {}
        for f in fields(self):
            result[f.name] = getattr(self, f.name)
        return result

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)


# ============================================================
# Config loading
# ============================================================

def _env_key(field_name: str) -> str:
    """Convert a field name to its WIZARD_* env var name.

    Example: ``embed_model`` -> ``WIZARD_EMBED_MODEL``
    """
    return f"WIZARD_{field_name.upper()}"


def _coerce(value: str, target_type) -> Any:
    """Coerce a string env-var value to the target type.

    ``target_type`` may be an actual type (bool, int, str) or the string
    annotation from a dataclass field ('bool', 'int', 'str').
    """
    # Normalize: dataclass field.type can be a string annotation
    type_name = target_type if isinstance(target_type, str) else getattr(target_type, '__name__', str(target_type))
    if type_name == "bool":
        return value.lower() in ("true", "1", "yes", "on")
    if type_name == "int":
        return int(value)
    if type_name.lower().startswith("dict"):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return value


def _load_config_file(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load the JSON config file. Returns empty dict if not found."""
    path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
    env_override = os.environ.get("WIZARD_CONFIG_PATH")
    if env_override:
        path = Path(env_override)

    if not path.exists():
        logger.debug("No config file at %s — using defaults", path)
        return {}

    try:
        with open(path, "r") as f:
            data = json.load(f)
        logger.debug("Loaded config from %s", path)
        return data
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read config file %s: %s — using defaults", path, e)
        return {}


def _build_config(config_path: Optional[str] = None) -> WizardConfig:
    """Build a WizardConfig by layering defaults < profile < file < env vars.

    This is the core config-resolution algorithm:

    1. Start with WizardConfig defaults (Tier-1 desktop).
    2. Load config file — if it has an ``active_profile``, apply that profile's
       values as a base layer.
    3. Apply any explicit key-value pairs from the config file on top of the profile.
    4. Apply WIZARD_* env vars on top of everything.
    """
    file_data = _load_config_file(config_path)

    # Determine the active profile
    profile_name = os.environ.get(
        "WIZARD_ACTIVE_PROFILE",
        file_data.get("active_profile", "desktop"),
    )
    profile = _PROFILES.get(profile_name, {})

    # Start with defaults
    merged: Dict[str, Any] = {}

    # Layer 1: profile values
    for k, v in profile.items():
        merged[k] = v

    # Layer 2: explicit config-file values (except nested "profiles" key).
    # CRITICAL: skip sentinel values ("", 0, None) that appear in the template
    # as placeholders. If these were applied, they would clobber the profile
    # defaults — e.g., a user copies the template, sets active_profile=laptop,
    # but embed_model="" from the template would overwrite the laptop profile's
    # "nomic-ai/nomic-embed-text-v1.5" with an empty string.
    # Keys prefixed with "_" (like "_comment") are also skipped.
    _SENTINEL_VALUES = ("", 0, None)
    for k, v in file_data.items():
        if k == "profiles":
            continue  # The profiles dict is reference-only, not direct config
        if k.startswith("_"):
            continue  # Skip JSON comments like "_comment"
        if not isinstance(v, bool) and v in _SENTINEL_VALUES:
            # Skip sentinel/placeholder values from the template. A bool is never a placeholder: `False == 0`, so
            # without the isinstance check a written `"reranker_enabled": false` was silently dropped (lane L2).
            continue
        merged[k] = v

    # Layer 3: env-var overrides
    valid_fields = {f.name: f for f in fields(WizardConfig)}
    for field_name, field_obj in valid_fields.items():
        env_var = _env_key(field_name)
        env_val = os.environ.get(env_var)
        if env_val is not None:
            merged[field_name] = _coerce(env_val, field_obj.type)

    # Build the config object — only pass known fields
    known = {k: v for k, v in merged.items() if k in valid_fields}
    return WizardConfig(**known)


# ============================================================
# Module-level singleton
# ============================================================

_config_instance: Optional[WizardConfig] = None


def get_config(config_path: Optional[str] = None) -> WizardConfig:
    """Get the singleton WizardConfig instance.

    On first call, builds the config from file + env vars.
    Subsequent calls return the cached instance.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = _build_config(config_path)
    return _config_instance


def reload_config(config_path: Optional[str] = None) -> WizardConfig:
    """Force-rebuild the config (useful for testing)."""
    global _config_instance
    _config_instance = _build_config(config_path)
    return _config_instance


# Convenience alias: ``from wizard_config import config``
# Uses a lazy descriptor so the config isn't loaded at import time if
# the module is imported but ``config`` is never accessed.
class _LazyConfig:
    """Descriptor that loads config on first attribute access."""

    def __init__(self):
        self._instance: Optional[WizardConfig] = None

    def _ensure(self) -> WizardConfig:
        if self._instance is None:
            self._instance = get_config()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ensure(), name)

    def __repr__(self) -> str:
        return repr(self._ensure())


config = _LazyConfig()


# ============================================================
# CLI (standalone usage)
# ============================================================

def _cli_show(args) -> None:
    """Print the resolved config as JSON."""
    cfg = reload_config(getattr(args, "config_path", None))
    print(cfg.to_json(indent=2))


def _cli_validate(args) -> None:
    """Validate the config and report issues."""
    cfg = reload_config(getattr(args, "config_path", None))
    issues = []

    # Check DB path exists
    if not Path(cfg.db_path).exists():
        issues.append(f"db_path does not exist: {cfg.db_path}")

    # Check module dir exists
    if not Path(cfg.module_dir).is_dir():
        issues.append(f"module_dir does not exist: {cfg.module_dir}")

    # Check persona dir exists
    if not Path(cfg.persona_dir).is_dir():
        issues.append(f"persona_dir does not exist: {cfg.persona_dir}")

    # The overnight thinker (warn, not error -- the owner may not have answered yet). One resolver: organs/thinker.py.
    try:
        import thinker as _thinker  # type: ignore
        _line = _thinker.health_line()
        if not _line.startswith("thinker: ") or "NO CREDENTIAL" in _line or "REFUSED" in _line \
                or "not chosen" in _line:
            issues.append("(warn) %s" % _line)
    except Exception as _e:  # noqa: BLE001 - a config check never breaks on the seam
        issues.append("(warn) the thinker seam could not be read: %s" % _e)

    # Check profile validity
    if cfg.active_profile not in _PROFILES:
        issues.append(f"active_profile '{cfg.active_profile}' not in known profiles: {list(_PROFILES)}")

    # Check embed_device validity
    if cfg.embed_device not in ("auto", "cpu") and not cfg.embed_device.startswith("cuda:"):
        issues.append(f"embed_device '{cfg.embed_device}' is invalid (expected auto/cpu/cuda:N)")

    # Check cron_pattern validity
    valid_cron = ("wsl2_hybrid", "systemd_only", "launchd", "none")
    if cfg.cron_pattern not in valid_cron:
        issues.append(f"cron_pattern '{cfg.cron_pattern}' not in {valid_cron}")

    # Check cross-tier paths
    for tier, path in cfg.tier_db_paths.items():
        if tier == "narrative":
            continue  # Already checked above
        if not Path(path).exists():
            issues.append(f"{tier} DB does not exist (warn): {path}")

    if issues:
        print(json.dumps({"status": "issues", "count": len(issues), "issues": issues}, indent=2))
    else:
        print(json.dumps({"status": "ok", "profile": cfg.active_profile, "message": "All checks passed"}))


def _cli_profiles(args) -> None:
    """List available profiles."""
    print(json.dumps({
        "profiles": {name: profile for name, profile in _PROFILES.items()},
        "active": get_config().active_profile,
    }, indent=2))


def _cli_env(args) -> None:
    """Print all WIZARD_* env vars and their current values."""
    env_map = {}
    for f in fields(WizardConfig):
        env_var = _env_key(f.name)
        env_val = os.environ.get(env_var)
        env_map[env_var] = {
            "field": f.name,
            "env_value": env_val,
            "resolved_value": str(getattr(get_config(), f.name)),
            "source": "env" if env_val is not None else "config/default",
        }
    print(json.dumps(env_map, indent=2))


# ============================================================
# Self-test
# ============================================================

def _run_self_test() -> None:
    """Unit tests for WizardConfig. Uses no external dependencies."""
    import sys
    import tempfile
    passed = 0
    failed = 0

    def assert_eq(actual, expected, label):
        nonlocal passed, failed
        if actual == expected:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {label}: expected {expected!r}, got {actual!r}")

    def assert_true(cond, label):
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {label}")

    # T1: Default config builds without error
    cfg = WizardConfig()
    assert_true(cfg.active_profile == "desktop", "T1: default profile is desktop")

    # T2: Profile application
    old_env = os.environ.get("WIZARD_ACTIVE_PROFILE")
    os.environ["WIZARD_ACTIVE_PROFILE"] = "laptop"
    cfg2 = _build_config()
    assert_eq(cfg2.embed_model, "nomic-ai/nomic-embed-text-v1.5", "T2: laptop profile embed_model")
    assert_eq(cfg2.embed_dim, 768, "T2: laptop profile embed_dim")
    assert_eq(cfg2.embed_device, "cpu", "T2: laptop profile embed_device")
    assert_eq(cfg2.reranker_enabled, False, "T2: laptop reranker disabled")
    if old_env is not None:
        os.environ["WIZARD_ACTIVE_PROFILE"] = old_env
    else:
        os.environ.pop("WIZARD_ACTIVE_PROFILE", None)

    # T3: Teammate profile
    os.environ["WIZARD_ACTIVE_PROFILE"] = "teammate"
    cfg3 = _build_config()
    assert_eq(cfg3.cron_pattern, "none", "T3: teammate no cron")
    assert_eq(cfg3.cron_timezone, "UTC", "T3: teammate UTC")
    os.environ.pop("WIZARD_ACTIVE_PROFILE", None)

    # T4: Env var override
    os.environ["WIZARD_EMBED_MODEL"] = "test-model/v1"
    os.environ["WIZARD_EMBED_DIM"] = "512"
    os.environ["WIZARD_RERANKER_ENABLED"] = "false"
    cfg4 = _build_config()
    assert_eq(cfg4.embed_model, "test-model/v1", "T4: env embed_model override")
    assert_eq(cfg4.embed_dim, 512, "T4: env embed_dim override")
    assert_eq(cfg4.reranker_enabled, False, "T4: env reranker_enabled override")
    os.environ.pop("WIZARD_EMBED_MODEL", None)
    os.environ.pop("WIZARD_EMBED_DIM", None)
    os.environ.pop("WIZARD_RERANKER_ENABLED", None)

    # T5: Config file loading
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "active_profile": "laptop",
            "db_path": "/tmp/test-wizard.db",
            "owner_persona": "testpersona",
        }, f)
        tmppath = f.name
    os.environ["WIZARD_CONFIG_PATH"] = tmppath
    cfg5 = _build_config()
    assert_eq(cfg5.active_profile, "laptop", "T5: file active_profile")
    assert_eq(cfg5.db_path, "/tmp/test-wizard.db", "T5: file db_path override")
    assert_eq(cfg5.owner_persona, "testpersona", "T5: file owner_persona override")
    # Profile values should apply underneath
    assert_eq(cfg5.embed_device, "cpu", "T5: laptop profile embed_device under file")
    os.environ.pop("WIZARD_CONFIG_PATH", None)
    os.unlink(tmppath)

    # T6: Env var beats config file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"embed_model": "file-model/v1"}, f)
        tmppath2 = f.name
    os.environ["WIZARD_CONFIG_PATH"] = tmppath2
    os.environ["WIZARD_EMBED_MODEL"] = "env-model/v2"
    cfg6 = _build_config()
    assert_eq(cfg6.embed_model, "env-model/v2", "T6: env beats file")
    os.environ.pop("WIZARD_CONFIG_PATH", None)
    os.environ.pop("WIZARD_EMBED_MODEL", None)
    os.unlink(tmppath2)

    # T7: Properties
    cfg7 = WizardConfig(
        db_path="/tmp/test.db",
        embed_model="jinaai/jina-embeddings-v5-text-small",
        council_members="orin,vael,dessa",
    )
    assert_true(cfg7.is_jina, "T7: is_jina detection")
    assert_true(not cfg7.is_nomic, "T7: not is_nomic")
    assert_eq(cfg7.council_member_list, ["orin", "vael", "dessa"], "T7: council list")
    assert_eq(str(cfg7.db_dir), "/tmp", "T7: db_dir")

    cfg7b = WizardConfig(embed_model="nomic-ai/nomic-embed-text-v1.5")
    assert_true(cfg7b.is_nomic, "T7b: is_nomic detection")

    # T8: to_dict / to_json roundtrip
    cfg8 = WizardConfig(db_path="/tmp/rt.db")
    d = cfg8.to_dict()
    assert_eq(d["db_path"], "/tmp/rt.db", "T8: to_dict preserves db_path")
    j = cfg8.to_json()
    parsed = json.loads(j)
    assert_eq(parsed["db_path"], "/tmp/rt.db", "T8: to_json roundtrip")

    # T9: _coerce function
    assert_eq(_coerce("true", bool), True, "T9: coerce bool true")
    assert_eq(_coerce("false", bool), False, "T9: coerce bool false")
    assert_eq(_coerce("1", bool), True, "T9: coerce bool 1")
    assert_eq(_coerce("42", int), 42, "T9: coerce int")
    assert_eq(_coerce("hello", str), "hello", "T9: coerce str passthrough")

    # T10: _env_key naming
    assert_eq(_env_key("embed_model"), "WIZARD_EMBED_MODEL", "T10: env key naming")
    assert_eq(_env_key("db_path"), "WIZARD_DB_PATH", "T10: env key db_path")

    # T11: resolved_embed_device with cpu fallback
    cfg11 = WizardConfig(embed_device="cpu")
    assert_eq(cfg11.resolved_embed_device, "cpu", "T11: explicit cpu device")
    cfg11b = WizardConfig(embed_device="cuda:0")
    assert_eq(cfg11b.resolved_embed_device, "cuda:0", "T11b: explicit cuda device")

    # T12: tier_db_paths structure
    cfg12 = WizardConfig(
        db_path="/a/narrative.db",
        project_local_db_path="/b/local.db",
        global_db_path="/c/global.db",
    )
    tiers = cfg12.tier_db_paths
    assert_eq(tiers["narrative"], "/a/narrative.db", "T12: narrative tier")
    assert_eq(tiers["project_local"], "/b/local.db", "T12: project_local tier")
    assert_eq(tiers["global"], "/c/global.db", "T12: global tier")

    # T13: reload_config actually reloads
    global _config_instance
    _config_instance = None
    c1 = get_config()
    os.environ["WIZARD_EMBED_DIM"] = "999"
    c2 = get_config()  # Should return cached (not 999)
    assert_eq(c2.embed_dim, c1.embed_dim, "T13: cached instance returned")
    c3 = reload_config()  # Should reload (999)
    assert_eq(c3.embed_dim, 999, "T13: reload picks up env change")
    os.environ.pop("WIZARD_EMBED_DIM", None)
    _config_instance = None  # Clean up

    # T14: Empty config file (missing) returns defaults
    os.environ["WIZARD_CONFIG_PATH"] = "/nonexistent/wizard-config.json"
    cfg14 = _build_config()
    assert_eq(cfg14.active_profile, "desktop", "T14: missing file uses defaults")
    os.environ.pop("WIZARD_CONFIG_PATH", None)

    # T15: Feature flags default to enabled
    cfg15 = WizardConfig()
    assert_true(cfg15.anti_recency_enabled, "T15: anti_recency default on")
    assert_true(cfg15.dreaming_enabled, "T15: dreaming default on")
    assert_true(cfg15.gap_mapping_enabled, "T15: gap_mapping default on")

    # T16: Lock dir default
    cfg16 = WizardConfig()
    assert_eq(cfg16.lock_dir, "", "T16: default lock_dir is unset (beside the brain)")
    assert_eq(cfg16.lock_file_path, os.path.dirname(os.path.realpath(cfg16.db_path)),
              "T16: locks live beside the brain, never in a machine-global dir")
    assert_eq(WizardConfig(db_path="/x/brain.db", lock_dir="/tmp").lock_file_path,
              os.path.dirname(os.path.realpath("/x/brain.db")), "T16: the legacy '/tmp' is set aside for the brain's dir")
    assert_eq(WizardConfig(db_path="/x/brain.db", lock_dir="/srv/locks").lock_file_path, "/srv/locks",
              "T16: an explicit owner lock_dir is honoured")

    # T17: cuda_visible_devices property
    cfg17 = WizardConfig(gpu_device_index="0")
    assert_eq(cfg17.cuda_visible_devices, "0", "T17: gpu index 0")
    cfg17b = WizardConfig(gpu_device_index="")
    assert_eq(cfg17b.cuda_visible_devices, "", "T17: empty gpu index")

    # T18: Profile name validation in validate
    cfg18 = WizardConfig(active_profile="nonexistent_profile")
    assert_eq(cfg18.active_profile, "nonexistent_profile", "T18: stores unknown profile name")

    # T19: Sentinel filter — empty strings and zeros from template do NOT clobber profile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "active_profile": "laptop",
            "embed_model": "",
            "embed_dim": 0,
            "embed_device": "",
            "reranker_model": "",
            "gpu_device_index": "",
            "cron_pattern": "",
            "cron_timezone": "",
        }, f)
        sentinel_path = f.name
    os.environ["WIZARD_CONFIG_PATH"] = sentinel_path
    # the fixture gives the profile an EXPLICIT zone -- none is packaged -- so "'' does not clobber
    # it" stays a claim that can fail (a profile '' vs a sentinel '' would compare equal by default)
    _laptop_tz = _PROFILES["laptop"]["cron_timezone"]
    _PROFILES["laptop"]["cron_timezone"] = "Etc/UTC"
    try:
        cfg19 = _build_config()
    finally:
        _PROFILES["laptop"]["cron_timezone"] = _laptop_tz
    assert_eq(cfg19.embed_model, "nomic-ai/nomic-embed-text-v1.5", "T19: sentinel '' doesn't clobber laptop embed_model")
    assert_eq(cfg19.embed_dim, 768, "T19: sentinel 0 doesn't clobber laptop embed_dim")
    assert_eq(cfg19.embed_device, "cpu", "T19: sentinel '' doesn't clobber laptop embed_device")
    assert_eq(cfg19.reranker_model, "Qwen/Qwen3-Reranker-0.6B", "T19: sentinel '' doesn't clobber laptop reranker_model")
    assert_eq(cfg19.cron_pattern, "systemd_only", "T19: sentinel '' doesn't clobber laptop cron_pattern")
    assert_eq(cfg19.cron_timezone, "Etc/UTC", "T19: sentinel '' doesn't clobber laptop cron_timezone")
    os.environ.pop("WIZARD_CONFIG_PATH", None)
    os.unlink(sentinel_path)

    # T20: Non-sentinel file values DO override profile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "active_profile": "laptop",
            "embed_model": "custom-model/v3",
            "embed_dim": 512,
            "owner_persona": "gandalf",
        }, f)
        real_path = f.name
    os.environ["WIZARD_CONFIG_PATH"] = real_path
    cfg20 = _build_config()
    assert_eq(cfg20.embed_model, "custom-model/v3", "T20: real file value overrides profile embed_model")
    assert_eq(cfg20.embed_dim, 512, "T20: real file value overrides profile embed_dim")
    assert_eq(cfg20.owner_persona, "gandalf", "T20: file value for non-profile key")
    # Profile values not overridden by file should still hold
    assert_eq(cfg20.embed_device, "cpu", "T20: laptop profile device survives")
    assert_eq(cfg20.reranker_enabled, False, "T20: laptop reranker_enabled survives")
    os.environ.pop("WIZARD_CONFIG_PATH", None)
    os.unlink(real_path)

    # T21: Reranker device and batch size fields
    cfg21 = WizardConfig(reranker_device="cuda:1", reranker_batch_size=32)
    assert_eq(cfg21.reranker_device, "cuda:1", "T21: explicit reranker_device")
    assert_eq(cfg21.reranker_batch_size, 32, "T21: explicit reranker_batch_size")
    assert_eq(cfg21.resolved_reranker_device, "cuda:1", "T21: resolved_reranker_device from explicit")

    # T22: Reranker device defaults to embed device when blank
    cfg22 = WizardConfig(reranker_device="", embed_device="cpu", reranker_enabled=True)
    assert_eq(cfg22.resolved_reranker_device, "cpu", "T22: reranker device falls back to embed device")

    # T23: Reranker device returns 'cpu' when reranker disabled and no explicit device
    cfg23 = WizardConfig(reranker_device="", reranker_enabled=False)
    assert_eq(cfg23.resolved_reranker_device, "cpu", "T23: disabled reranker defaults to cpu")

    # T24: _comment key in JSON is skipped
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "_comment": "This should be ignored",
            "active_profile": "teammate",
            "owner_persona": "saruman",
        }, f)
        comment_path = f.name
    os.environ["WIZARD_CONFIG_PATH"] = comment_path
    cfg24 = _build_config()
    assert_eq(cfg24.owner_persona, "saruman", "T24: real key applied")
    assert_eq(cfg24.cron_pattern, "none", "T24: teammate profile applied")
    assert_true(not hasattr(cfg24, "_comment"), "T24: _comment not stored as field")
    os.environ.pop("WIZARD_CONFIG_PATH", None)
    os.unlink(comment_path)

    # T25: Full template-as-config-file regression test (the exact P1 bug scenario)
    # Simulates: user copies template, sets active_profile=laptop, ALL sentinel fields present
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({
            "_comment": "Wizard Library Federation Config Template.",
            "active_profile": "laptop",
            "profiles": {
                "_comment": "Reference only",
                "desktop": {"embed_model": "jinaai/jina-embeddings-v5-text-small"},
                "laptop": {"embed_model": "nomic-ai/nomic-embed-text-v1.5"},
            },
            "db_path": "",
            "project_local_db_path": "",
            "global_db_path": "",
            "module_dir": "",
            "embed_model": "",
            "embed_dim": 0,
            "embed_device": "",
            "embed_prefix_query": "",
            "embed_prefix_document": "",
            "embed_batch_size": 16,
            "reranker_model": "",
            "reranker_enabled": True,
            "reranker_pool_size": 30,
            "gpu_device_index": "",
            "cron_pattern": "",
            "cron_timezone": "",
            "sleep_loop_time": "22:30",
            "sleep_loop_config_path": "",
            "persona_dir": "",
            "owner_persona": "example-owner",
            "council_members": "orin,vael,dessa,kael-em",
            "claude_token_path": "",
            "agy_path": "",
            "multi_hop_default": 0,
            "anti_recency_enabled": True,
            "dreaming_enabled": True,
            "gap_mapping_enabled": True,
            "whisper_digest_enabled": True,
            "pre_cognitive_enabled": True,
            "council_enabled": True,
            "rrf_k": 15,
            "fts5_limit": 50,
            "vector_limit": 50,
            "lock_dir": "/tmp",
        }, f)
        template_path = f.name
    os.environ["WIZARD_CONFIG_PATH"] = template_path
    _laptop_tz = _PROFILES["laptop"]["cron_timezone"]
    _PROFILES["laptop"]["cron_timezone"] = "Etc/UTC"     # an explicit zone for the fixture (none is packaged)
    try:
        cfg25 = _build_config()
    finally:
        _PROFILES["laptop"]["cron_timezone"] = _laptop_tz
    # THE CRITICAL ASSERTIONS: laptop profile values must survive template sentinels
    assert_eq(cfg25.embed_model, "nomic-ai/nomic-embed-text-v1.5", "T25: REGRESSION laptop embed_model survives template")
    assert_eq(cfg25.embed_dim, 768, "T25: REGRESSION laptop embed_dim survives template")
    assert_eq(cfg25.embed_device, "cpu", "T25: REGRESSION laptop embed_device survives template")
    assert_eq(cfg25.embed_prefix_query, "search_query", "T25: REGRESSION laptop query prefix survives template")
    assert_eq(cfg25.embed_prefix_document, "search_document", "T25: REGRESSION laptop doc prefix survives template")
    assert_eq(cfg25.reranker_model, "Qwen/Qwen3-Reranker-0.6B", "T25: REGRESSION laptop reranker_model survives template")
    assert_eq(cfg25.cron_pattern, "systemd_only", "T25: REGRESSION laptop cron_pattern survives template")
    assert_eq(cfg25.cron_timezone, "Etc/UTC", "T25: REGRESSION laptop cron_timezone survives template")
    # Non-sentinel values from template SHOULD be applied on top of profile
    assert_eq(cfg25.embed_batch_size, 16, "T25: real value 16 from template applied")
    assert_eq(cfg25.owner_persona, "example-owner", "T25: real string from template applied")
    assert_eq(cfg25.rrf_k, 15, "T25: real int 15 from template applied")
    assert_eq(cfg25.sleep_loop_time, "22:30", "T25: real string from template applied")
    assert_eq(cfg25.reranker_enabled, True, "T25: real bool from template applied")
    os.environ.pop("WIZARD_CONFIG_PATH", None)
    os.unlink(template_path)

    print(f"\n  {passed} passed, {failed} failed out of {passed + failed} assertions")
    if failed > 0:
        sys.exit(1)


# ============================================================
# Main
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Wizard Library Federation Configuration"
    )
    parser.add_argument("--config-path", help="Path to wizard-config.json")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("show", help="Print resolved config as JSON")
    sub.add_parser("validate", help="Validate config and report issues")
    sub.add_parser("profiles", help="List available profiles")
    sub.add_parser("env", help="Print WIZARD_* env var map")
    sub.add_parser("test", help="Run self-tests")

    args = parser.parse_args()

    if args.command == "show":
        _cli_show(args)
    elif args.command == "validate":
        _cli_validate(args)
    elif args.command == "profiles":
        _cli_profiles(args)
    elif args.command == "env":
        _cli_env(args)
    elif args.command == "test":
        _run_self_test()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
