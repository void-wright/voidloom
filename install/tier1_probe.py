#!/usr/bin/env python3
"""tier1_probe.py -- PROVE an ambient Tier 1 by EXECUTION, through the candidate interpreter.

Reference-host round, R1 (design: the ambient-Tier-1 upgrade shape, accepted 2026-09-25).
install.sh knew two shapes -- `--tier0-only` (writes tier0_only=1 into the brain: semantic
recall silently off) and the venv (builds ~5.7 GB, re-embeds every vector-less memory,
switches every unit's interpreter) -- and its own reference host fits neither: Tier 1 runs
through an interpreter OUTSIDE the brain dir, and the tier is declared by the DB. The
installer now accepts that shape, but only on PROOF, and this file is the proof. It is run
BY THE CANDIDATE (`<candidate> install/tier1_probe.py run ...`), from the PACKAGE tree,
before a single file is laid; every step runs in its own subprocess of the candidate with
its output captured, and the first failure stops the run naming the step.

    P1  imports       torch, sqlite_vec, sentence_transformers import in the candidate
    P2  vec           sqlite_vec loads on a throwaway connection and answers a distance
                      query at the DB's DECLARED width (read from the vec0 table's DDL)
    P4  gpu           the device the units will be pinned to equals the brain's machine
                      manifest compute_index (a brain that has never had a manifest: DERIVED
                      read-only by the organ's own rules -- Step A.1 writes the file after the
                      proofs), and is NOT a display card by LIVE observation:
                      every card's display_active is read NOW through the package's one
                      nvidia-smi locator (ruler VL-REF-v1.1, memory #21831 -- the manifest's
                      "display" is the complement of the configured compute index, so it
                      cannot prove the pin off the display):
                        1 card                  -> pass (the pin must be that card)
                        >=2 cards, >=1 Enabled  -> the pin must not be an Enabled card
                        >=2 cards, none Enabled -> UNOBSERVABLE: FAIL closed, unless the owner
                                                   declared WIZARD_DISPLAY_INDEX=<n>|none outside
                                                   the managed block (--display-index): then the
                                                   pin must differ from it (basis=owner-declared)
                                                   AND the pin must be the owner's declaration too
                                                   (--pin-declared-by: CUDA_VISIBLE_DEVICES or
                                                   WIZARD_CUDA_DEVICE outside the block; ruler
                                                   VL-REF-v1.2 -- a declaration may authorise a
                                                   pin, never launder the installer's guess)
                      then CUDA is available under that pin. (--force-cpu SKIPS P4 and proves
                      nothing about the units' device; install.sh refuses it in this shape.)
                      Runs BEFORE P3 on purpose: P3 is the first model load, and a pin that
                      could be the display card must be refused before anything is loaded.
    P3  recall        ONE real semantic recall through the package's own recall path --
                      claude_memory._encode (query side) + hybrid_retrieval.recall_hybrid
                      (vector-only) -- with HF_HUB_OFFLINE=1, against the real DB opened
                      read-only: >= 1 row, and the query vector's width == the DB's
                      declared width. A model absent from the cache fails here; nothing
                      is downloaded. Re-run after the migrations as a post-migration gate.
    P5  versions      the executed versions vs the package pins -> WARNINGS, never a
                      failure (the base environment is accepted as found, never upgraded)
    P6  golden        (handoff round 1, L4) the package's own encoder, on the device the organs chose, against
                      the CPU-fp32 reference vectors in install/fixtures/golden_vectors_<tier>.json
                      (install/golden_vectors.py): every text's cosine >= the fixture's frozen tolerance and
                      every query's top-3 identical. Runs BEFORE anything is embedded: a backend that fails it
                      embeds nothing (install.sh flips an MPS/ROCm/XPU machine to the CPU, announced, and records
                      proof_label=FAILED-ON-THIS-MACHINE -- contract C4).
    P7  reranker      only when the reranker gate is ON (--reranker 1): the Qwen3 reranker, offline, on that
                      device, scores the relevant passage above the irrelevant one for every fixture query.
    P8  timed recall  after the brain holds vectors: one warm-up, then five hybrid recalls through the package's
                      own path (encode + recall_hybrid), each timed; p50 is DERIVED and recorded, never typed.
                      Fails only if a recall raises or returns nothing.

    counts            active memories, vectors in the active table, active memories with
                      no vector, vectors whose memory is gone -- derived, printed, never typed

No step writes to the DB, installs anything, or falls back to anything. Exit codes:
0 every requested step passed; 1 a step failed (named in the JSON); 2 usage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path

TIER_BY_TABLE = {"memory_embeddings": "nomic", "memory_embeddings_v2": "jina", "memory_embeddings_v3": "qwen"}
QUERY = "memory"
_DIM_RE = re.compile(r"embedding\s+float\[(\d+)\]", re.I)


# --------------------------------------------------------------------------- DB facts (stdlib)
def _ro_uri(db: str) -> str:
    """`file:<db>?mode=ro` with the path ESCAPED (reference-host round, iteration 4, LOW 2): spliced raw, a '?' or '#'
    in the path ends it early and SQLite opens -- read-write-CREATE, the rest being an unknown query key -- the
    prefix (measured: a dry run created an empty file beside the brain dir)."""
    return "file:%s?mode=ro" % urllib.request.pathname2url(os.path.abspath(db))


def db_facts(db: str) -> dict:
    """The brain's declarations, read without writing: the active table, its declared width,
    tier0_only. mode=ro, NEVER immutable=1 (reference-host round, iteration 2; memory #21830):
    these facts decide the brain's CURRENT state (P2's width, P3's tier, the counts' table), and
    immutable=1 skips the WAL -- a declaration committed behind an open reader stayed invisible.
    mode=ro is WAL-aware; on a WAL brain it may leave SQLite's -wal/-shm beside it."""
    out = {"db": db}
    try:
        c = sqlite3.connect(_ro_uri(db), uri=True)
        try:
            row = c.execute("SELECT value FROM metadata WHERE key='active_embedding_table'").fetchone()
            t0 = c.execute("SELECT value FROM metadata WHERE key='tier0_only'").fetchone()
            table = row[0] if row else None
            ddl = None
            if table:
                r = c.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()
                ddl = r[0] if r else None
        finally:
            c.close()
    except sqlite3.Error as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
        return out
    m = _DIM_RE.search(ddl or "")
    out.update({"active_embedding_table": table, "tier": TIER_BY_TABLE.get(table or ""),
                "declared_dim": int(m.group(1)) if m else None,
                "tier0_only": (t0[0] if t0 else None)})
    return out


# --------------------------------------------------------------------------- the steps
def step_p1(a) -> dict:
    import platform
    facts = {"executable": sys.executable, "python": platform.python_version(), "sys_version": sys.version.split()[0]}
    import torch  # noqa: F401  -- the import IS the proof
    import sqlite_vec
    import sentence_transformers
    facts.update({"torch": torch.__version__, "sentence_transformers": sentence_transformers.__version__,
                  "sqlite_vec": getattr(sqlite_vec, "__version__", None)})
    return facts


def step_p2(a) -> dict:
    import sqlite_vec
    facts = db_facts(a.db)
    dim = facts.get("declared_dim")
    if not dim:
        raise RuntimeError("the DB declares no vector width for %r" % facts.get("active_embedding_table"))
    c = sqlite3.connect(":memory:")
    try:
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
        v1 = [0.0] * dim
        v2 = [0.0] * dim
        v1[0], v2[1] = 1.0, 1.0
        d = c.execute("SELECT vec_distance_cosine(?, ?)",
                      (sqlite_vec.serialize_float32(v1), sqlite_vec.serialize_float32(v2))).fetchone()[0]
        ver = c.execute("SELECT vec_version()").fetchone()[0]
    finally:
        c.close()
    if not (isinstance(d, float) and abs(d - 1.0) < 1e-6):
        raise RuntimeError("vec_distance_cosine of two orthogonal %d-dim vectors was %r, not 1.0" % (dim, d))
    return {"vec_version": ver, "dim": dim, "orthogonal_cosine_distance": d}


def _manifest(path: str, organs: str = "") -> dict:
    """The machine manifest P4 compares the pin against. PRESENT: read (an unreadable one fails P4, named).
    ABSENT: DERIVED READ-ONLY (env-file writer pass, W8 -- found at the reference host's landing, #21961: the proofs
    run BEFORE Step A.1 writes voidloom_manifest.json, so an ambient host that had never had one could never pass P4;
    the rehearsal refused with "the machine manifest ... is unreadable"). The derivation is the organ's OWN: exactly the
    gpu_guard_policy `organs/voidloom_manifest.py write` puts in a fresh manifest at A.1 -- compute_index from its
    DEFAULT_MANIFEST (voidloom_device.declared_device()), display_uuid discovered from the live probe as the complement
    of it (discover_display_uuid) -- computed in memory. Nothing is written; A.1 writes the file after P4 passes."""
    p = Path(path) if path else None
    if p is None or (not p.exists() and not p.is_symlink()):
        return _derived_manifest(organs, path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("the machine manifest %s is unreadable (%s: %s) -- P4 compares the pin against it; move it "
                           "aside to let P4 derive it read-only, or rewrite it: <python3> <brain>/organs/"
                           "voidloom_manifest.py write %s" % (path, type(e).__name__, e, path))


def _derived_manifest(organs: str, path: str) -> dict:
    if organs and organs not in sys.path:
        sys.path.insert(0, organs)
    import voidloom_manifest  # the package's own writer, used READ-ONLY: its defaults and its discovery rule
    compute = str(voidloom_manifest.DEFAULT_MANIFEST["gpu_guard_policy"]["compute_index"])
    gpus = voidloom_manifest.probe_gpus()
    display_uuid, basis, detail = voidloom_manifest.discover_display_uuid(gpus, compute_index=compute)
    pol = {"compute_index": compute}
    if display_uuid is not None:
        pol["display_uuid"] = display_uuid
    return {"gpu_guard_policy": pol, "_discovery": {"basis": basis, "detail": detail},
            "_source": "derived read-only: no manifest at %s (organs/voidloom_manifest.py's own rules -- what Step A.1 "
                       "writes after the proofs)" % (path or "<none given>")}


UNOBSERVABLE = ("no card reports an active display (monitor asleep?) -- wake it and re-run, or declare "
                "WIZARD_DISPLAY_INDEX=<n>|none in the env file outside the managed block")


def live_display(organs: str) -> dict:
    """{index: display_active} for every card, observed NOW through the package's ONE nvidia-smi
    locator (organs/voidloom_device.py nvidia_smi(): PATH, then NVIDIA_SMI_DIRS). Anything that
    stops the observation is raised: an unobserved display is never read as 'no display'."""
    if organs not in sys.path:
        sys.path.insert(0, organs)
    import voidloom_device
    smi = voidloom_device.nvidia_smi()
    if not smi:
        raise RuntimeError("nvidia-smi cannot be located (PATH, then %s): display_active is unobservable"
                           % (list(voidloom_device.NVIDIA_SMI_DIRS),))
    p = subprocess.run([smi, "--query-gpu=index,display_active", "--format=csv,noheader"],
                       capture_output=True, text=True, timeout=8)
    if p.returncode != 0:
        raise RuntimeError("%s --query-gpu=index,display_active exited %d (%s): display_active is unobservable"
                           % (smi, p.returncode, _tail(p.stderr or p.stdout, 200)))
    cards = {}
    for line in p.stdout.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 2 and re.fullmatch(r"\d+", parts[0]):
            cards[parts[0]] = parts[1]
    if not cards:
        raise RuntimeError("%s --query-gpu=index,display_active listed no card: display_active is unobservable" % smi)
    return {"nvidia_smi": smi, "cards": cards}


PIN_DECLARATION_KEYS = ("CUDA_VISIBLE_DEVICES", "WIZARD_CUDA_DEVICE")


def display_verdict(pin: str, cards: dict, declared: str = "", pin_declared_by: str = "",
                    pin_source: str = "") -> dict:
    """The P4 display rule (ruler VL-REF-v1.1 and v1.2; coordinator rulings, memories #21831, #21839), a
    pure function of the LIVE observation `cards` ({index: display_active}), the owner's display
    declaration `declared` (WIZARD_DISPLAY_INDEX from the env file, outside the managed block: '<index>',
    'none' or ''), and WHO declared the pin: `pin_declared_by` is the env-file key OUTSIDE the managed
    block that declares it (one of PIN_DECLARATION_KEYS) or '' when the pin is the installer's own choice
    (`pin_source` says which). Returns facts naming the basis; raises naming the reason. Fail-closed on
    purpose: a false pass puts a background job on the monitor card silently for hours, a false fail
    costs waking the screen (or one declared line) and a re-run, loudly, in minutes."""
    decl = (declared or "").strip()
    by = (pin_declared_by or "").strip()
    order = sorted(cards, key=int)
    enabled = [i for i in order if cards[i].strip().lower() == "enabled"]
    facts = {"live_display_active": {i: cards[i] for i in order}, "live_display_enabled": enabled,
             "display_declared": decl or None}
    if decl and decl.lower() != "none":
        if not re.fullmatch(r"\d+", decl):
            raise RuntimeError("WIZARD_DISPLAY_INDEX=%r is neither a card index nor 'none'" % decl)
        if decl not in cards:
            raise RuntimeError("WIZARD_DISPLAY_INDEX=%s names no card nvidia-smi lists (%s)" % (decl, order))
    if len(cards) == 1:
        if pin != order[0]:
            raise RuntimeError("one card (index %s) and the pin is %s: the pin must be that card" % (order[0], pin))
        facts["display_basis"] = "single-card"     # sharing the only card with a display is unavoidable
        return facts
    if pin not in cards:
        raise RuntimeError("the pin %s is not a card nvidia-smi lists (%s)" % (pin, order))
    if pin in enabled:
        raise RuntimeError("the pin %s reports display_active=Enabled NOW: card %s drives a display" % (pin, pin))
    if decl and decl.lower() != "none" and pin == decl:
        raise RuntimeError("the pin %s is the display card the owner declared (WIZARD_DISPLAY_INDEX=%s)" % (pin, decl))
    if enabled:
        facts["display_basis"] = "observed"
        return facts
    if not decl:
        raise RuntimeError("%d cards and %s" % (len(cards), UNOBSERVABLE))
    # ruler VL-REF-v1.2: the owner's display declaration may AUTHORISE a pin; it may not launder a guess.
    # Measured on iteration 2: both cards Disabled, WIZARD_DISPLAY_INDEX=none, no pin declared -- the
    # installer's display_active heuristic chose card 0 and this branch passed it as owner-declared.
    if by not in PIN_DECLARATION_KEYS:
        raise RuntimeError(
            "no card reports an active display and the owner declared WIZARD_DISPLAY_INDEX=%s, but the pin %s is "
            "NOT declared by the owner (%s): a declaration may authorise a pin, never launder the installer's "
            "choice -- declare WIZARD_CUDA_DEVICE=<index> (or CUDA_VISIBLE_DEVICES=<index>) in the env file "
            "OUTSIDE the managed block, then re-run" % (decl, pin, (pin_source or "").strip() or "not declared"))
    facts["display_basis"] = "owner-declared"
    return facts


def manifest_pin_advice(pin: str, declared_by: str, env_file: str, manifest: str, derived: bool, organs: str,
                        compute: str = "") -> str:
    """What an owner who is not an expert DOES so P4's compute_index check passes (env-file writer pass, iteration 4,
    carried item O3). The machine manifest records the card this machine DECLARES -- voidloom_device.declared_device(),
    which reads WIZARD_CUDA_DEVICE and never CUDA_VISIBLE_DEVICES, by design (a live pin may already be renumbered) -- so a
    pin declared by CUDA_VISIBLE_DEVICES alone, or not declared at all (the installer's own choice), never matches it. An
    EXISTING manifest keeps its compute_index across a rewrite (voidloom_manifest.py merges on upgrade -- reflector-4,
    executed: the rewrite this advice used to name left compute_index unchanged and P4 refused again), so the words move
    it aside: P4 then derives it read-only from the declaration, and Step A.1 writes it afresh. The words name the exact
    line, the file and where in it; followed literally they pass."""
    import shlex
    where = env_file or "the env file"
    add = ("add this line to %s, on a line of its own OUTSIDE the managed block (above the line '# >>> wizard-library "
           "managed >>>' or below the line '# <<< wizard-library managed <<<'): WIZARD_CUDA_DEVICE=%s" % (where, pin))
    if declared_by == "CUDA_VISIBLE_DEVICES":
        fix = ("The pin is declared by CUDA_VISIBLE_DEVICES, which the machine manifest does not read: it records the card "
               "this machine DECLARES, from WIZARD_CUDA_DEVICE. To fix it, %s -- keep your CUDA_VISIBLE_DEVICES line" % add)
    elif declared_by == "WIZARD_CUDA_DEVICE":
        if derived:
            return ""
        fix = ("The machine manifest at %s was written for card %s, before WIZARD_CUDA_DEVICE=%s was declared"
               % (manifest, compute or "another card", pin))
    else:
        fix = ("The pin %s is this installer's own choice, not a declaration, and the machine manifest records the card "
               "this machine DECLARES (card %s: nothing declares one). If card %s is the card that drives no display, %s"
               % (pin, compute or "?", pin, add))
    if not derived and manifest:
        fix += ("; then move the machine manifest aside, so that it is derived again from that declaration (P4 derives it "
                "read-only; Step A.1 writes it afresh after the proofs): mv %s %s"
                % (shlex.quote(manifest), shlex.quote(manifest + ".before-card-" + pin)))
    return ". " + fix + "; then re-run."


def step_p4(a) -> dict:
    if a.force_cpu:
        return {"skipped": "--force-cpu: P4 NOT run -- this proves nothing about the units' device "
                           "(install.sh refuses --force-cpu in the ambient shape)"}
    pin = (a.pin or "").strip()
    facts = {"pin": pin, "pin_declared_by": (getattr(a, "pin_declared_by", "") or "").strip() or None,
             "pin_source": (getattr(a, "pin_source", "") or "").strip() or None}
    a.partial_facts = facts                       # a refusal still reports what P4 observed
    if not re.fullmatch(r"\d+", pin):
        raise RuntimeError("no single CUDA device is pinned for the units (pin=%r). Declare one: "
                           "WIZARD_CUDA_DEVICE=<index> in the env file outside the managed block" % pin)
    man = _manifest(a.manifest, a.organs)
    facts["manifest_source"] = man.get("_source") or "file %s" % a.manifest
    pol = man.get("gpu_guard_policy") or {}
    compute = str(pol.get("compute_index", "")).strip()
    facts["manifest_compute_index"] = compute
    if compute != pin:
        raise RuntimeError("the pin %s is not the machine manifest's compute_index %r (%s)%s" % (
            pin, compute, facts["manifest_source"], manifest_pin_advice(pin, facts["pin_declared_by"] or "",
                                                                      getattr(a, "env_file", "") or "", a.manifest,
                                                                      bool(man.get("_source")), a.organs, compute)))
    sys.path.insert(0, a.organs)
    import voidloom_manifest  # the package's own probe (nvidia-smi via voidloom_device.nvidia_smi)
    gpus = voidloom_manifest.probe_gpus()
    by_uuid = {g["uuid"]: g["index"] for g in gpus}
    display_uuid = pol.get("display_uuid")
    facts.update({"live_cards": sorted(by_uuid.values()), "manifest_display_uuid": display_uuid,
                  "manifest_display_basis": (man.get("_discovery") or {}).get("basis")})
    if not gpus:
        raise RuntimeError("nvidia-smi lists no GPUs (located: %r) while a device is pinned"
                           % voidloom_manifest.voidloom_device.nvidia_smi())
    if len(gpus) > 1 and display_uuid:
        # The manifest's display card is DERIVED (the complement of the configured compute index),
        # so agreeing with it proves nothing -- but disagreeing with it is still a refusal.
        if display_uuid not in by_uuid:
            raise RuntimeError("the manifest's display card %s is absent from the live probe (%s)"
                               % (display_uuid, sorted(by_uuid)))
        facts["manifest_display_index"] = str(by_uuid[display_uuid])
        if facts["manifest_display_index"] == pin:
            raise RuntimeError("the pin %s IS the display card the machine manifest names (display_uuid %s = live "
                               "index %s)" % (pin, display_uuid, facts["manifest_display_index"]))
    # THE PROOF: display_active observed NOW (never the manifest's derivation, which reads the pin)
    obs = live_display(a.organs)
    facts["nvidia_smi"] = obs["nvidia_smi"]
    if sorted(obs["cards"], key=int) != sorted(str(i) for i in by_uuid.values()):
        raise RuntimeError("the display query lists cards %s but the inventory lists %s"
                           % (sorted(obs["cards"], key=int), sorted(by_uuid.values())))
    # what was OBSERVED goes into the facts before the verdict, so a refusal reports it too
    facts["live_display_active"] = {i: obs["cards"][i] for i in sorted(obs["cards"], key=int)}
    facts["live_display_enabled"] = [i for i in facts["live_display_active"]
                                     if facts["live_display_active"][i].strip().lower() == "enabled"]
    facts["display_declared"] = (getattr(a, "display_index", "") or "").strip() or None
    facts.update(display_verdict(pin, obs["cards"], getattr(a, "display_index", "") or "",
                                 getattr(a, "pin_declared_by", "") or "", getattr(a, "pin_source", "") or ""))
    # Only now, with the pin proven not to be the display card, is CUDA initialised -- under
    # CUDA_VISIBLE_DEVICES=<pin>, forced by the parent before this interpreter started.
    if os.environ.get("CUDA_VISIBLE_DEVICES") != pin:
        raise RuntimeError("internal: CUDA_VISIBLE_DEVICES=%r, expected the pin %s" % (
            os.environ.get("CUDA_VISIBLE_DEVICES"), pin))
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to the candidate under CUDA_VISIBLE_DEVICES=%s" % pin)
    facts.update({"cuda_available": True, "visible_devices": torch.cuda.device_count()})
    return facts


def step_p3(a) -> dict:
    facts = db_facts(a.db)
    tier, dim = facts.get("tier"), facts.get("declared_dim")
    if not tier or not dim:
        raise RuntimeError("the DB declares no recognised Tier-1 table/width: %r" % facts)
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("internal: P3 must run with HF_HUB_OFFLINE=1")
    if os.environ.get("WIZARD_EMBED_MODEL") != tier:
        raise RuntimeError("internal: WIZARD_EMBED_MODEL=%r, the DB declares %r" % (os.environ.get("WIZARD_EMBED_MODEL"), tier))
    sys.path.insert(0, a.organs)
    import claude_memory as cm
    import hybrid_retrieval as hr
    import sqlite_vec
    cm.DB_PATH = Path(a.db)
    if cm._active_embed_model() != tier:
        raise RuntimeError("the package's recall path resolved tier %r; the DB declares %r" % (cm._active_embed_model(), tier))
    qvec = cm._encode(QUERY, prefix="search_query")
    qdim = len(qvec) // 4
    conn = sqlite3.connect(_ro_uri(a.db), uri=True)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        rows = hr.recall_hybrid(conn, QUERY, qvec, top_k=3, mode="vector-only")
    finally:
        conn.close()
    facts.update({"query": QUERY, "query_dim": qdim, "rows": len(rows), "top_ids": [r.get("id") for r in rows],
                  "model_repo": cm.EMBED_TIERS[tier][2]})
    if qdim != dim:
        raise RuntimeError("the query vector is %d-wide; the DB declares %d" % (qdim, dim))
    if not rows:
        raise RuntimeError("one semantic recall against the DB returned NO rows")
    return facts


def _pins(requirements: str) -> dict:
    pins = {}
    try:
        for line in Path(requirements).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
            if m:
                pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    except OSError:
        pass
    return pins


def step_p5(a) -> dict:
    import importlib.metadata as md
    pins = _pins(a.requirements) if a.requirements else {}
    executed, warnings = {}, []
    for name in ("torch", "sentence-transformers", "sqlite-vec"):
        try:
            executed[name] = md.version(name)
        except Exception:  # noqa: BLE001 - conda layouts may lack dist metadata for some
            executed[name] = None
        want = pins.get(name)
        if want and executed[name] != want:
            warnings.append("%s %s executed; the package pins %s (accepted as found, never upgraded)"
                            % (name, executed[name], want))
    return {"executed": executed, "pins": {k: pins[k] for k in executed if k in pins}, "warnings": warnings}


def counts(a) -> dict:
    """Active memories, vectors, active memories with no vector, orphan vectors -- through the
    public vec0 interface (sqlite_vec loaded), read-only."""
    import sqlite_vec
    facts = db_facts(a.db)
    table = facts.get("active_embedding_table")
    if table not in TIER_BY_TABLE:
        raise RuntimeError("unrecognised active table %r" % table)
    c = sqlite3.connect(_ro_uri(a.db), uri=True)
    try:
        c.enable_load_extension(True)
        sqlite_vec.load(c)
        c.enable_load_extension(False)
        active = c.execute("SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived,0)=0").fetchone()[0]
        vectors = c.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        unvectored = c.execute("SELECT COUNT(*) FROM memories m WHERE COALESCE(m.is_archived,0)=0 AND m.id NOT IN "
                               "(SELECT memory_id FROM %s)" % table).fetchone()[0]
        orphans = c.execute("SELECT COUNT(*) FROM %s WHERE memory_id NOT IN (SELECT id FROM memories)" % table).fetchone()[0]
    finally:
        c.close()
    return {"table": table, "active": active, "vectors": vectors, "unvectored_active": unvectored,
            "orphan_vectors": orphans}


def _device_facts():
    """What the child was told about its device: the backend the plan chose and the env that steers it."""
    return {"backend": os.environ.get("VOIDLOOM_PROBE_BACKEND") or None,
            "WIZARD_ACCELERATOR": os.environ.get("WIZARD_ACCELERATOR"),
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES")}


def _golden():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import golden_vectors
    return golden_vectors


def _tier(a):
    """The tier P6/P7 prove: --tier when given (the preflight has no brain yet), else the brain's declaration."""
    return (getattr(a, "tier", "") or "").strip() or db_facts(a.db).get("tier")


def step_p6(a) -> dict:
    tier = _tier(a)
    out = dict(_device_facts(), tier=tier)
    a.partial_facts = out
    gv = _golden()
    if tier not in gv.TIERS:
        out["skipped"] = "no golden fixture for tier %r (only %s ship one)" % (tier, "/".join(gv.TIERS))
        return out
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("internal: P6 must run with HF_HUB_OFFLINE=1")
    fx = gv.load_fixture(tier)
    docs, queries, f = gv.encode_all(a.organs, tier)
    out.update({"device": f.get("device"), "dim": f.get("dim")})
    r = gv.compare(docs, queries, fx)
    out.update({k: r.get(k) for k in ("min_cosine", "min_cosine_required", "mean_cosine", "top3_identical", "texts")})
    if not r["ok"]:
        raise RuntimeError("golden vectors on %s: %s" % (f.get("device") or "the organs' device", r["reason"]))
    return out


RERANK_TRIPLES = [
    ("What is the capital of China?", "The capital of China is Beijing.",
     "Gravity is a force that attracts two bodies towards each other."),
    ("how does the brain consolidate memories overnight",
     "The sleep loop consolidates episodic memories into semantic summaries every night.",
     "Photosynthesis converts light energy into chemical energy stored in glucose."),
    ("what does a reranker do", "A reranker scores each query and passage pair together to order search results.",
     "SQLite stores the whole database in a single file on disk."),
]


def step_p7(a) -> dict:
    out = dict(_device_facts())
    a.partial_facts = out
    if str(getattr(a, "reranker", "") or "0") != "1":
        out["skipped"] = "the reranker gate is OFF on this machine: nothing to prove, nothing was downloaded"
        return out
    if os.environ.get("HF_HUB_OFFLINE") != "1":
        raise RuntimeError("internal: P7 must run with HF_HUB_OFFLINE=1")
    sys.path.insert(0, a.organs)
    import qwen_reranker as qr
    bundle = qr.load_model()
    if bundle is None:
        raise RuntimeError("the reranker did not load: %s" % qr.absence_reason())
    pairs = []
    for q, rel, irr in RERANK_TRIPLES:
        sc = qr.score_pairs(q, [rel, irr])
        if sc is None:
            raise RuntimeError("the reranker could not score: %s" % qr.absence_reason())
        pairs.append([round(float(sc[0]), 6), round(float(sc[1]), 6)])
    out.update({"device": str(getattr(bundle.get("model"), "device", None)) if isinstance(bundle, dict) else None,
                "relevant_vs_irrelevant": pairs})
    bad = [i for i, (r, n) in enumerate(pairs) if not (r > n and 0.0 <= r <= 1.0 and 0.0 <= n <= 1.0)]
    if bad:
        raise RuntimeError("the reranker ranked the irrelevant passage at or above the relevant one (or scored outside "
                           "[0,1]) for fixture quer%s %s: %s" % ("y" if len(bad) == 1 else "ies", bad, pairs))
    return out


P8_QUERIES = ("memory", "how the installer works", "sleep consolidation", "semantic recall", "what the brain knows")


def step_p8(a) -> dict:
    import statistics
    import time
    facts = db_facts(a.db)
    tier = facts.get("tier")
    if not tier:
        raise RuntimeError("the DB declares no recognised Tier-1 table: %r" % facts)
    out = dict(_device_facts(), tier=tier, queries=len(P8_QUERIES))
    a.partial_facts = out
    sys.path.insert(0, a.organs)
    import claude_memory as cm
    import hybrid_retrieval as hr
    import sqlite_vec
    cm.DB_PATH = Path(a.db)
    conn = sqlite3.connect(_ro_uri(a.db), uri=True)
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        t0 = time.perf_counter()
        hr.recall_hybrid(conn, "warm up", cm._encode("warm up", prefix="search_query"), top_k=5, mode="hybrid")
        out["warmup_s"] = round(time.perf_counter() - t0, 3)
        times, rows = [], []
        for q in P8_QUERIES:
            t = time.perf_counter()
            r = hr.recall_hybrid(conn, q, cm._encode(q, prefix="search_query"), top_k=5, mode="hybrid")
            times.append(round(time.perf_counter() - t, 4))
            rows.append(len(r))
    finally:
        conn.close()
    out.update({"times_s": times, "rows": rows, "p50_s": round(statistics.median(times), 4)})
    if not any(rows):
        raise RuntimeError("five recalls against the brain returned nothing")
    return out


STEPS = {"P1": step_p1, "P2": step_p2, "P3": step_p3, "P4": step_p4, "P5": step_p5, "P6": step_p6, "P7": step_p7,
         "P8": step_p8, "counts": counts}


# --------------------------------------------------------------------------- the driver
def _hides_every_card(env: dict, organs: str) -> bool:
    """organs/voidloom_device.hides_every_cuda_card(env): the resolver's own "selects no device" class ('', '-1',
    'none', 'NoDevFiles', '-1,1', an index nvidia-smi does not list...). If the package's resolver cannot be imported,
    CUDA's documented reading stands in: the list is read up to its first entry that is neither an index nor a
    GPU-/MIG- UUID, and an empty reading hides every card."""
    if "CUDA_VISIBLE_DEVICES" not in env:
        return False
    try:
        if organs and organs not in sys.path:
            sys.path.insert(0, organs)
        import voidloom_device
        return bool(voidloom_device.hides_every_cuda_card(env))
    except Exception:
        first = (env.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0].strip()
        return not (first.isdigit() or first.startswith(("GPU-", "MIG-")))


def _child_env(a, name: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # FORCED, never setdefault: the device the units will be pinned to, or none on --force-cpu
    # (an EMPTY CUDA_VISIBLE_DEVICES is CUDA's own "no devices").
    if name in ("P6", "P7", "P8") and not a.force_cpu and not (a.pin or ""):
        # a machine with no NVIDIA pin (MPS, ROCm, XPU): the device env is left exactly as the units will have it --
        # an EMPTY CUDA_VISIBLE_DEVICES would hide a ROCm card too (HIP honours it)
        pass
    else:
        env["CUDA_VISIBLE_DEVICES"] = "" if a.force_cpu else (a.pin or "")
    if _hides_every_card(env, a.organs):
        # every card hidden: torch's driver probe segfaults on WSL2 (torch 2.6.0+cu124); its NVML probe answers False.
        # Asked of the resolver's own class (round 2, M1), and for EVERY child -- an inherited 'none' crashed too.
        env["PYTORCH_NVML_BASED_CUDA_CHECK"] = "1"
    if a.force_cpu:
        env["WIZARD_ACCELERATOR"] = "cpu"          # contract C2: the organs' resolver answers the CPU
    if getattr(a, "backend", ""):
        env["VOIDLOOM_PROBE_BACKEND"] = a.backend
    if name in ("P6", "P7", "P8"):
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["WIZARD_EMBED_MODEL"] = _tier(a) or ""
        if a.db:
            env["WIZARD_LIBRARY_DB"] = a.db
    if name == "P3":
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["WIZARD_EMBED_MODEL"] = db_facts(a.db).get("tier") or ""
        env["WIZARD_LIBRARY_DB"] = a.db
    return env


def _tail(text: str, n: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "..." + text[-n:]


def run(a) -> int:
    wanted = [s.strip() for s in a.steps.split(",") if s.strip()]
    bad = [s for s in wanted if s not in STEPS]
    if bad:
        print(json.dumps({"ok": False, "error": "unknown step(s) %s" % bad}))
        return 2
    report = {"ok": True, "interpreter": sys.executable, "db": a.db, "steps": [], "failed_step": None}
    for name in wanted:
        cmd = [sys.executable, "-B", os.path.abspath(__file__), "step", name, "--db", a.db, "--organs", a.organs,
               "--manifest", a.manifest or "", "--pin", a.pin or "", "--display-index", a.display_index or "",
               "--pin-declared-by", a.pin_declared_by or "", "--pin-source", a.pin_source or "",
               "--env-file", getattr(a, "env_file", "") or "",
               "--backend", getattr(a, "backend", "") or "", "--reranker", getattr(a, "reranker", "") or "0",
               "--tier", getattr(a, "tier", "") or "",
               "--requirements", a.requirements or ""]
        if a.force_cpu:
            cmd.append("--force-cpu")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, env=_child_env(a, name), timeout=a.timeout)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            rc, out, err = 124, "", "timed out after %ss: %s" % (a.timeout, e)
        facts = None
        for line in reversed((out or "").splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    facts = json.loads(line)
                    break
                except ValueError:
                    continue
        ok = rc == 0 and isinstance(facts, dict) and facts.get("ok") is True
        entry = {"step": name, "rc": rc, "ok": ok,
                 "facts": (facts or {}).get("facts"), "error": (facts or {}).get("error"),
                 "stderr_tail": _tail(err)}
        if rc < 0 and not entry["error"]:
            # a step KILLED by a signal leaves no JSON and no traceback (iteration 3, item 10: measured, P3 under
            # CUDA_VISIBLE_DEVICES='' segfaults torch on the reference host and install.sh printed "(rc -11): .")
            import signal as _signal
            try:
                _name = _signal.Signals(-rc).name
            except ValueError:
                _name = "unknown signal"
            entry["error"] = "the step was KILLED by signal %d (%s) -- no Python traceback exists" % (-rc, _name)
        report["steps"].append(entry)
        if not ok:
            report["ok"] = False
            report["failed_step"] = name
            break
    print(json.dumps(report, default=str))
    return 0 if report["ok"] else 1


def one_step(a) -> int:
    import contextlib
    import io
    buf = io.StringIO()     # the organs print their own refusals ({"status":"error","message":...}) then sys.exit(1)
    try:
        with contextlib.redirect_stdout(buf):
            facts = STEPS[a.name](a)
        sys.stderr.write(buf.getvalue())
        print(json.dumps({"ok": True, "step": a.name, "facts": facts}, default=str))
        return 0
    except BaseException as e:  # noqa: BLE001 - an ImportError is the P1 answer, not a crash
        said = buf.getvalue()
        sys.stderr.write(said)
        why = "%s: %s" % (type(e).__name__, e)
        if isinstance(e, SystemExit):
            # a bare "SystemExit: 1" told the owner nothing (friend-package smoke, 2026-10-16): carry what the
            # organ printed before it exited
            msg = ""
            for line in reversed([ln for ln in said.strip().splitlines() if ln.strip()]):
                try:
                    d = json.loads(line)
                    msg = str(d.get("message") or d.get("error") or "")
                except ValueError:
                    msg = line.strip()
                if msg:
                    break
            if msg:
                why += " -- " + msg[-600:]
        # a step that records facts as it goes (P4) reports what it OBSERVED even when it refuses
        print(json.dumps({"ok": False, "step": a.name, "error": why,
                          "facts": getattr(a, "partial_facts", None)}, default=str))
        return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prove an ambient Tier 1 through the candidate interpreter.")
    sub = ap.add_subparsers(dest="verb")
    for verb in ("run", "step"):
        s = sub.add_parser(verb)
        if verb == "step":
            s.add_argument("name", choices=sorted(STEPS))
        else:
            s.add_argument("--steps", default="P1,P2,P4,P3,P5")
            s.add_argument("--timeout", type=int, default=900)
        s.add_argument("--db", default="", help="the brain (P1-P5, P8, counts); P6/P7 take --tier instead when there is none")
        s.add_argument("--tier", default="", help="P6/P7: the embedding tier to prove (default: the brain's declaration)")
        s.add_argument("--organs", required=True, help="the PACKAGE tree's organs/ (the code about to be laid)")
        s.add_argument("--manifest", default="", help="the brain's voidloom_manifest.json (P4)")
        s.add_argument("--pin", default="", help="the CUDA device the units will be pinned to (P4, P3)")
        s.add_argument("--display-index", default="",
                       help="the owner's WIZARD_DISPLAY_INDEX (<index>|none), read by install.sh from the env file "
                            "OUTSIDE the managed block; used by P4 only when no card reports an active display")
        s.add_argument("--pin-declared-by", default="", choices=("",) + PIN_DECLARATION_KEYS,
                       help="the env-file key OUTSIDE the managed block that declares the pin, read by install.sh "
                            "(empty: the pin is the installer's own choice); P4 requires it when the display basis "
                            "is owner-declared (ruler VL-REF-v1.2)")
        s.add_argument("--pin-source", default="", help="where the pin came from, in words (P4's messages and facts)")
        s.add_argument("--env-file", default="", help="the env file the units read (P4 names it in what to add)")
        s.add_argument("--requirements", default="", help="install/requirements.txt (P5)")
        s.add_argument("--backend", default="", help="the accelerator the plan chose (P6-P8 facts)")
        s.add_argument("--reranker", default="0", choices=("0", "1"), help="the reranker gate: 1 = ON (P7 runs)")
        s.add_argument("--force-cpu", action="store_true")
    a = ap.parse_args(argv)
    if a.verb == "run":
        return run(a)
    if a.verb == "step":
        return one_step(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
