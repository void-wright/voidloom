#!/usr/bin/env python3
"""voidloom.cli — argparse dispatch + the Voidloom ``--json`` envelope writer.

This is the toolkit's front door. It:
  * parses the v0.1 verb surface (bootstrap, recall, store, context, approvals,
    agent-context, doctor, capabilities, batch-recall, batch-store) plus the four
    ORGAN verbs (hooks, instructions, preamble, canary), which dispatch to
    organs/voidloom_<verb>.py::run(argv) -> envelope and print through THIS
    module's _emit exactly once (R1);
  * runs the local-FS guard (the auth model) where a verb touches the brain;
  * validates inputs structurally (category enum, approvals ref-regex);
  * dispatches to the lazy facade / stdlib bootstrap / approvals backend;
  * emits a uniform envelope: ``{status: ok|error|refused, code, data}``.

The envelope is the contract. Callers (LLMs, scripts, tests) assert on
``status`` + ``code`` — NEVER on substring error text.

Stdlib-purity: importing this module imports only stdlib + the stdlib-pure
voidloom submodules. The facade (and through it claude_memory + torch) is
imported LAZILY, inside the verbs that need the brain. ``bootstrap`` and
``agent-context`` never import the facade at all.
"""
from __future__ import annotations

import argparse
import contextlib  # stdlib; used by the --quiet stderr-silence guard
import json
import os
import re
import sqlite3  # stdlib; used only to type-match the engine's read-only error
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from . import _agent_context
from . import _bootstrap
from . import _guard
from . import _paths

# ---------------------------------------------------------------------------
# Envelope: the single output contract.
# ---------------------------------------------------------------------------

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_REFUSED = "refused"
# C5: the organs have advertised `incomplete`/rc 4 since they were written (see
# the sidecar contract below), and the CLI already honours it for an organ's own
# envelope. It did not exist for the CLI's OWN verbs, so a batch that lost items
# had nowhere honest to land and reported `ok`. Some work happened and some did
# not: that is neither ok, nor an error, nor a refusal.
STATUS_INCOMPLETE = "incomplete"
_VALID_STATUS = (STATUS_OK, STATUS_ERROR, STATUS_REFUSED, STATUS_INCOMPLETE)


def _envelope(status: str, code: str, data: Any,
              verb: Optional[str] = None) -> Dict[str, Any]:
    assert status in _VALID_STATUS, f"bad status {status!r}"
    env: Dict[str, Any] = {"status": status, "code": code, "data": data}
    if verb is not None:
        env["verb"] = verb
    return env


def ok(code: str, data: Any, verb: Optional[str] = None) -> Dict[str, Any]:
    return _envelope(STATUS_OK, code, data, verb)


def incomplete(code: str, data: Any, verb: Optional[str] = None) -> Dict[str, Any]:
    """Some of the work landed and some did not — exit 4.

    C5. Measured before this existed: `batch-store` with 3 items returned
    `status: ok`, exit 0, `succeeded 1 / failed 2`. A caller writing
    `voidloom batch-store … && echo done` printed done while two thirds of the
    batch was lost. The count was right there in the payload; only the verdict
    beside it was wrong.
    """
    return _envelope(STATUS_INCOMPLETE, code, data, verb)


def error(code: str, message: str, data: Any = None,
          verb: Optional[str] = None) -> Dict[str, Any]:
    payload = {"message": message}
    if data is not None:
        payload["detail"] = data
    return _envelope(STATUS_ERROR, code, payload, verb)


def refused(code: str, message: str, data: Any = None,
            verb: Optional[str] = None) -> Dict[str, Any]:
    payload = {"message": message}
    if data is not None:
        payload["detail"] = data
    return _envelope(STATUS_REFUSED, code, payload, verb)


def _exit_code_for(env: Dict[str, Any]) -> int:
    """Process exit code from an envelope: ok->0, error->1, refused->3.
    A distinct refusal code lets callers tell 'blocked by policy' from 'failed'."""
    status = env.get("status")
    if status == STATUS_OK:
        return 0
    if status == STATUS_REFUSED:
        return 3
    if status == STATUS_INCOMPLETE:
        return 4
    return 1


# THE ORGAN SIDECARS.  An organ's run(argv) returns ONE envelope that may carry
# its own exit code (`_exit_code`: 0 ok / 3 refused / 4 incomplete / 1 error —
# the codes the organs have always advertised) and its own human rendering
# (`_human`).  Both are POPPED here, before printing, so the envelope on stdout
# is the organ's envelope and nothing else, printed ONCE, and the exit code the
# organ decided is the process exit code (R1: the envelope flows once, the exit
# code is preserved).
EXIT_CODE_KEY = "_exit_code"
HUMAN_KEY = "_human"


def _emit(env: Dict[str, Any], as_json: bool) -> int:
    """Write the envelope (JSON when --json, else a calm human view) + return the
    process exit code."""
    rc_override = None
    human = None
    if isinstance(env, dict):
        rc_override = env.pop(EXIT_CODE_KEY, None)
        human = env.pop(HUMAN_KEY, None)
    if as_json:
        print(json.dumps(env, ensure_ascii=False, indent=2, default=str))
    elif isinstance(human, str):
        print(human)
    else:
        print(_render_human(env))
    if rc_override is not None:
        try:
            return int(rc_override)
        except (TypeError, ValueError):
            pass
    return _exit_code_for(env)


def _render_human(env: Dict[str, Any]) -> str:
    status = env.get("status")
    code = env.get("code", "")
    verb = env.get("verb", "")
    data = env.get("data")
    head = f"voidloom {verb}".strip()
    if status == STATUS_OK:
        # Face 2 (--present): the pop-up CARD is the human surface — print it
        # directly (it is already VOIDWRIGHT-styled + degrades by terminal),
        # not the raw envelope. The AskUserQuestion payload only matters to the
        # machine path (--json), so it is intentionally omitted here.
        if code == "approvals_present" and isinstance(data, dict) and data.get("card"):
            return str(data["card"])
        # capabilities (--json off): render the full prose organ view directly
        # (the structured projection only matters to the machine/--json path).
        if code == "capabilities" and isinstance(data, dict) and data.get("organs"):
            sa = _import_self_aware()
            if sa is not None:
                try:
                    return sa.render_full_text(data)
                except Exception:
                    pass
        # recall / batch-recall / context (--json off): a legible MEMORY-CARD
        # surface, not the raw ~24-field scoring envelope (H5). The full
        # per-result scoring math stays available on the --json path (and is
        # surfaced inline under --explain); the default human view leads with
        # the matched memory TEXT. Fail-graceful: any shape surprise falls
        # through to the generic pretty-dump so a render hiccup never hides a
        # successful result.
        if code in ("recall_ok", "batch_recall_ok", "context_ok"):
            try:
                card = _render_recall_human(env, code, head)
                if card is not None:
                    return card
            except Exception:
                pass
        return f"[ok] {head} ({code})\n{_pretty(data)}"
    if status == STATUS_REFUSED:
        msg = data.get("message") if isinstance(data, dict) else data
        out = f"[refused:{code}] {head}\n  {msg}"
        # R7/C2 -- THE HUMAN SURFACE OF A PARTIAL. `_render_human` printed the
        # message and nothing else, so a payload carrying a perfectly good
        # `unified_count` reached the terminal as a bare "I could not tell".
        # A refusal that HAS a count prints it; a refusal that has none is
        # unchanged.
        if isinstance(data, dict) and data.get("unified_count") is not None:
            _bd = data.get("breakdown") or {}
            _parts = ", ".join(f"{k}: {v}" for k, v in sorted(_bd.items()))
            _un = data.get("unreadable_sources") or []
            out += (f"\n  at least {data['unified_count']} pending"
                    + (f" ({_parts})" if _parts else "")
                    + (f" -- UNREAD: {', '.join(_un)}" if _un else ""))
        return out
    msg = data.get("message") if isinstance(data, dict) else data
    out = f"[error:{code}] {head}\n  {msg}"
    # H7: a rich error carries a `detail.valid` enum (e.g. invalid_category lists
    # the allowed categories). The --json path already exposes it; surface it on
    # the plain path too so a human who didn't add --json is told WHAT is allowed,
    # not merely THAT the input was wrong.
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, dict):
            valid = detail.get("valid")
            if isinstance(valid, (list, tuple)) and valid:
                out += "\n  valid: " + ", ".join(str(v) for v in valid)
    return out


def _pretty(data: Any) -> str:
    if isinstance(data, str):
        return data
    try:
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(data)


# ---------------------------------------------------------------------------
# Human memory-card render (H5). The default (non-`--json`) recall surface leads
# with the matched memory TEXT — a compact, scannable card per hit — instead of
# the raw ~24-field scoring envelope. The full scoring math stays on `--json`
# (and surfaces inline under `--explain`); the card is the legible-by-default
# contract for a human at a prompt. NOTE: the `--json` path is untouched — it
# still emits the canonical `{status,code,data,verb}` envelope verbatim, so the
# machine contract does not regress.
# ---------------------------------------------------------------------------

# The fields a memory row may carry that hold its prose (first populated wins).
_MEMORY_TEXT_KEYS = ("summary", "content", "text", "body")
# Max characters of memory prose shown per card before truncation.
_CARD_TEXT_MAX = 240


def _card_text(row: Dict[str, Any]) -> str:
    """The most useful one-shot prose for a memory row: prefer ``summary``, else
    ``content`` (et al.), trimmed + single-lined + length-capped for a card."""
    for key in _MEMORY_TEXT_KEYS:
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            text = " ".join(val.split())  # collapse newlines/runs of whitespace
            if len(text) > _CARD_TEXT_MAX:
                text = text[: _CARD_TEXT_MAX - 1].rstrip() + "…"
            return text
    return "(no text)"


def _card_score(row: Dict[str, Any]) -> str:
    """A compact score tag for the card header. Prefers ``score`` (higher=better),
    falls back to ``distance`` (lower=better); empty when neither is present."""
    score = row.get("score")
    if isinstance(score, (int, float)):
        return f" score={score:.3f}"
    dist = row.get("distance")
    if isinstance(dist, (int, float)):
        return f" dist={dist:.3f}"
    return ""


def _card_why(row: Dict[str, Any]) -> str:
    """A one-line 'why-surfaced' tail from the honest sub-scores already on the
    row (lens / foundation / ppr / diversity). Render-only — never recomputed.
    Empty when no sub-scores are present (so a plain row gets a clean 2-line
    card)."""
    bits: List[str] = []
    lm = row.get("lens_multiplier")
    if isinstance(lm, (int, float)) and lm not in (1, 1.0):
        bits.append(f"lens×{lm:.2f}")
    if row.get("foundation_boosted"):
        bits.append("foundation+")
    if row.get("ppr_boosted"):
        bits.append("ppr+")
    dr = row.get("diversity_rank")
    if isinstance(dr, int):
        bits.append(f"div#{dr}")
    if row.get("provenance_framed"):
        bits.append("framed")
    return ("  why: " + " · ".join(bits)) if bits else ""


def _render_memory_card(row: Dict[str, Any], *, indent: str = "") -> str:
    """One memory -> a 2-3 line card:
        #<id> [<category>·<project>]<score>
          <summary/content, trimmed>
          why: <sub-score factors>   (only when present)
    """
    if not isinstance(row, dict):
        return f"{indent}{_pretty(row)}"
    mid = row.get("id")
    cat = row.get("category") or ""
    proj = row.get("source_project") or row.get("project") or ""
    tag = "·".join(p for p in (cat, proj) if p)
    tag = f" [{tag}]" if tag else ""
    header = f"{indent}#{mid}{tag}{_card_score(row)}"
    lines = [header, f"{indent}  {_card_text(row)}"]
    why = _card_why(row)
    if why:
        lines.append(f"{indent}{why}")
    return "\n".join(lines)


def _degrade_banner(data: Dict[str, Any]) -> Optional[str]:
    """One line naming a degraded answer, for the surface a HUMAN reads.

    CRUCIBLE-3 D6-F6 (LIES): `_render_recall_human` built its card block from
    `data["results"]` and never read `data["degraded"]`, `data["tier"]` or
    `data["engine"]` -- `grep -n degraded cli.py` returned hits only in the
    read-only-DB path, never in a renderer. So a keyword-only answer over a brain
    with no working semantic tier rendered EXACTLY like a full hybrid one, on every
    non-`--json` recall, under any degrade. The envelope told the truth and the
    human surface did not repeat it.
    """
    if not isinstance(data, dict):
        return None
    deg = data.get("degraded")
    if not deg:
        return None
    reason = (deg.get("reason") if isinstance(deg, dict) else None) or "Tier 1 unavailable"
    means = (deg.get("means") if isinstance(deg, dict) else None) or ""
    eng = data.get("engine") or "tier-0 engine"
    return (f"  ⚠ DEGRADED — tier {data.get('tier', 0)} ({eng}): {reason}."
            + (f"\n     {means}" if means else "")
            + "\n     (--json for the full degrade block)")


def _doctor_gpu_pin() -> Dict[str, Any]:
    """The pin THIS process's CUDA_VISIBLE_DEVICES resolves to (organs/voidloom_device.py), with the refusal made
    VISIBLE (iteration 5): a display-card or unresolvable-UUID refusal shows as refused: true and the same one-line
    reason the engine prints at the point of use. Informational: the units resolve their own environment. Never raises."""
    out: Dict[str, Any] = {"cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES")}
    try:
        od = str(_paths.organ_dir())
        if od not in sys.path:
            sys.path.insert(0, od)
        import voidloom_device as _vd  # type: ignore
        value, basis = _vd.resolve_cuda_device()
        out.update({"value": value, "basis": basis, "degraded": _vd.is_degraded(basis),
                    "refused": _vd.is_refused(basis)})
        if out["refused"]:
            out["line"] = _vd.refusal_line(out["cuda_visible_devices"], basis)
        # round 2, M5: on a CPU-declared brain the pin above is a fact about the shell, not about this brain -- a row
        # that said "card 1" beside "accelerator cpu" read as a contradiction
        # voidloom_device.cpu_declaration() (lane R2b) -> 'declared:cpu (<layer>)' or None; an organ that predates it
        # is asked the same question through declared_accelerator()
        _cd = getattr(_vd, "cpu_declaration", None)
        if _cd is not None:
            decl = _cd()
        else:
            _v, _b = _vd.declared_accelerator()
            decl = ("declared:cpu (%s)" % _b) if _v == "cpu" else None
        if decl:
            out["not_used"] = "not used: CPU declared -- %s; no job of this brain uses a card" % decl
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _doctor_reranker() -> Dict[str, Any]:
    """The accelerator section (handoff round 1): backend, device, basis, proof label, and the reranker decision with
    its reason (accelerator, RAM vs the 15.0 GiB threshold, or the OFF switch that decided it), the installer's stored
    sweep beside the live answer, and "enabled but not cached". Stdlib only -- doctor never imports torch, so this is
    the resolver's planned answer; the recall path re-checks live. Never raises."""
    try:
        od = str(_paths.organ_dir())
        if od not in sys.path:
            sys.path.insert(0, od)
        import hybrid_retrieval as _hr  # type: ignore  (stdlib at import; loads no model)
        rec = str(_paths.brain_dir() / "INSTALL_RECORD")      # the brain home, where the installer writes it
        st = _hr.reranker_status(install_record=rec if os.path.isfile(rec) else None, confirm=False)
        st["line"] = _hr.reranker_banner_line(st)
        return st
    except Exception as e:  # noqa: BLE001 - never break doctor
        return {"error": f"{type(e).__name__}: {e}"}


def _doctor_accelerator() -> Dict[str, Any]:
    """The accelerator THIS brain computes on (contract C1, stdlib -- no torch), what was declared (C2), its proof label
    (C4, as the banner reads it) and ``cpu_only`` -- True whenever the backend is the CPU, which a CPU-declared brain
    must show (round 2, M5; criterion 2). Never raises."""
    try:
        od = str(_paths.organ_dir())
        if od not in sys.path:
            sys.path.insert(0, od)
        import voidloom_device as _vd  # type: ignore
        import hybrid_retrieval as _hr  # type: ignore  (stdlib at import; loads no model)
        r = _vd.accelerator_report(confirm=False)
        backend = r.get("backend") or "cpu"
        # cpu_only must be TRUE about library loaders too: only a CPU declaration (which hides every card) or an
        # environment that already hides them makes it so. A degraded declaration answers "cpu" while a card stays
        # visible -- peft & co. would still open a CUDA context there (round 2 reflection N2).
        hidden = (getattr(_vd, "cpu_declaration", lambda: None)() is not None
                  or getattr(_vd, "hides_every_cuda_card", lambda: False)())
        degraded = bool(getattr(_vd, "is_degraded", lambda b: False)(r.get("basis") or ""))
        rec = str(_paths.brain_dir() / "INSTALL_RECORD")
        label, source = _hr.accelerator_proof_label(backend, rec if os.path.isfile(rec) else None)
        declared = r.get("declared") or {}
        return {"backend": backend, "torch_device": r.get("torch_device"), "basis": r.get("basis"),
                "declared": declared, "cpu_only": backend == "cpu" and hidden, "degraded": degraded,
                "cpu_declared": declared.get("value") == "cpu",
                "label": label, "label_source": source,
                "line": "%s [%s] -- %s%s" % (backend, label or "?", r.get("basis") or "",
                                             "; CPU only: no job of this brain uses a card" if backend == "cpu" and hidden
                                             else ("; DEGRADED: the resolver answers CPU, but CUDA_VISIBLE_DEVICES=%s "
                                                   "stays visible to library loaders -- repair the env file"
                                                   % os.environ.get("CUDA_VISIBLE_DEVICES", "<unset>"))
                                             if backend == "cpu" else "")}
    except Exception as e:  # noqa: BLE001 - never break doctor
        return {"error": f"{type(e).__name__}: {e}"}


def _doctor_gpu_declaration() -> Dict[str, Any]:
    """WIZARD_CUDA_DEVICE as the units receive it -- the environment, then the env file read as systemd reads it
    (install/env_file.py through voidloom_device) -- each '-1' / 'none' spelling reported as CPU only. Never raises."""
    out: Dict[str, Any] = {"key": "WIZARD_CUDA_DEVICE", "seen": [], "cpu_only": []}
    seen = []
    env = os.environ.get("WIZARD_CUDA_DEVICE")
    if env is not None:
        seen.append(("the environment", env))
    try:
        od = str(_paths.organ_dir())
        if od not in sys.path:
            sys.path.insert(0, od)
        import voidloom_device as _vd  # type: ignore
        path = _vd._env_file_path()
        reader = _vd._systemd_reader()
        if reader is not None and path.is_file():
            got = reader.parse(path.read_bytes())["effective"].get("WIZARD_CUDA_DEVICE")
            if got is not None:
                seen.append((str(path), got[0]))
    except Exception as e:  # never break doctor
        out["error"] = f"{type(e).__name__}: {e}"
    for src, val in seen:
        out["seen"].append({"source": src, "value": val})
        v = val.split("#", 1)[0].strip().strip("'\"").lower()
        if v in ("-1", "none"):
            out["cpu_only"].append({"source": src, "value": val,
                                    "note": "CPU only: every consumer runs with no GPU (CUDA_VISIBLE_DEVICES='')"})
    out["meaning"] = ("WIZARD_CUDA_DEVICE=-1 / none means NO GPU to every consumer (the owner's ruling); the product "
                      "never writes it -- install with --force-cpu for a CPU run")
    return out


def _embedding_coherence(db_path: Optional[str]) -> Dict[str, Any]:
    """Does the brain's recorded embedding tier AGREE with the bytes in its table?

    CRUCIBLE-3 D6-F7: `metadata.active_embedding_dim` is WRITTEN BY TWO SITES
    (`organs/claude_memory.py:965`, `organs/embedding_upgrade.py:894`) AND READ BY
    NONE -- a value written to a store with no read path, which is the UNBOUND
    class. D6 named it exactly: *"the brain writes down the one number that would
    catch all three [of my findings] — `metadata.active_embedding_dim` — and
    nothing reads it."*

    This is that reader. It compares the RECORDED dim against the width of a
    vector actually stored in the active table, so a 768/1024 mismatch — which
    the engine logs to stderr and then drops from an envelope that says
    `status: ok` — becomes a named check on the health verb instead.

    Never raises: every failure is a returned reason, because `doctor` must
    answer on a broken brain as well as a healthy one. "Cannot tell" is its own
    state and is not folded into "fine".
    """
    out: Dict[str, Any] = {"recorded_dim": None, "recorded_table": None,
                           "recorded_model": None, "observed_dim": None,
                           "agrees": None, "reason": None}
    if not db_path or not os.path.exists(db_path):
        out["reason"] = "no brain at the resolved path"
        return out
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            try:
                import sqlite_vec           # the active table is usually vec0
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
                conn.enable_load_extension(False)
            except Exception:
                pass
            rows = dict(conn.execute(
                "SELECT key, value FROM metadata WHERE key IN "
                "('active_embedding_dim','active_embedding_table','active_embedding_model')"
            ).fetchall())
            out["recorded_dim"] = rows.get("active_embedding_dim")
            out["recorded_table"] = rows.get("active_embedding_table")
            out["recorded_model"] = rows.get("active_embedding_model")
            tab = out["recorded_table"]
            if not tab:
                out["reason"] = "the brain records no active_embedding_table"
                return out
            # CRUCIBLE-3 D6-F2: COVERAGE, which is the number nobody could see.
            # The shipped seed pack -- 100% of a fresh brain's content -- is loaded
            # through the Tier-0 engine, whose own header says "embedding deferred
            # to Tier 1", and NOTHING EVER PERFORMS THE DEFERRAL: no installer
            # step, no CLI verb. Measured on a fresh install: 66 memories, 0
            # vectors in all three tables, while recall reported mode "hybrid".
            # The existing backfill (embedding_upgrade.py cmd_re_embed) is hardcoded
            # to Jina -> memory_embeddings_v2 and is NOT a generic backfill, so
            # wiring it would write the wrong tier's vectors on a nomic install.
            # Closing the gap needs a tier-aware backfill; until that exists, the
            # gap is at least VISIBLE here rather than silent.
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE COALESCE(is_archived,0)=0"
                ).fetchone()[0]
                vecs = conn.execute(f"SELECT COUNT(*) FROM {tab}").fetchone()[0]
                out["memories_live"] = total
                out["vectors_in_active_table"] = vecs
                out["unembedded"] = max(0, total - vecs) if total is not None else None
                if total and vecs == 0:
                    out["coverage_note"] = (
                        f"{total} live memories and ZERO vectors in {tab}: every recall "
                        f"over this brain is keyword-only, whatever `mode` reports. No "
                        f"shipped verb performs the embedding deferral the Tier-0 loader "
                        f"promises.")
                elif total and vecs < total:
                    out["coverage_note"] = (
                        f"{total - vecs} of {total} live memories have no vector in {tab}; "
                        f"those are reachable by keyword only.")
            except Exception as e:
                out["coverage_error"] = f"{type(e).__name__}: {e}"
            row = conn.execute(f"SELECT embedding FROM {tab} LIMIT 1").fetchone()
            if row is None or row[0] is None:
                out["reason"] = f"{tab} holds no vector to measure"
                return out
            blob = row[0]
            out["observed_dim"] = (len(blob) // 4) if isinstance(blob, (bytes, bytearray)) else None
        finally:
            conn.close()
    except Exception as e:
        out["reason"] = f"{type(e).__name__}: {e}"
        return out
    try:
        rec = int(out["recorded_dim"]) if out["recorded_dim"] is not None else None
    except (TypeError, ValueError):
        rec = None
    if rec is None or out["observed_dim"] is None:
        out["reason"] = "one side is unknown; not compared"
        return out
    out["agrees"] = (rec == out["observed_dim"])
    if not out["agrees"]:
        out["reason"] = (f"the brain records {rec}-dim vectors for {out['recorded_table']} "
                         f"but the table holds {out['observed_dim']}-dim vectors — semantic "
                         f"recall over this table cannot be trusted")
    return out


def _render_recall_human(env: Dict[str, Any], code: str, head: str) -> Optional[str]:
    """Render recall / batch-recall / context as memory cards. Returns the card
    block string, or None to fall through to the generic pretty-dump (a shape we
    do not recognise — never hide a successful result)."""
    data = env.get("data")
    if not isinstance(data, dict):
        return None

    if code == "batch_recall_ok":
        return _render_batch_recall_human(data, head)

    # Single recall / context: matched memories live flat at data["results"].
    results = data.get("results")
    if not isinstance(results, list):
        return None
    count = len(results)
    lines = [f"[ok] {head} — {count} "
             f"match{'es' if count != 1 else ''}"]
    _deg = _degrade_banner(data)          # D6-F6: say it on the human surface too
    if _deg:
        lines.append(_deg)
    if count == 0:
        lines.append("  (no memories matched)")
    for row in results:
        lines.append(_render_memory_card(row))
    lines.extend(_render_channel_block(data))
    lines.append("  (--json for full scoring · --explain for why-surfaced)")
    return "\n".join(lines)


def _clip(text: Any, n: int) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[:n - 1] + "…"


def _render_channel_block(rec: Dict[str, Any], indent: str = "  ") -> List[str]:
    """The side channels AFTER every memory card, one labelled block (the dream fix, A3/A4). Each row already
    carries its frame; nothing here re-words provenance."""
    out: List[str] = []
    rows = rec.get("dreams") if isinstance(rec, dict) else None
    if not isinstance(rows, list) or not rows:
        return out
    out.append(f"{indent}dreams ({len(rows)} — unverified leads the brain synthesised; not recorded facts):")
    for d in rows:
        if not isinstance(d, dict):
            continue
        anchor = d.get("anchor")
        out.append(f"{indent}  ~#{d.get('id')} [{d.get('dream_type') or 'dream'} · "
                   f"{str(d.get('created_at') or '')[:10]} · conf {d.get('confidence')}]"
                   + (f" rides on #{anchor}" if anchor else ""))
        out.append(f"{indent}    {_clip(d.get('summary') or '', 160)}")
        out.append(f"{indent}    full text: voidloom dream show {d.get('id')}")
    return out


def _render_batch_recall_human(data: Dict[str, Any], head: str) -> Optional[str]:
    """Render the COLLAPSED batch shape (H6 contract): ``data["results"]`` is a
    list of per-query records ``{"query": <str>, "results": [<memory>...]}`` —
    the inner ``result.`` layer is gone, so memories live at
    ``.data.results[].results[]``. One labelled block per query."""
    queries = data.get("results")
    if not isinstance(queries, list):
        return None
    succeeded = data.get("succeeded")
    failed = data.get("failed")
    count = data.get("count", len(queries))
    headline = f"[ok] {head} — {count} quer{'y' if count == 1 else 'ies'}"
    if isinstance(succeeded, int) and isinstance(failed, int):
        headline += f" ({succeeded} ok · {failed} failed)"
    lines = [headline]
    for rec in queries:
        if not isinstance(rec, dict):
            lines.append(f"  {_pretty(rec)}")
            continue
        q = rec.get("query", "")
        if rec.get("status") == "error":
            lines.append(f"  • {q!r}: error — {rec.get('message', 'failed')}")
            continue
        hits = rec.get("results")
        if not isinstance(hits, list):
            lines.append(f"  • {q!r}: (no results)")
            continue
        n = len(hits)
        lines.append(f"  • {q!r} — {n} match{'es' if n != 1 else ''}:")
        for row in hits:
            lines.append(_render_memory_card(row, indent="    "))
        lines.extend(_render_channel_block(rec, indent="    "))
    lines.append("  (--json for full scoring; memories at "
                 ".data.results[].results[]; dream leads at .data.results[].dreams[])")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Verb implementations. Each returns an envelope dict.
# ---------------------------------------------------------------------------


def verb_bootstrap(args) -> Dict[str, Any]:
    """STDLIB-ONLY host detect. Never imports the facade/engine/torch."""
    report = _bootstrap.bootstrap(db_path=getattr(args, "db", None))
    return ok("bootstrap_ok", report, verb="bootstrap")


def verb_agent_context(args) -> Dict[str, Any]:
    """Emit the self-describing toolkit manifest. Stdlib-only."""
    m = _agent_context.manifest()
    return ok("agent_context", m, verb="agent-context")


def verb_capabilities(args) -> Dict[str, Any]:
    """The FULL prose+examples projection of the brain's L5 organs (L5-M3).

    Reads the SAME ``organs`` registry from ``voidloom_manifest.json`` that the
    session-start hook reads for its compact block — ONE manifest, TWO
    projections, zero duplication. Stdlib-only (a tiny ``json.loads``); never
    imports the facade/engine/torch.

    ``--all-tiers`` (default) lists every organ, marking which are available on
    this host's tier; ``--available-only`` lists just the ones that pass the
    host-tier filter.
    """
    sa = _import_self_aware()
    if sa is None:
        return error("self_aware_missing",
                     "the _self_aware projection module is not importable from "
                     "the brain dir; cannot render capabilities.",
                     verb="capabilities")
    manifest_path = _manifest_path(getattr(args, "db", None))
    all_tiers = not bool(getattr(args, "available_only", False))
    projection = sa.render_full_projection(manifest_path, all_tiers=all_tiers)
    if projection is None:
        return error("no_organs",
                     "no L5 organ registry found in the manifest "
                     f"({manifest_path}); nothing to describe.",
                     data={"manifest": manifest_path}, verb="capabilities")
    return ok("capabilities", projection, verb="capabilities")


def _no_rerank_this_process(args) -> None:
    """`--no-rerank` (handoff round 1): VOIDLOOM_RERANKER=off for THIS process, which the engine's reranker gate reads
    at call time -- one mechanism for recall and batch-recall (the batch runs every query in this process). A forced
    assignment, never setdefault: an inherited VOIDLOOM_RERANKER=qwen must not survive the flag."""
    if getattr(args, "no_rerank", False):
        os.environ["VOIDLOOM_RERANKER"] = "off"


def verb_recall(args) -> Dict[str, Any]:
    """Facade -> brain recall. Gated by the local-FS guard.

    Read-only-DB typed degrade (Surface 2 item 4): recall LOOKS read-only, but
    the engine's ``cmd_recall`` UNCONDITIONALLY logs access stats for every
    surfaced memory (``UPDATE memories SET last_accessed ...`` +
    ``INSERT INTO access_log ...``) and then ``conn.commit()``. On a read-only DB
    (e.g. a ``chmod 0444`` brain, or one on a read-only mount) those side-writes
    raise ``sqlite3.OperationalError: attempt to write a readonly database`` from
    deep inside the engine. The facade only catches ``SystemExit``, so that
    exception would propagate out of recall and surface as an UNTYPED
    ``unhandled`` crash — a poor, leaky failure mode.

    We detect the read-only DB UP FRONT (via the same guard ``store`` uses) and
    return the calm, typed ``readonly_db`` refusal (exit 3) the guard already
    emits elsewhere — never a crash, never an untyped leak. The detection runs
    BEFORE the heavy ``_facade`` import, so a read-only recall (like a non-local
    one) pulls in NO embedding model. Reads are not lost data — the brain is
    simply un-writable; the refusal explains how to serve reads (remount
    read-write, or recall against a writable copy)."""
    db = getattr(args, "db", None)
    refusal = _guard.require_local(db)
    if refusal is not None:
        return refused(refusal["code"], refusal["message"], data=refusal,
                       verb="recall")

    # READ-ONLY: SERVE THE READ, DO NOT REFUSE IT (2026-09-08).
    # This used to return a typed `readonly_db` refusal. It was well-built and
    # it was the wrong call: it declined to answer a READ because it could not
    # LOG the read. Found by execution -- inside Codex's `--sandbox read-only`
    # the model read the Voidloom preamble, reached for batch-recall unprompted,
    # and got the refusal. Read-only is the default sandbox posture across agent
    # harnesses, so the refusal made the brain unusable on precisely the
    # platforms this work exists to reach. The engine now degrades its
    # access-stat write-back instead of raising; here we record that the recall
    # ran degraded so the envelope SAYS SO rather than quietly dropping
    # telemetry. Losing an access-log row is losing telemetry; refusing the
    # recall is losing the function.
    _ro_degraded = _readonly_refusal(db, action="recall") is not None
    _no_rerank_this_process(args)

    from . import _facade  # lazy: this is the heavy path
    # Voidloom #176 iter-2 (Vesper: B2 race + read-only DIRECTORY). The up-front
    # probe above closes the common case, but it has a TOCTOU gap: a brain that
    # is writable when we probe can be `chmod 0444`'d (or its parent dir made
    # read-only) DURING the recall, and the read-only-DIRECTORY case (file 0644
    # but dir 0555 -> SQLite cannot create the -journal/-wal) can read as
    # writable=True yet still fail the write-back. In both, the engine's
    # access-stat commit raises sqlite3.OperationalError, which the facade lets
    # propagate UNTYPED. We catch ONLY the read-only signature here and re-emit
    # the same typed `readonly_db` refusal -> no untyped leak, no crash. A
    # non-read-only OperationalError is re-raised untouched (not relabeled).
    try:
        res = _recall_via_facade(
            _facade, args.query, top_k=args.top_k,
            explain=getattr(args, "explain", False),
            no_cross_tier=bool(getattr(args, "no_cross_tier", False)),
            db_path=db, leads=getattr(args, "leads", None),
            explore=bool(getattr(args, "explore", False)))
    except sqlite3.OperationalError as exc:
        ro = _readonly_refusal_from_exc(db, exc, action="recall")
        if ro is not None:
            return ro
        if _is_uninitialized_error(exc):
            return _db_uninitialized_error(db, verb="recall")
        raise
    env = _wrap_engine_result(res, verb="recall", ok_code="recall_ok")
    # BIND THE FLAG. A degrade nobody can see is a silent drop, which is the
    # defect class this whole round exists to end -- so the envelope says it.
    if _ro_degraded and isinstance(env.get("data"), dict):
        env["data"]["degraded"] = "readonly_db"
        env["data"]["degraded_detail"] = (
            "the brain is read-only, so this recall was SERVED but its access "
            "stats were not logged; results are complete, telemetry is not")
    return env


def _recall_via_facade(facade, query: str, *, top_k: int, explain: bool,
                       no_cross_tier: bool, db_path, leads: Optional[int] = None,
                       explore: bool = False):
    """Call ``_facade.recall`` forwarding ``--no-cross-tier`` (H12).

    The flag threads to the engine exactly like ``top_k``/``explain`` do: the
    engine reads ``args.no_cross_tier`` and skips the EDDIE enrichment when True
    (belt-and-suspenders alongside C1's default-OFF gate). The facade owns the
    Namespace; this helper passes the kwarg, falling back gracefully if a facade
    build predating the kwarg is in place (no_cross_tier defaults False there, so
    only the explicit-OFF request is lost — never a crash, never the silent
    cross-tier enable that C1 already prevents)."""
    _kw = {}
    if leads is not None:
        _kw["leads"] = leads
    if explore:
        _kw["explore"] = True
    try:
        return facade.recall(query, top_k=top_k, explain=explain,
                             no_cross_tier=no_cross_tier, db_path=db_path, **_kw)
    except TypeError:
        # Facade predates the no_cross_tier kwarg. C1's default-OFF gate already
        # prevents the cross-tier call, so dropping the explicit flag is safe.
        return facade.recall(query, top_k=top_k, explain=explain,
                             db_path=db_path)


def _readonly_refusal(db: Optional[str], *, action: str) -> Optional[Dict[str, Any]]:
    """Return a typed ``readonly_db`` refusal envelope when the brain EXISTS but
    is not writable, else None (proceed).

    This is the recall-path counterpart to ``_guard.require_write``: it reuses
    the guard's writability probe but is applied to a path (recall) that the
    guard would otherwise wave through as ``require_local``-only. We refuse ONLY
    when the file exists and is read-only (``writable is False``); a not-yet-
    created DB (``writable is None``) is NOT refused here — there is nothing to
    read yet and engine init owns that case. Never raises (the guard probe is
    fail-closed-but-graceful)."""
    try:
        verdict = _guard.check(db)
    except Exception:
        # If the probe itself fails, do not block — fall through to the engine,
        # which will surface its own (now at least attempted) result.
        return None
    if not verdict.ok:
        # Locality already handled by the caller's require_local; nothing to add.
        return None
    if verdict.writable is False:
        return refused(
            "readonly_db",
            (f"DB {verdict.db_path!r} is read-only; {action} cannot run because "
             "the engine logs access stats for every surfaced memory and that "
             "write-back cannot commit on a read-only brain. Remount the brain "
             "read-write, fix its file permissions, or run the recall against a "
             "writable copy to serve reads."),
            data={"ok": False, "code": "readonly_db",
                  "db_path": verdict.db_path, "writable": False,
                  "action": action},
            verb="recall",
        )
    return None


# Voidloom #176 iter-2 (Vesper). Signatures SQLite raises when a write is
# attempted against a read-only file OR a read-only directory (it cannot create
# the rollback journal / WAL there). We match these — and ONLY these — to know a
# mid-recall OperationalError is a read-only condition and not some unrelated
# engine error (which must NOT be relabeled).
_READONLY_ERR_SIGNATURES = (
    "readonly database",          # "attempt to write a readonly database"
    "read-only database",         # alternate spelling some builds emit
    "unable to open database file",  # read-only DIR: journal/WAL can't be created
)


def _is_readonly_error(exc: BaseException) -> bool:
    """True iff `exc` (a sqlite3.OperationalError) carries a read-only signature."""
    try:
        msg = str(exc).lower()
    except Exception:
        return False
    return any(sig in msg for sig in _READONLY_ERR_SIGNATURES)


# M6: signatures SQLite raises when a query/insert hits a brain whose schema was
# never created (an empty file pointed at by CLAUDE_MEMORY_DB, or a freshly
# `rm`'d sandbox). The engine does NOT wrap these in its own `_error` (that path
# only fires when the DB FILE is absent — `Database not found ... Run 'init'`);
# a present-but-empty file sails past the open and raises mid-query, escaping the
# facade untyped. We match these — and ONLY these — to emit a calm, typed
# `db_uninitialized` envelope instead of the leaky top-level `unhandled` crash.
_UNINITIALIZED_ERR_SIGNATURES = (
    "no such table",   # "no such table: memories" — the canonical empty-DB signature
    "no such column",  # a partially-/wrongly-initialized brain missing a column
)


def _is_uninitialized_error(exc: BaseException) -> bool:
    """True iff `exc` (a sqlite3.OperationalError) carries a missing-schema
    signature (the brain file exists but has no/incomplete voidloom schema)."""
    try:
        msg = str(exc).lower()
    except Exception:
        return False
    return any(sig in msg for sig in _UNINITIALIZED_ERR_SIGNATURES)


def _db_uninitialized_error(db: Optional[str], *, verb: str) -> Dict[str, Any]:
    """The typed ``db_uninitialized`` envelope for a brain with no schema. Mirrors
    the read-only typed-degrade philosophy: a cold agent handed an empty sandbox
    gets a recovery path, not a cryptic ``unhandled``. Best-effort path recovery
    via the same resolver the rest of the CLI uses."""
    try:
        db_path = _paths.resolve_db(db)
    except Exception:
        db_path = db
    return error(
        "db_uninitialized",
        (f"brain at {db_path!r} has no schema (the file exists but its tables "
         "are missing). Initialise it with `python3 claude_memory.py init`, or "
         "point CLAUDE_MEMORY_DB / WIZARD_LIBRARY_DB at an already-initialised "
         "brain."),
        data={"db_path": db_path, "remedy": "claude_memory.py init"},
        verb=verb,
    )


def _readonly_refusal_from_exc(db: Optional[str], exc: BaseException, *,
                               action: str) -> Optional[Dict[str, Any]]:
    """Build the typed `readonly_db` refusal from a read-only OperationalError that
    escaped the engine MID-recall (the up-front probe's TOCTOU gap + the read-only
    DIRECTORY case the probe can miss). Returns None when the error is NOT a
    read-only signature, so the caller re-raises it untouched (never relabel an
    unrelated engine failure). db_path is recovered best-effort from the guard
    probe, falling back to the raw `db` argument. Never raises."""
    if not _is_readonly_error(exc):
        return None
    db_path: Any = db
    try:
        verdict = _guard.check(db)
        if getattr(verdict, "db_path", None):
            db_path = verdict.db_path
    except Exception:
        pass
    return refused(
        "readonly_db",
        (f"DB {db_path!r} became read-only during {action} (or its directory is "
         "read-only, so SQLite cannot create its journal); the engine's "
         "per-memory access-stat write-back could not commit. Remount the brain "
         "read-write, fix its file/directory permissions, or run the recall "
         "against a writable copy to serve reads."),
        data={"ok": False, "code": "readonly_db",
              "db_path": db_path, "writable": False,
              "action": action, "phase": "mid_recall"},
        verb="recall",
    )


def verb_store(args) -> Dict[str, Any]:
    """Facade -> brain store. Validates the category enum BEFORE any heavy import,
    and gates writes through the local-FS + read-only guard."""
    # SECURITY-HOOK(#176): pre-store secret-detector. The final comprehensive
    # security audit will scan `args.content` here for API keys / tokens / PII
    # and refuse (code=`secret_detected`) before the memory is embedded+stored.
    # Intentionally NOT implemented in v0.1 (deferred, per Drew's call).

    db = getattr(args, "db", None)

    # Category enum validation FIRST — a bad category must never load the
    # embedding model. Read the engine's canonical list (lazy, cheap import;
    # falls back to the documented list if the engine isn't importable).
    from . import _facade
    categories = _facade.engine_categories()
    if args.category not in categories:
        return error("invalid_category",
                     f"category {args.category!r} is not valid",
                     data={"given": args.category, "valid": categories},
                     verb="store")

    if args.importance is not None and not (0.0 <= args.importance <= 1.0):
        return error("invalid_importance",
                     f"importance {args.importance} out of range [0.0, 1.0]",
                     data={"given": args.importance}, verb="store")

    # Write guard: local + writable (read-only DB degrades gracefully).
    refusal = _guard.require_write(db)
    if refusal is not None:
        return refused(refusal["code"], refusal["message"], data=refusal,
                       verb="store")

    try:
        res = _facade.store(args.content, args.category,
                            tags=getattr(args, "tags", "") or "",
                            importance=(args.importance
                                        if args.importance is not None else 0.5),
                            db_path=db, answers=getattr(args, "answers", None))
    except sqlite3.OperationalError as exc:
        # M6: a present-but-empty brain (no schema) raises `no such table:
        # memories` mid-store; emit the typed db_uninitialized envelope rather
        # than let it escape as an untyped top-level `unhandled` crash.
        if _is_uninitialized_error(exc):
            return _db_uninitialized_error(db, verb="store")
        raise
    return _wrap_engine_result(res, verb="store", ok_code="store_ok")


def verb_context(args) -> Dict[str, Any]:
    """Facade -> session-start context load. Gated by the local-FS guard."""
    db = getattr(args, "db", None)
    refusal = _guard.require_local(db)
    if refusal is not None:
        return refused(refusal["code"], refusal["message"], data=refusal,
                       verb="context")
    from . import _facade
    try:
        res = _facade.context(project=getattr(args, "project", None),
                              limit=args.limit, db_path=db)
    except sqlite3.OperationalError as exc:
        # M6: an uninitialized brain (no `memories` table) raises mid-context;
        # emit the typed db_uninitialized envelope, not an untyped crash.
        if _is_uninitialized_error(exc):
            return _db_uninitialized_error(db, verb="context")
        raise
    return _wrap_engine_result(res, verb="context", ok_code="context_ok")


# ---------------------------------------------------------------------------
# Batch verbs (memory #1715: every semantic CLI exposes BOTH targeted + batch).
# The whole point is ONE model load for the whole batch: the facade imports the
# engine once per process and the embedder/reranker are process-cached, so the
# loop in _batch runs N items behind a single load. A shell `for`-loop over the
# single verbs reloads the model every item — this does not.
# ---------------------------------------------------------------------------


def _load_json_array(raw: str) -> Any:
    """Resolve the batch input source -> a parsed JSON value.

    Three input forms (the memory #1715 batch-input contract):
      * ``@/path/to/file.json`` — read the array from a file;
      * ``-``                   — read the array from STDIN;
      * anything else           — the inline JSON array string itself.

    Returns the parsed JSON (the caller asserts it is a list). Raises
    ``ValueError`` (file missing / unreadable / not valid JSON) — the verb
    catches it and emits a typed ``error`` envelope, never a stack trace."""
    src = raw.strip() if isinstance(raw, str) else raw
    if src == "-":
        text = sys.stdin.read()
    elif isinstance(src, str) and src.startswith("@"):
        path = src[1:]
        try:
            from pathlib import Path
            text = Path(path).read_text(encoding="utf-8")
        except Exception as e:
            raise ValueError(f"could not read batch file {path!r}: {e}")
    else:
        text = src
    try:
        return json.loads(text)
    except (ValueError, TypeError) as e:
        raise ValueError(f"batch input is not valid JSON: {e}")


def verb_batch_recall(args) -> Dict[str, Any]:
    """Facade -> N recalls in ONE process (single model load). Gated like recall.

    Input (``--items``): a JSON array via an inline string, ``@file``, or ``-``
    (stdin). Each element is a query string OR ``{"query": .., "top_k": ..}``; a
    batch-wide ``--top-k`` is the default. Emits ONE envelope whose data is
    ``{count, succeeded, failed, results:[...]}`` — per-item isolation means a
    malformed query errors in-place and the batch continues.

    H6 collapsed-shape contract: ``_batch.recall_batch`` returns the COLLAPSED
    per-query record list — each element is
    ``{"query": <str>, "status": .., "results": [<memory>...]}`` (the legacy
    inner ``result.`` wrapper is gone). We fold that list straight into
    ``data["results"]`` unchanged, so the matched memories live at
    ``.data.results[].results[]`` on the ``--json`` path (and the human card
    renders the same shape). The ``--json`` envelope is passed through verbatim —
    no re-nesting, no reshape here.

    The read gates are IDENTICAL to the single ``recall`` verb and run BEFORE the
    heavy import: locality (non-local DB refused) + read-only degrade (recall's
    access-stat write-back cannot commit on a read-only brain). A refused batch
    pulls in NO embedding model."""
    db = getattr(args, "db", None)
    refusal = _guard.require_local(db)
    if refusal is not None:
        return refused(refusal["code"], refusal["message"], data=refusal,
                       verb="batch-recall")
    # Same read-only degrade as `recall` above: serve the read, flag it.
    _ro_degraded = _readonly_refusal(db, action="batch-recall") is not None
    _no_rerank_this_process(args)

    try:
        items = _load_json_array(args.items)
    except ValueError as e:
        return error("invalid_batch_input", str(e), verb="batch-recall")
    if not isinstance(items, list):
        return error("invalid_batch_input",
                     "batch-recall input must be a JSON array",
                     data={"got": type(items).__name__}, verb="batch-recall")

    from . import _batch
    try:
        _kw = {}
        if getattr(args, "leads", None) is not None:
            _kw["leads"] = args.leads
        if getattr(args, "explore", False):
            _kw["explore"] = True
        results = _batch.recall_batch(items, top_k=args.top_k, db_path=db, **_kw)
    except sqlite3.OperationalError as exc:
        ro = _readonly_refusal_from_exc(db, exc, action="batch-recall")
        if ro is not None:
            ro["verb"] = "batch-recall"
            return ro
        if _is_uninitialized_error(exc):
            return _db_uninitialized_error(db, verb="batch-recall")
        raise
    summary = _batch.summarize(results)
    _payload = {**summary, "results": results}
    if _ro_degraded:
        _payload["degraded"] = "readonly_db"
        _payload["degraded_detail"] = (
            "the brain is read-only, so these recalls were SERVED but their "
            "access stats were not logged; results are complete, telemetry is not")
    # C5: same rule as batch-store -- both fold `_batch.summarize`, so both had
    # the same count in the payload and the same wrong verdict beside it.
    _failed = _payload.get("failed") if isinstance(_payload, dict) else None
    _env = (incomplete if _failed else ok)
    return _env("batch_recall_incomplete" if _failed else "batch_recall_ok",
                _payload, verb="batch-recall")


def verb_batch_store(args) -> Dict[str, Any]:
    """Facade -> N stores in ONE process (single model load). Gated like store.

    Input (``--items``): a JSON array via an inline string, ``@file``, or ``-``
    (stdin). Each element is a memory dict (``content`` + ``category`` required;
    ``tags``/``kind``/``importance``/``summary``/``project``/``context``/
    ``source_user``/``auto_edges`` optional — the L5-organ-aware store path).
    Emits ONE envelope ``{count, succeeded, failed, results:[...]}``; per-item
    isolation means one invalid memory errors in-place and the batch continues.

    Write gates are IDENTICAL to the single ``store`` verb: local + writable
    (read-only degrades gracefully). The engine's category enum is read live and
    passed to the batch so an invalid-category item fails cleanly. STRONGLY
    prefer ``--db <temp copy>`` for any batch that must not touch the live brain."""
    db = getattr(args, "db", None)

    # Write guard FIRST — local + writable (read-only DB degrades gracefully).
    refusal = _guard.require_write(db)
    if refusal is not None:
        return refused(refusal["code"], refusal["message"], data=refusal,
                       verb="batch-store")

    try:
        items = _load_json_array(args.items)
    except ValueError as e:
        return error("invalid_batch_input", str(e), verb="batch-store")
    if not isinstance(items, list):
        return error("invalid_batch_input",
                     "batch-store input must be a JSON array",
                     data={"got": type(items).__name__}, verb="batch-store")

    # Read the engine's canonical category enum once (lazy, cheap import) so
    # each item is validated against it inside the batch.
    from . import _facade
    categories = _facade.engine_categories()

    from . import _batch
    results = _batch.store_batch(items, db_path=db, valid_categories=categories)
    summary = _batch.summarize(results)
    # C5: the verdict is taken from the SAME count this envelope prints.
    _env = (incomplete if summary.get("failed") else ok)
    return _env("batch_store_incomplete" if summary.get("failed") else "batch_store_ok",
                {**summary, "results": results}, verb="batch-store")


# Accepted approval ref shapes, verified against voidloom_approvals._parse_ref:
#   self-eval:<int>            (a self_eval_reports.id)
#   belief:<report_batch>      (a belief edge_proposals report_batch string)
#   governance:<report_batch>  (a governance HOLD batch — L5-I-β, now first-class)
# We keep the regex strict but permissive enough for the real batch ids, which
# are slugs like 'belief-sweep-2026-06-10T...'. Allow [A-Za-z0-9_.:-] in the raw
# part (colons appear in ISO timestamps inside belief batch ids).
def _approval_ref_re():
    """R7/C5 -- THE SIXTH SPELLING, NOW DERIVED AND BOUND.

    This was a regex literal encoding the same three words that
    `decision_units` declares, that `voidloom_approvals` declares AGAIN, and
    that `voidloom_approvals` encodes as a regex a second time. Nothing bound
    any of them. Measured 2026-09-04: a one-character drift in the source
    declaration imports cleanly, asserts nothing and dispatches 0 of 9 units,
    while this regex keeps matching the stored `unit_key` -- so a ref VALIDATES
    and then resolves to nothing.

    The pattern is now taken from the module that owns the vocabulary, and a
    disagreement raises here rather than emptying a verb.
    """
    # The vocabulary owners live in organs/ (the 2026-09-07 layout). Put that dir
    # on the path FIRST: before the move both owners sat in the brain dir, which
    # some other call had usually already inserted, so a bare import happened to
    # work. That is a dependency on call ORDER, and it is exactly what made this
    # degrade to shape-only validation with nothing but a stderr line to say so.
    try:
        _od = str(_paths.organ_dir())
        if _od not in sys.path:
            sys.path.insert(0, _od)
    except Exception:
        pass
    try:
        import voidloom_approvals as _va  # type: ignore
    except Exception:
        _va = None
    if _va is not None and getattr(_va, "APPROVAL_REF_RE", None) is not None:
        return _va.APPROVAL_REF_RE
    try:
        import decision_units as _du  # type: ignore
    except Exception:
        _du = None
    if _du is not None and getattr(_du, "APPROVAL_REF_PATTERN", None):
        return re.compile(_du.APPROVAL_REF_PATTERN)
    # NEITHER OWNER IS REACHABLE, SO THE VERB VALIDATES SHAPE AND NOT
    # VOCABULARY. Re-spelling the three names here would make this file a
    # fourth declaration of them -- the exact defect C5 exists to end -- and it
    # would be the worst copy of the four, because it is the one that runs when
    # the owners are unreachable and therefore the one nothing could ever
    # cross-check. A structurally valid ref with an unknown surface is refused
    # downstream by the resolver, which DOES know the vocabulary; a malformed
    # ref is still refused here. The gap is stated rather than papered over.
    print("[voidloom.cli] neither voidloom_approvals nor decision_units is "
          "importable; approval refs are validated for SHAPE only and the "
          "surface name is NOT checked against the ledger's vocabulary",
          file=sys.stderr)
    return re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:[A-Za-z0-9_.:-]+$")


_APPROVAL_REF_RE = _approval_ref_re()


def _validate_approval_ref(ref: str) -> Optional[Dict[str, Any]]:
    """Return None if the ref is structurally valid, else a refusal envelope."""
    if not ref or not _APPROVAL_REF_RE.match(ref):
        return refused("invalid_approval_ref",
                       f"approval ref {ref!r} is malformed; expected "
                       "'self-eval:<id>' or 'belief:<batch>'",
                       data={"given": ref,
                             "pattern": _APPROVAL_REF_RE.pattern},
                       verb="approvals")
    return None


def verb_approvals(args) -> Dict[str, Any]:
    """The three-faced approvals surface over the voidloom_approvals backend.

    Read paths (--list / --show / --present / --tui) are open (read-only
    aggregation + presentation). Enact paths (--approve / --reject) are:
      * ref-regex validated BEFORE any backend call (cheap structural safety);
      * confirm-gated: an explicit --confirm flag OR an interactive y/N. A bare
        --approve with neither (e.g. --json or piped) REFUSES — there is no
        silent enact (#6055).
    A --comment is threaded through to the backend, where it is recorded as a
    HIGH-TRUST training signal and an audit row is written.

    Face routing:
      --present  -> Face 2: emit the VOIDWRIGHT card + the AskUserQuestion payload.
      --tui      -> Face 1: the interactive TUI (textual or ANSI fallback).
      (else)     -> Face 3: the structured headless record (the canonical source).
    """
    db = getattr(args, "db", None)
    # The approval backend reads the brain; apply the same locality auth gate.
    refusal = _guard.require_local(db)
    if refusal is not None:
        return refused(refusal["code"], refusal["message"], data=refusal,
                       verb="approvals")

    backend = _import_approvals_backend()
    if backend is None:
        return error("approvals_backend_missing",
                     "voidloom_approvals backend not importable from the brain "
                     "dir; cannot service approvals.", verb="approvals")

    as_json = bool(getattr(args, "json", False))
    comment = getattr(args, "comment", None)
    confirm_flag = bool(getattr(args, "confirm", False))

    # --- mode: the owner's approvals choice (2026-10-26) ---
    if getattr(args, "mode", None) is not None:
        try:
            import approvals_policy  # type: ignore  (beside the backend, same dir)
        except Exception as e:
            return error("approvals_policy_missing",
                         f"approvals_policy is not importable from the brain dir: {e}",
                         verb="approvals")
        if args.mode == "show":
            return ok("approvals_mode", approvals_policy.show(), verb="approvals")
        # Changing who decides is the owner's act. With no terminal (an agent, a
        # script) it needs --confirm, and the record says which it was (review 2,
        # N13): an agent asked by the owner passes --confirm; one acting alone stops.
        _tty = False
        try:
            _tty = sys.stdin.isatty()
        except Exception:
            _tty = False
        if not _tty and not confirm_flag:
            return refused("approvals_mode_needs_confirm",
                           "changing who decides the brain's proposals is the owner's choice; with no "
                           "terminal pass --confirm (an agent: only when the owner asked for it)",
                           verb="approvals")
        import getpass
        who = (os.environ.get("VOIDLOOM_ACTOR") or "").strip() or getpass.getuser() or "operator"
        by = "%s via %s" % (who, "terminal" if _tty else "no-terminal --confirm")
        before = approvals_policy.show()
        rc, msg = approvals_policy.set_mode(args.mode, set_by=by)
        if rc != 0:
            return error("approvals_mode_not_recorded", msg, verb="approvals")
        prev = before["mode"] + ("" if before.get("recorded") else " (nothing recorded)")
        return ok("approvals_mode_set", {**approvals_policy.show(), "previous": prev,
                                         "message": "%s (was %s)" % (msg, prev)},
                  verb="approvals")

    # --- snooze: suppress the calm ping for N hours (no enact) ---
    if getattr(args, "snooze", None) is not None:
        hours = float(args.snooze)
        out = backend.set_snooze(hours, db=db)
        return _wrap_backend_result(out, verb="approvals", ok_code="snooze_set")

    # --- UNIT B: decide (both directions, ONE drew_review call) ---
    # Placed BEFORE approve deliberately: --approve dispatches and RETURNS, which
    # is exactly why a mixed decision was unreachable before this verb existed.
    if getattr(args, "decide", None):
        ref = args.decide
        bad = _validate_approval_ref(ref)
        if bad is not None:
            return bad
        yes = getattr(args, "yes", None) or []
        no = getattr(args, "no_titles", None) or []
        gate = _confirm_gate("decide", ref, as_json, confirm_flag=confirm_flag)
        if gate is not None:
            return gate
        out = backend.decide(ref, approve_titles=yes, reject_titles=no,
                             reason=getattr(args, "reason", None), db=db,
                             confirm=confirm_flag, comment=comment,
                             confirmed_via=("flag" if confirm_flag
                                            else "interactive"))
        return _wrap_backend_result(out, verb="approvals",
                                    ok_code="decision_recorded")

    # --- enact: approve ---
    if getattr(args, "approve", None):
        ref = args.approve
        bad = _validate_approval_ref(ref)
        if bad is not None:
            return bad
        gate = _confirm_gate("approve", ref, as_json, confirm_flag=confirm_flag)
        if gate is not None:
            return gate
        out = backend.approve(ref, titles=getattr(args, "title", None),
                              ids=getattr(args, "ids", None), db=db,
                              confirm=confirm_flag, comment=comment,
                              confirmed_via=("flag" if confirm_flag
                                             else "interactive"))
        return _wrap_backend_result(out, verb="approvals",
                                    ok_code="approval_enacted")

    # --- enact: reject ---
    if getattr(args, "reject", None):
        ref = args.reject
        bad = _validate_approval_ref(ref)
        if bad is not None:
            return bad
        gate = _confirm_gate("reject", ref, as_json, confirm_flag=confirm_flag)
        if gate is not None:
            return gate
        out = backend.reject(ref, titles=getattr(args, "title", None),
                             ids=getattr(args, "ids", None),
                             reason=getattr(args, "reason", None), db=db,
                             comment=comment,
                             confirmed_via=("flag" if confirm_flag
                                            else "interactive"))
        return _wrap_backend_result(out, verb="approvals",
                                    ok_code="rejection_recorded")

    # --- read: show one item ---
    if getattr(args, "show", None):
        ref = args.show
        bad = _validate_approval_ref(ref)
        if bad is not None:
            return bad
        out = backend.show(ref, db=db)
        return _wrap_backend_result(out, verb="approvals", ok_code="approval_show")

    # --- Face 2: the AskUserQuestion-mediated pop-up (card + payload) ---
    if getattr(args, "present", False):
        return _verb_present(backend, db)

    # --- Face 1: the interactive TUI ---
    if getattr(args, "tui", False):
        return _verb_tui(backend, db, face=getattr(args, "face", None))

    # --- Face 3: the structured headless record (canonical, default) ---
    records = backend.structured_record(db=db)
    total, breakdown = backend.unified_count(db=db)
    ping = backend.should_ping(db=db, persist=False)
    # R3 -- THE THIRD FALSE-ZERO SURFACE, and the one Drew is told to type.
    # `backend.unified_count` is built from `list_pending`, whose three item
    # readers each `except -> []`, so an unreadable brain arrives here as a
    # confident 0 with no marker of any kind. The trust verdict is read from the
    # module that owns the distinction and attached by the shared builder.
    return _approvals_envelope(
        "approvals_list",
        {"unified_count": total, "breakdown": breakdown,
         "ping": ping, "records": records},
        _count_trust(db))


def _verb_present(backend, db) -> Dict[str, Any]:
    """Face 2: render the VOIDWRIGHT approval card + build the AskUserQuestion
    payload a session drops into the tool at runtime."""
    from . import _approvals_faces as faces
    records = backend.structured_record(db=db)
    payload = faces.build_askuserquestion_payload(records)
    card = faces.render_present(records)
    total, breakdown = backend.unified_count(db=db)
    # R3 -- SAME CLASS, SECOND FACE. `--present` renders a CARD a human reads and
    # an AskUserQuestion payload a session acts on; both said `0` on a brain
    # nobody could open. Sweeping PRODUCE alone would have missed this: it was
    # found by asking which sites RENDER the count, not which sites compute it.
    return _approvals_envelope(
        "approvals_present",
        {"unified_count": total, "breakdown": breakdown,
         "card": card, "askuserquestion": payload, "records": records},
        _count_trust(db))


def _verb_tui(backend, db, face: Optional[str] = None) -> Dict[str, Any]:
    """Face 1: the interactive TUI. Guards non-TTY (refuses, pointing at
    --present / --json). The backend callables are injected so the faces module
    never imports the backend itself."""
    from . import _approvals_faces as faces

    def _enact(ref, *, action, comment=None, confirmed_via="interactive",
               titles=None, reason=None, confirm=False, ids=None):
        """D9's actual fix. The closure passed a ref and NO TITLES, so every
        self-eval approve from the TUI hit the backend's `requires >=1 --title`
        refusal -- correctly, because there is deliberately no approve-all -- and
        the TUI then printed `tui_enacted` over the error. Titles now come from
        the record's own `units`, which carried them the whole time.

        `confirm` DEFAULTS TO FALSE, and that is the second half of the same
        edit. This closure used to hardcode confirm=True, which meant the
        L5-I-beta variance gate -- the bound on how many proposals ONE enact may
        apply -- could never fire from this surface. Threading titles in without
        this would have made the TUI a permanent bypass of it
        (voidloom_approvals.py:905 says so in as many words). The gate is now
        rendered by the face: held proposals printed, second explicit y, and
        only THEN a re-call with confirm=True. A trust bit's default is the
        refusing value (architecture-009)."""
        if action == "approve":
            return backend.approve(ref, db=db, confirm=confirm, comment=comment,
                                   confirmed_via=confirmed_via, titles=titles,
                                   ids=ids)
        return backend.reject(ref, db=db, comment=comment,
                              confirmed_via=confirmed_via, titles=titles,
                              reason=reason, ids=ids)

    res = faces.run_tui(
        _force_backend=face,
        list_pending=lambda: backend.list_pending(db=db),
        structured=lambda: backend.structured_record(db=db),
        enact=_enact,
        snooze=lambda hours: backend.set_snooze(hours, db=db),
    )
    status = res.get("status")
    if status == "refused":
        return refused(res.get("code", "no_tty"), res.get("message", ""),
                       data=res, verb="approvals")
    if status == "error":
        return error(res.get("code", "tui_error"), res.get("message", ""),
                     data=res, verb="approvals")
    # R3 -- FACE 1 WAS THE ONE THAT DID NOT INHERIT THE ADMISSION, and
    # `_approvals_envelope`'s own docstring promises that "a fourth face added
    # later inherits it". The FIRST face never got it: an unreadable brain
    # reached `_approvals_faces.run_tui`'s empty case and returned
    # code="tui_empty", status ok, exit 0 -- "the void is quiet -- nothing awaits
    # review." on the surface Drew is most likely to be looking at when he acts.
    # A promise about future call sites that the existing ones do not keep is the
    # class this round exists to end, so it is routed through the shared builder
    # like every other face.
    return _approvals_envelope(res.get("code", "tui_done"), res, _count_trust(db))


def verb_doctor(args) -> Dict[str, Any]:
    """Health check: DB reachable + local-FS-guard verdict + brain importable +
    (lazy, best-effort) model availability. Fail-graceful — always returns ok
    with a health report; individual checks carry their own pass/fail."""
    # M1: resolve the DB the SAME env-first way every other verb does, BEFORE the
    # health checks, so doctor never diverges from the rest of the CLI. Previously
    # doctor passed bare `args.db` (None when only CLAUDE_MEMORY_DB is set) into
    # detect_brain, whose fallback re-appended a hardcoded `claude_memory.db`
    # basename — reporting the WRONG path + a false-negative under the very env
    # override the sandbox workflow mandates. `_paths.resolve_db` honours
    # override > WIZARD_LIBRARY_DB > CLAUDE_MEMORY_DB > relative-self.
    db = _paths.resolve_db(getattr(args, "db", None))
    guard = _guard.check(db)

    # Engine importability (lazy, best-effort). Doctor MAY import the facade;
    # it does so without running a heavy verb, so torch still does not load.
    engine_health: Dict[str, Any]
    try:
        from . import _facade
        engine_health = _facade.engine_importable()
    except Exception as e:
        engine_health = {"importable": False, "error": str(e)}

    # Brain presence (path inspection from bootstrap — no engine import needed).
    brain = _bootstrap.detect_brain(db)

    # Model availability — best-effort, path-only (never loads torch).
    models = brain.get("models_detail", {})

    # C5 -- DESSA'S SPLIT, and Drew's ruling on the one genuine fork in this
    # cycle. There must never be ONE boolean named `healthy` meaning "the
    # database opened", because this envelope also prints hook_states,
    # hook_ladders and warnings -- and the flag read NONE of them. Measured on a
    # freshly-installed hook: `healthy: True` beside `FIRED:
    # INSTALLED_NEVER_FIRED`, `OBEYED: NEVER`, `codex: NOT_INSTALLED`.
    #
    # Two questions, two answers:
    #   brain_healthy   -- is the MACHINE sound? guard, db, engine, warnings.
    #   promise_healthy -- is the PROMISE kept?  the AND of the ladder.
    # Folding OBEYED into a single flag was the rejected option: every fresh
    # install would read "unhealthy" until a session obeys, which is
    # INSTALLED_NEVER_FIRED wearing a different word. Split, a fresh install is
    # brain_healthy TRUE / promise_healthy FALSE -- which is exactly true.
    brain_healthy = bool(guard.ok and brain.get("db_present")
                         and engine_health.get("importable"))
    # R4 as amended: the hook join — registered x fired x the platform's own
    # sessions.  A JOIN, not a belief; DEAD when sessions ran that the hook did
    # not fire in.  Fail-graceful (its own try/except per platform).
    cwd = getattr(args, "cwd", None) or os.getcwd()
    plats = getattr(args, "platform", None)
    # The organs resolve from the EXPLICIT --db (or WIZARD_LIBRARY_HOME, or the
    # DB env's dir) -- the same chain _import_self_aware uses.  Passing the
    # already-resolved `db` would make an env-resolved DB look explicit and
    # pin the organ dir to the DB's directory over WIZARD_LIBRARY_HOME.
    _explicit_db = getattr(args, "db", None)
    hooks = doctor_hook_join(_explicit_db, cwd, [plats] if plats else None,
                             probe_platform=bool(getattr(args, "probe_platform", False)))
    _plats = (hooks.get("platforms") or {})
    _warnings = [v["config_warning"] for v in _plats.values() if v.get("config_warning")]
    # N7 (Crucible D04-H3) -- A REGISTERED COMMAND THAT DOES NOT EXIST.
    #
    # `doctor` reported healthy: true, warnings: [], registered: true for a hook
    # whose command SCRIPT had been deleted. Every rung read "installed" because
    # the settings entry was there; nobody asked whether the thing it names is on
    # disk. The platform will try to execute that path at every session start and
    # fail silently. This is the cheapest possible check -- one stat per
    # registered command -- and it was missing entirely.
    _dead_commands = []
    for _pid, _pv in _plats.items():
        for _cmd in (_pv.get("ours") or []):
            _tok = next((t for t in str(_cmd).split()
                         if ("/" in t or "\\" in t) and t.rstrip('"\'').endswith((".py", ".mjs", ".js"))),
                        None)
            if _tok and not os.path.exists(_tok.strip('"\'')):
                _dead_commands.append({"platform": _pid, "command": _cmd,
                                       "missing_script": _tok.strip('"\'')})
    if _dead_commands:
        _warnings = _warnings + [
            f"{d['platform']}: the registered hook command names a script that does not "
            f"exist ({d['missing_script']}) — the platform will try to run it at every "
            f"session start and fail silently" for d in _dead_commands]
    # WIZARD_CUDA_DEVICE '-1' / 'none' -- Drew's ruling (memory anchor vl_gpu_minus1_ruling): it means NO GPU to every
    # consumer, so doctor reports it as an informational "CPU only", never a warning (iteration 5; iteration 3 warned).
    _gpu_decl = _doctor_gpu_declaration()
    brain_healthy = brain_healthy and not _warnings
    # The ladder, read rather than printed. A platform that is NOT_INSTALLED is
    # not a broken promise -- nothing was promised there -- so the AND runs over
    # the platforms that carry one of our elements. With none installed the
    # promise is UNKNOWN, and `unknown` is a first-class answer here: collapsing
    # "cannot tell" into "yes" is the class this cycle exists to end.
    _promised = {k: v for k, v in _plats.items()
                 if str(v.get("state")) not in ("NOT_INSTALLED", "", "None")}
    promise_healthy = (None if not _promised else
                       all(not any(str(r).endswith((": NEVER", "_NEVER_FIRED"))
                                   or ": DEAD" in str(r)
                                   for r in (v.get("ladder") or []))
                           for v in _promised.values()))
    report = {
        "brain_healthy": brain_healthy,
        "promise_healthy": promise_healthy,
        "checks": {
            "local_fs_guard": guard.as_dict(),
            "db_present": brain.get("db_present"),
            "db_path": brain.get("db_path"),
            "db_size_mb": brain.get("db_size_mb"),
            "engine_importable": engine_health,
            "models_present": brain.get("models_present"),
            "models_detail": models,
            # D6-F7: active_embedding_dim finally has a reader.
            "embedding_coherence": _embedding_coherence(brain.get("db_path")),
            "gpu_declaration": _gpu_decl,
            "gpu_pin": _doctor_gpu_pin(),
            # round 2, M5: the backend, cpu_only and the proof label, beside the pin
            "accelerator": _doctor_accelerator(),
            # handoff round 1 (L2): the accelerator and the reranker decision WITH its reason and proof label
            "reranker": _doctor_reranker(),
        },
        "hooks": hooks,
        "hook_states": {k: v.get("state") for k, v in (hooks.get("platforms") or {}).items()},
        "hook_ladders": {k: v.get("ladder") for k, v in (hooks.get("platforms") or {}).items()},
        "warnings": _warnings,
        "dead_hook_commands": _dead_commands,
        "codex_context_budget": doctor_codex_budget(_explicit_db),
        # model-choice unit (M14): who thinks overnight, "unreachable N of 7 nights", the credential and its expiry
        "thinker": _doctor_thinker(_explicit_db),
        # the dream fix (A6): the same dream row invariant the sentinel reads -- strangers never get the sentinel
        # timer, so doctor says BROKEN / STARVED / HEALTHY itself
        "dreams": _doctor_dreams(brain.get("db_path") or db),
        # Round 3 G3: a SKIPPED night (an organ found its brain's lock held, exit 75) is named, never read as success
        "organ_runs": _doctor_organ_runs(brain.get("db_path") or db),
        # the Persona Forge: is there a persona yet? absent/declined is PENDING, never a failure -- it does not touch
        # brain_healthy (a brain with no persona is the shipped default, not a broken machine)
        "persona": _doctor_persona(_explicit_db),
    }
    return ok("doctor_ok", report, verb="doctor")


def _preflight_script() -> str:
    """install/preflight.py beside this package (the package / brain dir that holds install/)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "install", "preflight.py")


def verb_preflight(args) -> Dict[str, Any]:
    """`voidloom preflight` (decision D1): EXEC install/preflight.py (lane L4) so the preflight owns stdout and the exit
    code (rc 0 GREEN, 1 RED, 2 could not run) -- no envelope wraps a ten-minute run. Absent script: a typed error."""
    script = _preflight_script()
    if not os.path.isfile(script):
        return error("preflight_absent", "install/preflight.py is not at %s" % script,
                     data={"script": script}, verb="preflight")
    rest = [a for a in (getattr(args, "rest", None) or []) if a != "--"]
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-B", script] + rest)
    return error("preflight_exec_returned", "os.execv returned", verb="preflight")   # unreachable on a real execv


def _doctor_persona(db: Optional[str]) -> Dict[str, Any]:
    """The persona row: forged | declared-missing-skill | declined | absent, with a verdict a reader can act on.
    Read-only, never raises; an import failure is reported in the row."""
    mod, err = _import_organ("voidloom_persona", db)
    if mod is None or not hasattr(mod, "forge_status"):
        return {"status": "unknown", "verdict": "unknown (persona organ not importable)", "error": err}
    try:
        st = mod.forge_status(_brain_dir(db))
    except Exception as e:  # forge_status never raises; belt and braces
        return {"status": "unknown", "verdict": "unknown", "error": f"{type(e).__name__}: {e}"}
    s = st.get("status")
    verdict = {"forged": "ok",
               "declared-missing-skill": "pending (not a failure): the declared persona has no SKILL.md -- "
                                         "re-run /persona-forge",
               "declined": "pending (not a failure): the reminder is off by your choice -- /persona-forge any time",
               "absent": "pending (not a failure): no persona yet -- open Claude Code and type /persona-forge",
               }.get(s, "unknown")
    return {"status": s, "verdict": verdict, "persona": st.get("persona"),
            "skill_md": st.get("skill_md"), "declaration": st.get("declaration")}


def _doctor_organ_runs(db: Optional[str], days: float = 7.0) -> Dict[str, Any]:
    """The run ledger's SKIPPED runs in the last week (organ_run_log.skipped_runs, read-only), with a line. Every organ
    that can find its lock held exits 75 on that skip and the ledger files it "skipped-lock"; before Round 3 the
    sleep loop's skip exited 0 and the ledger recorded "success". Fail-graceful: an unreadable ledger is SAID."""
    if not db or not os.path.exists(str(db)):
        return {"skips": None, "line": "organ runs: UNMEASURABLE -- no brain at %s" % db}
    orl, err = _import_organ("organ_run_log", db)
    if orl is None:
        return {"skips": None, "line": "organ runs: UNMEASURABLE -- organs/organ_run_log.py is not importable (%s)" % err}
    try:
        import sqlite3 as _sq
        conn = _sq.connect("file:%s?mode=ro" % db, uri=True)
        try:
            rows = orl.skipped_runs(conn, days=days)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - doctor never crashes on one check
        return {"skips": None, "line": "organ runs: UNMEASURABLE -- the run ledger could not be read (%s: %s)"
                % (type(e).__name__, e)}
    if not rows:
        return {"skips": [], "line": "organ runs: no SKIPPED run in the last %d days" % days}
    per: Dict[str, int] = {}
    for r in rows:
        per[r["service_name"] or r["unit_name"]] = per.get(r["service_name"] or r["unit_name"], 0) + 1
    newest = rows[0]
    return {"skips": rows, "line": "organ runs: %d SKIPPED run%s in the last %d days (%s; newest %s at %s) -- %s" % (
        len(rows), "" if len(rows) == 1 else "s", days, ", ".join("%s x%d" % kv for kv in sorted(per.items())),
        newest["service_name"] or newest["unit_name"], newest["run_started_at"], orl.SKIP_NOTE)}


def _doctor_dreams(db: Optional[str]) -> Dict[str, Any]:
    """dream_ledger.dream_watch on the brain, read-only. Fail-graceful: UNMEASURABLE with the reason."""
    if not db or not os.path.exists(str(db)):
        return {"verdict": "UNMEASURABLE", "detail": "no brain at %s" % db}
    dl, err = _import_organ("dream_ledger", db)
    if dl is None:
        return {"verdict": "UNMEASURABLE", "detail": "organs/dream_ledger.py is not importable (%s)" % err}
    try:
        import sqlite3 as _sq
        conn = _sq.connect("file:%s?mode=ro" % db, uri=True)
        try:
            w = dl.dream_watch(conn)
        finally:
            conn.close()
        s7 = w.get("surfacing_7d") or {}
        w["line"] = "dreams: %s -- %s%s; 7d: shown on %s of %s recalls, corpus %s dreams / %s memories" % (
            w.get("verdict"), w.get("detail", ""), ("; " + w["met_detail"]) if w.get("met_warning") else "",
            s7.get("recalls_showing_a_dream"), s7.get("recalls_with_results"), s7.get("dream_corpus"),
            s7.get("authored_memories"))
        return w
    except Exception as e:  # noqa: BLE001 - doctor never crashes on one check
        return {"verdict": "UNMEASURABLE", "detail": "%s: %s" % (type(e).__name__, e)}


def _doctor_thinker(db: Optional[str]) -> Dict[str, Any]:
    """The thinker line (organs/thinker.py health_line) plus the facts it summarises. Fail-graceful: a missing or
    broken seam is itself the line."""
    th, err = _import_organ("thinker", db)
    if th is None:
        return {"line": "thinker: organs/thinker.py is not importable (%s)" % err}
    try:
        rep = th.status_report()
        return {"line": th.health_line(rep), "backend": rep.get("backend"), "proven_at": rep.get("proven_at"),
                "nights": rep.get("nights"), "credential": rep.get("credential"),
                "record_error": rep.get("record_error"),
                "env_file_carries_claude_token": rep.get("env_file_carries_claude_token"),
                # MC-R1-04 (Round 1 iteration 2): the doctor NAMES the env-file token line (file + line) and says it
                # can go -- the same words as `thinker status` and the installer's DONE; the owner deletes it
                "token_line": th.token_line_advice(rep)}
    except Exception as e:  # noqa: BLE001
        return {"line": "thinker: status could not be read (%s: %s)" % (type(e).__name__, e)}


# ---------------------------------------------------------------------------
# ORGAN VERBS — mechanisms are verbs, platforms are parameters (design A3).
# Each organ exposes run(argv) -> envelope; the CLI passes the verb's own argv
# through UNPARSED (the organ owns its grammar), appends --json / --db when the
# top-level flags were given, and lets _emit print once.  Stdlib-only imports
# (the organs are stdlib-pure; none imports torch), so R2 holds.
# ---------------------------------------------------------------------------
ORGAN_VERBS: Dict[str, Dict[str, Any]] = {
    "hooks": {"module": "voidloom_hooks_status", "accepts_db": False},
    "instructions": {"module": "voidloom_instructions", "accepts_db": False},
    "preamble": {"module": "voidloom_preamble", "accepts_db": True},
    "canary": {"module": "voidloom_canary", "accepts_db": True},
    # model-choice unit, Round 1: WHO thinks overnight -- status | set | login | probe | check | migrate.
    # The organ owns its grammar; the brain it acts on is the one --db names (WIZARD_LIBRARY_DB), see _run_organ.
    "thinker": {"module": "thinker", "accepts_db": False},
    # the dream fix (Tranche A): backfill --plan|--apply|--rollback, confirm, show, watch.
    "dream": {"module": "dream_ledger", "accepts_db": True},
    # the Persona Forge: is there a persona yet? status | forge | declare | decline [--undo]. Stdlib, never raises.
    "persona": {"module": "voidloom_persona", "accepts_db": False},
}


def _import_organ(module_name: str, db: Optional[str] = None):
    """Import organs/<module_name>.py from the brain dir (the same resolution
    _import_self_aware uses).  Returns (module, error)."""
    from pathlib import Path as _P
    _root = _P(_brain_dir(db))
    _organs = _root / "organs"
    brain_dir = str(_organs if _organs.is_dir() else _root)
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    try:
        import importlib
        return importlib.import_module(module_name), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _run_organ(verb: str, args) -> Dict[str, Any]:
    spec = ORGAN_VERBS[verb]
    mod, err = _import_organ(spec["module"], getattr(args, "db", None))
    if mod is None or not hasattr(mod, "run"):
        return error("organ_missing",
                     f"organs/{spec['module']}.py is not importable from the brain dir "
                     f"or has no run(argv)",
                     data={"brain_dir": _brain_dir(getattr(args, "db", None)),
                           "error": err}, verb=verb)
    argv = list(getattr(args, "organ_args", None) or [])
    if getattr(args, "json", False) and "--json" not in argv:
        argv.append("--json")
    if spec["accepts_db"] and getattr(args, "db", None) and "--db" not in argv:
        argv += ["--db", args.db]
    env = mod.run(argv)
    if not isinstance(env, dict) or "status" not in env:
        return error("organ_bad_envelope",
                     f"organs/{spec['module']}.run() did not return an envelope",
                     verb=verb)
    env.setdefault("verb", verb)
    return env


def verb_hooks(args) -> Dict[str, Any]:
    return _run_organ("hooks", args)


def verb_instructions(args) -> Dict[str, Any]:
    return _run_organ("instructions", args)


def verb_preamble(args) -> Dict[str, Any]:
    return _run_organ("preamble", args)


def verb_canary(args) -> Dict[str, Any]:
    return _run_organ("canary", args)


def verb_dream(args) -> Dict[str, Any]:
    return _run_organ("dream", args)


def verb_persona(args) -> Dict[str, Any]:
    return _run_organ("persona", args)


def verb_thinker(args) -> Dict[str, Any]:
    # the thinker resolves its brain from WIZARD_LIBRARY_DB (else the tree it was laid in); a --db given to the
    # CLI names that brain for this one call
    db = getattr(args, "db", None)
    if db:
        os.environ["WIZARD_LIBRARY_DB"] = os.path.abspath(os.path.expanduser(db))
    return _run_organ("thinker", args)


def doctor_hook_join(db: Optional[str], cwd: str,
                     platforms: Optional[Sequence[str]] = None,
                     probe_platform: bool = False) -> Dict[str, Any]:
    """R4 as amended: per platform, JOIN {registered in the platform's config}
    x {heartbeat fires} x {the platform's OWN session records} and report
    NOT_INSTALLED / INSTALLED_NEVER_FIRED / DEAD (n sessions since) / LIVE.
    On Codex the registration + trust come from the app-server's hooks/list
    (zero model calls), never inferred from silence.  Fail-graceful: any
    organ import failure is reported in the row, never raised."""
    out: Dict[str, Any] = {"platforms": {}, "sensor": "heartbeat.session_id JOIN platform "
                                                      "session records (not the clock)",
                           "platform_probe": {"consented": bool(probe_platform),
                                              "note": ("codex app-server spawned (vendor writes "
                                                       "under $CODEX_HOME)" if probe_platform else
                                                       "no vendor process spawned; --probe-platform "
                                                       "consents to the live hooks/list view")}}
    hb, e1 = _import_organ("voidloom_heartbeat", db)
    hs, e2 = _import_organ("voidloom_hooks_status", db)
    if hb is None or hs is None:
        out["error"] = {"voidloom_heartbeat": e1, "voidloom_hooks_status": e2}
        return out
    for pid in (platforms or ("claude-code", "codex")):
        try:
            if pid == "claude-code":
                probe = hs.probe_claude_settings(
                    ["{claude_config}/settings.json", "{claude_config}/settings.local.json",
                     ".claude/settings.json", ".claude/settings.local.json"], cwd)
                registered = bool(probe.get("installed"))
                evidence = {"resolver": "settings chain", "ours": probe.get("ours"),
                            "sources": [{"path": s["path"], "exists": s["exists"],
                                         "our_element_indices": s.get("our_element_indices")}
                                        for s in probe.get("sources") or []]}
            else:
                probe = hs.probe_codex_hooks(["{codex_home}/hooks.json",
                                              "{codex_home}/config.toml"], cwd,
                                             probe_platform=probe_platform)
                registered = bool(probe.get("installed"))
                pv = probe.get("platform_view") or {}
                evidence = {"resolver": probe.get("installed_derived_from"),
                            "hooks_json": probe.get("hooks_json", {}).get("path"),
                            "hooks_json_exists": probe.get("hooks_json", {}).get("exists"),
                            "platform_view_queried": pv.get("queried"),
                            "trust_status": probe.get("trust_status"),
                            "trust_derived_from": probe.get("trust_derived_from"),
                            "our_keys": (pv.get("our_keys") if pv.get("queried")
                                         else (probe.get("hooks_json") or {}).get("our_keys_by_schema")),
                            "additional_context_limit": pv.get("our_additional_context_limit"),
                            "trust_key_note": (probe.get("config_toml") or {}).get("trust_key_note")}
            beats = hb.read_heartbeats(pid)
            sessions = hb.list_platform_sessions(pid)
            row = hb.join_state(registered=registered, heartbeats=beats,
                                sessions=sessions, platform=pid,
                                registration_evidence=evidence)
            # THE OTHER SENSOR'S RUNG (Council #12): doctor's verdict is the
            # heartbeat join (FIRED); the canary ledger (`hooks status`) holds
            # PRESENT / PROVEN / OBEYED.  One ladder on both verbs, each naming
            # the sensor, so LIVE beside NEVER reads as "fired, never witnessed".
            try:
                pv_ = hs.ledger_view(hs.ledger_path(), pid)
                row["proof"] = {k: (pv_.get(k) or {}).get("state")
                                for k in ("last_present", "last_proven",
                                          "last_obeyed", "last_uncontradicted")}
                row["proof"]["sensor"] = "canary ledger (hooks status' sensor)"
            except Exception as e:                   # never break doctor
                row["proof"] = {"error": f"{type(e).__name__}: {e}"}
            # CRUCIBLE-3 D01-F5, the third part: THE LADDER WAS NOT MONOTONIC.
            # It rendered ["FIRED: NOT_INSTALLED", "PRESENT: NEVER",
            # "PROVEN: FRESH", "OBEYED: FRESH"] -- a hook that is not installed
            # and has never been PRESENT was nonetheless PROVEN and OBEYED. G0 is
            # the rung beneath G1/G2 and its failure did not demote them, so the
            # strongest claim on the ladder was the one with the least support.
            #
            # The rungs are not independent readings: PROVEN is a strictly
            # stronger claim than PRESENT, so it cannot hold where PRESENT does
            # not. The stored states are NOT rewritten -- the ledger keeps what it
            # observed -- but the rendered rung says when a higher claim stands on
            # a lower one that is absent, which is the surface the defect was on.
            _pf = row.get("proof") or {}
            _present_ok = (_pf.get("last_present") == "FRESH")

            def _rung(label: str, state: Any, needs_present: bool) -> str:
                if needs_present and not _present_ok and state == "FRESH":
                    return (f"{label}: {state} (UNSUPPORTED — PRESENT is "
                            f"{_pf.get('last_present')})")
                return f"{label}: {state}"

            row["ladder"] = [f"FIRED: {row.get('state')}",
                             _rung("PRESENT", _pf.get("last_present"), False),
                             _rung("PROVEN", _pf.get("last_proven"), True),
                             _rung("OBEYED", _pf.get("last_obeyed"), True)]
            # W7 (Council #12): a sibling settings file that fails to parse is
            # SKIPPED SILENTLY by Claude Code; `hooks status` logged it and
            # doctor said nothing.  Every config source is now a row here.
            if pid == "claude-code":
                cfg = []
                for src in probe.get("sources") or []:
                    ent = {"path": src.get("path"), "exists": bool(src.get("exists"))}
                    if src.get("exists"):
                        try:
                            ent["bytes"] = os.path.getsize(src["path"])
                        except OSError:
                            ent["bytes"] = None
                        if src.get("error"):
                            ent["parse"] = "NOT_JSON"
                            ent["error"] = src.get("error")
                            ent["note"] = ("Claude Code skips an unparseable settings file "
                                           "SILENTLY — every hook and permission in it is dark")
                        else:
                            ent["parse"] = "ok"
                            if src.get("note"):
                                ent["note"] = src.get("note")
                    cfg.append(ent)
                row["config_files"] = cfg
                bad = [c["path"] for c in cfg if c.get("parse") == "NOT_JSON"]
                if bad:
                    row["config_warning"] = f"{len(bad)} settings file(s) do not parse: {bad}"
            if pid == "codex" and registered and probe.get("trust_status") in ("untrusted", "modified"):
                row["trust_status"] = probe.get("trust_status")
                row["note"] = (f"the platform reports the hook as {probe.get('trust_status')}: "
                               f"it will be SKIPPED SILENTLY until trusted")
            out["platforms"][pid] = row
        except Exception as e:                       # never break doctor
            out["platforms"][pid] = {"platform": pid, "state": "UNKNOWN",
                                     "error": f"{type(e).__name__}: {e}"}
    return out


def doctor_codex_budget(db: Optional[str]) -> Dict[str, Any]:
    """The preamble's size against Codex's spill ceiling, DERIVED: the hook's
    Codex budget (read from the hook), the installed additionalContextLimit
    (from the join), and the emitter's current token count."""
    res: Dict[str, Any] = {}
    try:
        from pathlib import Path as _P
        hook = _P(_brain_dir(db)) / "hooks" / "session_start_inject.py"
        src_ = hook.read_text(encoding="utf-8") if hook.exists() else ""
        m = re.search(r"^CODEX_PREAMBLE_BUDGET\s*=\s*(\d+)", src_, flags=re.M)
        res["hook_codex_budget_tokens"] = int(m.group(1)) if m else None
        res["hook_path"] = str(hook)
    except Exception as e:
        res["hook_codex_budget_tokens"] = None
        res["error"] = f"{type(e).__name__}: {e}"
    hs, _ = _import_organ("voidloom_hooks_status", db)
    res["install_additional_context_limit"] = (getattr(hs, "CODEX_ADDITIONAL_CONTEXT_LIMIT", None)
                                               if hs else None)
    res["vendor_default_when_null"] = 2500
    b, l = res.get("hook_codex_budget_tokens"), res.get("install_additional_context_limit")
    res["budget_below_limit"] = (b is not None and l is not None and b < l)
    return res


# ---------------------------------------------------------------------------
# Helpers: confirm-gate, engine/backend import, result wrapping.
# ---------------------------------------------------------------------------


def _confirm_gate(action: str, ref: str, as_json: bool,
                  confirm_flag: bool = False) -> Optional[Dict[str, Any]]:
    """Confirmation gate for an enact. Returns None to PROCEED, or a refusal
    envelope to BLOCK. The enact path is the dangerous surface (#6055) — there is
    NO silent --approve.

    Two valid ways to confirm a state change:
      * EXPLICIT --confirm flag (the headless confirmation): an operator who
        passes --confirm has affirmed the change; proceed. This is what lets a
        scripted / --json enact run — but ONLY with the explicit affirmation.
      * INTERACTIVE y/N: when stdin is a TTY and --confirm was not given, prompt;
        proceed only on y/yes.

    REFUSES (no enact) when:
      * neither --confirm nor an interactive TTY confirmation is available
        (e.g. --json or a piped stdin WITHOUT --confirm), OR
      * the interactive operator answers anything other than y/yes.
    A bare `--approve REF` with no --confirm and no TTY -> REFUSED.
    """
    # The explicit headless affirmation. An operator who typed --confirm has
    # confirmed; this is the sanctioned non-interactive path.
    if confirm_flag:
        return None

    # No --confirm: require an interactive TTY to confirm. --json (machine
    # context) or a non-TTY stdin without --confirm is refused.
    if as_json or not sys.stdin or not sys.stdin.isatty():
        return refused("confirm_required_no_tty",
                       f"refusing to {action} {ref!r}: enact actions require "
                       "confirmation. Pass --confirm for a non-interactive "
                       "affirmation, or re-run from a terminal (without --json) "
                       "for an interactive y/N.",
                       data={"ref": ref, "action": action}, verb="approvals")
    prompt = (f"voidloom: {action} {ref}? This enacts a state change. "
              "Type 'y' to confirm [y/N]: ")
    try:
        answer = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes"):
        return refused("confirm_declined",
                       f"{action} of {ref!r} not confirmed; no action taken.",
                       data={"ref": ref, "action": action}, verb="approvals")
    return None


def _import_approvals_backend():
    """Lazily import the existing voidloom_approvals backend from the brain dir.
    Returns the module or None (never raises). The brain dir is resolved through
    the single package resolver (``_paths.brain_dir``) so it can never diverge
    from where the facade/guard open the DB."""
    brain_dir = str(_paths.organ_dir())
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    try:
        import voidloom_approvals  # type: ignore
        return voidloom_approvals
    except Exception:
        return None


#: The three states of a rendered count. `unknown` is the one this file existed
#: for eleven weeks without being able to say.
COUNT_READ = "read"                  # every source was actually read
COUNT_UNKNOWN = "unknown"            # a source refused: I COULD NOT TELL
COUNT_UNVERIFIABLE = "unverifiable"  # this tree's banner cannot report trust
COUNT_PARTIAL = "partial"            # SOME sources answered; the count is a
#                                    # LOWER BOUND and the unread ones are named


def _all_sources_dark(unread, breakdown=None) -> bool:
    """True only when EVERY source went dark. R7/C2: the difference between a
    partial answer and a total loss is the whole of this criterion, and it was
    a difference no CLI face could express.

    R7/C5. No literal key set: with no breakdown in hand this returns False,
    and [D4] rules that the doubt resolves toward publishing the count.
    """
    _un = set(str(x) for x in (unread or ()))
    _keys = set(breakdown or ())
    return bool(_keys) and _un >= _keys


def _import_banner():
    """Lazily import ``voidloom_banner`` from the brain dir, or None.

    R3 -- WHY THE TRUST BIT IS BORROWED AND NOT RE-DERIVED HERE. R10 forbids a
    SECOND spelling of a query the ledger already owns, and the historical
    self-eval / belief contract plus the ``(count, trusted)`` distinction live
    exactly once, in ``voidloom_banner``. Re-opening the brain here to decide
    whether the brain is readable would be a fourth spelling of the question and
    could disagree with the banner Drew read thirty seconds earlier -- which is
    the class of drift this whole tier exists to end. Same resolver as
    ``_import_approvals_backend`` so the two can never point at different trees.
    """
    brain_dir = str(_paths.organ_dir())
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    try:
        import voidloom_banner  # type: ignore
        return voidloom_banner
    except Exception:
        return None


def _count_trust(db: Optional[str]) -> Dict[str, Any]:
    """``{"count_trust": ..., "refusal": {...}|None}`` for an approvals envelope.

    THE DEFECT THIS CLOSES, measured 2026-09-03 on a copy of the live tree:
    ``voidloom approvals`` against an UNREADABLE brain and against a genuinely
    EMPTY one emitted the SAME envelope --
    ``{"status":"ok","unified_count":0,"breakdown":{...},"ping":...,"records":[]}``
    -- exit 0 in both cases. Three distinct world-states rendered as one output,
    on the verb the banner itself tells Drew to run. A false zero is not a
    display bug here: it is the surface asserting a decided state it never
    earned.

    NO COMPATIBILITY SHIM, AND NO FALSE ALARM EITHER. A banner too old to report
    trust yields ``unverifiable`` -- stated, never silently upgraded to `read`
    (which would re-hide the defect) and never downgraded to `unknown` (which
    would cry wolf on a healthy brain). That third value is the same admission
    ``hooks/voidloom_notify.py`` already prints when it meets an older banner:
    "the count's trustworthiness is ASSUMED, not read".
    """
    banner = _import_banner()
    if banner is None:
        return {"count_trust": COUNT_UNVERIFIABLE, "refusal": None,
                "count_trust_reason": "voidloom_banner is not importable from "
                                      "the brain dir, so the count's "
                                      "trustworthiness was not read"}
    fn = getattr(banner, "refusal", None)
    if fn is None:
        return {"count_trust": COUNT_UNVERIFIABLE, "refusal": None,
                "count_trust_reason": "this voidloom_banner has no refusal() "
                                      "reader; the count's trustworthiness is "
                                      "ASSUMED, not read"}
    try:
        ref = fn(db)
    except Exception as e:  # a trust reader that raises is itself an unknown
        return {"count_trust": COUNT_UNKNOWN,
                "refusal": {"unknown": True, "count_is_not_zero": True,
                            "reason": f"voidloom_banner.refusal raised "
                                      f"{type(e).__name__}",
                            "text": "I could not tell -- the pending count is "
                                    "UNKNOWN, not zero"},
                "count_trust_reason": f"{type(e).__name__} from the trust reader"}
    if ref is None:
        return {"count_trust": COUNT_READ, "refusal": None,
                "count_trust_reason": None, "unreadable_sources": []}
    # R7/C2. `refusal()` already names WHICH sources went dark; this builder
    # dropped that list, so `_approvals_envelope` below had no way to tell a
    # partial from a total loss and flipped every one of the CLI's three faces
    # to a bare `[refused] I could not tell`.
    _un = list(ref.get("unreadable_sources") or [])
    # THE BREAKDOWN IS FETCHED, NOT ASSUMED. `refusal()` names the holes but not
    # the shape; `unified_pending_report` is the one reader that carries both,
    # and reaching for it here is what lets `_all_sources_dark` be written with
    # no key set of its own.
    _bd = None
    _rep_fn = getattr(banner, "unified_pending_report", None)
    if _rep_fn is not None:
        try:
            _bd = (_rep_fn(db) or {}).get("breakdown")
        except Exception:
            _bd = None
    _partial = bool(_un) and not _all_sources_dark(_un, _bd)
    return {"count_trust": COUNT_PARTIAL if _partial else COUNT_UNKNOWN,
            "refusal": ref, "unreadable_sources": _un,
            "count_trust_reason": ref.get("reason")}


def _approvals_envelope(code: str, data: Dict[str, Any],
                        trust: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the trust verdict to an approvals envelope, ONE way, for every face.

    A renderer that names its output keys IS a consumer, and this build already
    lost the bit at three of them. So the keys are added HERE rather than at each
    call site: a fourth face added later inherits the admission instead of
    quietly re-collapsing it.

    ``status`` becomes ``refused`` (exit 3) only for ``unknown``. That is not a
    cosmetic choice: ``status: "ok"`` beside ``unified_count: 0`` on a brain that
    could not be opened is the exact sentence the surface had no right to say,
    and the exit code is the machine-readable half of it. Every historical key
    is preserved unchanged in ``data`` so nothing that reads them breaks.
    """
    data = dict(data)
    data["count_trust"] = trust["count_trust"]
    if trust.get("count_trust_reason"):
        data["count_trust_reason"] = trust["count_trust_reason"]
    ref = trust.get("refusal") or {}
    _un = list(trust.get("unreadable_sources") or [])
    if _un:
        data["unreadable_sources"] = _un
    if trust["count_trust"] == COUNT_PARTIAL:
        # R7/C2 + [D4]. SOME source answered, so the count stands as a LOWER
        # BOUND and is published as one. The status stays `refused` (exit 3) --
        # the total genuinely is not final, and a machine consumer must not read
        # it as settled -- but the payload keeps `unified_count`, `breakdown`
        # and `records`, and the human render below prints them. That is the
        # difference between saying "I could not tell" and saying "I could tell
        # you this much, and here is what I could not reach".
        data["unknown"] = True
        data["count_is_not_zero"] = True
        data["refusal"] = ref
        data["message"] = ("partial: " + ", ".join(_un) + " UNREAD -- the "
                           "count below is a LOWER BOUND")
        return _envelope(STATUS_REFUSED, "approvals_count_partial", data,
                         "approvals")
    if trust["count_trust"] != COUNT_UNKNOWN:
        return ok(code, data, verb="approvals")
    data["unknown"] = True
    data["refusal"] = ref
    data["message"] = ref.get("text") or ("I could not tell -- the pending "
                                          "count is UNKNOWN, not zero")
    return _envelope(STATUS_REFUSED, "approvals_count_unknown", data,
                     "approvals")


def _brain_dir(db: Optional[str]) -> str:
    """Resolve the brain directory (where the manifest + brain-root modules
    live). Order: explicit --db's dir > WIZARD_LIBRARY_HOME > the DB-env's dir >
    the relative-self default's dir.

    Uses ``os.path.dirname(os.path.abspath(...))`` rather than ``Path.resolve()``
    so a symlinked DB is NOT dereferenced back to its target directory (the
    manifest is expected to sit beside the configured DB path, not beside the
    symlink's target — this also avoids the staging-symlink class of surprise).

    The final fallback comes from the single package resolver
    (``_paths.default_db``) — relative-self, so this resolves correctly wherever
    the package is unpacked, with no path hardcoded in source."""
    if db:
        return os.path.dirname(os.path.abspath(db))
    home = os.environ.get("WIZARD_LIBRARY_HOME")
    if home:
        return os.path.abspath(home)
    path = (os.environ.get("WIZARD_LIBRARY_DB")
            or os.environ.get("CLAUDE_MEMORY_DB")
            or _paths.default_db())
    return os.path.dirname(os.path.abspath(path))


def _manifest_path(db: Optional[str]) -> str:
    """The voidloom_manifest.json path, resolved relative to the brain dir."""
    from pathlib import Path
    return str(Path(_brain_dir(db)) / "voidloom_manifest.json")


def _import_self_aware(db: Optional[str] = None):
    """Lazily import the _self_aware projection module from the brain dir
    (L5-M3). Returns the module or None (never raises). Stdlib-only module."""
    from pathlib import Path as _P
    _root = _P(_brain_dir(db))
    _organs = _root / "organs"
    brain_dir = str(_organs if _organs.is_dir() else _root)
    if brain_dir not in sys.path:
        sys.path.insert(0, brain_dir)
    try:
        import _self_aware  # type: ignore
        return _self_aware
    except Exception:
        return None


def _wrap_engine_result(res: Dict[str, Any], *, verb: str,
                        ok_code: str) -> Dict[str, Any]:
    """Map an engine result dict (``{status: ok|error, action, ...}``) into a
    Voidloom envelope."""
    if not isinstance(res, dict):
        return error("engine_bad_result",
                     "engine returned a non-dict result", data=res, verb=verb)
    status = res.get("status")
    if status == "ok":
        return ok(ok_code, res, verb=verb)
    # Engine error (printed by _error / a failure path).
    msg = res.get("message") or res.get("error") or "engine reported an error"
    return error("engine_error", str(msg), data=res, verb=verb)


_APPROVAL_WORK_KEYS = ("enacted", "failed", "blocked", "skipped")


def _approvals_work(res: Dict[str, Any]) -> Optional[Dict[str, list]]:
    """The WORK a backend result reports, or ``None`` if it reports none.

    Producers put it in two places: at the TOP LEVEL for belief and governance,
    and nested under ``enact`` for self-eval. Returning ``None`` when no work key
    is present at all is deliberate and is the narrow escape hatch: a verb that
    did no enacting (a listing, a recorded decision) has no list to read, and
    turning its silence into a failure would invent a defect.
    """
    for src in (res, res.get("enact") if isinstance(res.get("enact"), dict) else None):
        if isinstance(src, dict) and any(k in src for k in _APPROVAL_WORK_KEYS):
            return {k: list(src.get(k) or []) for k in _APPROVAL_WORK_KEYS}
    return None


def _wrap_backend_result(res: Dict[str, Any], *, verb: str,
                         ok_code: str) -> Dict[str, Any]:
    """Map a voidloom_approvals backend result into a Voidloom envelope.

    CRUCIBLE-3 D04-F1 (LIES): this read ``res["status"]`` and nothing else, so

        voidloom approvals --approve belief:1 --ids 999999 --confirm --json

    returned ``status: "ok"``, ``code: "approval_enacted"``, EXIT 0 -- directly
    above ``"enacted": []`` and ``"failed": [{"error": "proposal not found"}]``.
    The human render is worse, because it is the line an operator reads:
    ``[ok] voidloom approvals (approval_enacted)``. Blast radius is every
    non-TTY enact -- ``--json``, scripts, any ``&& next-step`` -- plus the
    persisted ``approval_audit_log`` safety trail.

    The rule this applies is already in the brain, from the 2026-09-08
    measurement of the same shape one layer in (``vl_ok_is_not_a_verdict``):
    **a status field is not the work, it is a claim ABOUT the work made by its
    author. When a result carries both a status and a list of outcomes, the list
    is the evidence and the status is testimony. Read the list.**
    """
    if not isinstance(res, dict):
        return error("approvals_bad_result",
                     "approvals backend returned a non-dict", data=res, verb=verb)
    status = res.get("status")
    if status != "ok":
        msg = res.get("message") or "approvals backend reported an error"
        return error("approvals_error", str(msg), data=res, verb=verb)

    # The backend says ok. Now read what it actually did.
    work = _approvals_work(res)
    if work is None:                       # no work list to read; nothing to derive
        return ok(ok_code, res, verb=verb)

    n_ok = len(work["enacted"])
    n_bad = len(work["failed"]) + len(work["blocked"])
    n_skip = len(work["skipped"])
    derived = {"enacted": n_ok, "failed": len(work["failed"]),
               "blocked": len(work["blocked"]), "skipped": n_skip,
               "note": ("the verdict below is DERIVED from these counts, not from "
                        "the backend's own status field")}
    if n_bad:
        return error("approvals_enact_incomplete",
                     f"{n_ok} enacted, {len(work['failed'])} failed, "
                     f"{len(work['blocked'])} blocked"
                     + (f", {n_skip} skipped" if n_skip else "")
                     + " — the backend reported status ok over this; the counts are "
                       "the evidence. Nothing below was enacted that is not in "
                       "`enacted`.",
                     data={**res, "derived_verdict": derived}, verb=verb)
    if n_ok == 0 and n_skip == 0:
        return error("approvals_enacted_nothing",
                     "the backend reported status ok and enacted nothing at all "
                     "— no item succeeded, failed, was blocked or was skipped, so "
                     "there is no work this verdict could be about.",
                     data={**res, "derived_verdict": derived}, verb=verb)
    return ok(ok_code, {**res, "derived_verdict": derived}, verb=verb)


# ---------------------------------------------------------------------------
# Argparse wiring. The v0.1 surface — and ONLY this surface.
# ---------------------------------------------------------------------------


def _add_global_flags(sp: argparse.ArgumentParser) -> None:
    """Mirror the top-level ``--db`` + ``--quiet`` onto a verb subparser (M4 +
    H3). argparse only matches a top-level optional BEFORE the subcommand, so the
    natural ``voidloom doctor --db X`` / ``voidloom recall --quiet`` post-verb
    forms otherwise exit 2 with a bare ``unrecognized arguments``. Declaring them
    on each subparser too (``default=argparse.SUPPRESS``) makes BOTH positions
    work: a value given post-verb lands on the same namespace attr; when absent,
    SUPPRESS leaves the top-level parser's value (or its default) intact rather
    than clobbering it with None/False."""
    sp.add_argument("--db", default=argparse.SUPPRESS,
                    help="brain DB path override (also valid before the verb).")
    sp.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                    help="force full stderr silence (suppress model-load logs).")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="voidloom",
        description="The LLM's execution surface over the Voidloom memory brain "
                    "(a thin facade over claude_memory.py).")
    p.add_argument("--version", action="version",
                   version=f"voidloom {__version__}")
    p.add_argument("--db", default=None,
                   help="override the brain DB path (advanced; must precede the "
                        "verb, e.g. `voidloom --db PATH recall ...`; also "
                        "accepted after the verb. Default resolves from "
                        "WIZARD_LIBRARY_DB / CLAUDE_MEMORY_DB / relative-self).")
    # H3 quiet: force FULL stderr silence for this invocation (suppresses the
    # model-load INFO/WARNING + the SentenceTransformers version warning the
    # engine emits to stderr on the first recall/store). The stdout envelope is
    # untouched, so `voidloom recall --quiet --json | jq` is byte-clean. Declared
    # on the top-level parser so it is honoured wherever it appears (before the
    # verb); also mirrored onto each verb subparser so the natural post-verb
    # position works too (see `_add_global_flags`).
    p.add_argument("--quiet", action="store_true", default=False,
                   help="force full stderr silence (suppress model-load logs).")
    sub = p.add_subparsers(dest="command", metavar="<verb>")

    # bootstrap
    sp = sub.add_parser("bootstrap",
                        help="stdlib-only host detect (OS/WSL2, GPU, brain).")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_bootstrap)

    # agent-context
    sp = sub.add_parser("agent-context",
                        help="emit the self-describing toolkit manifest.")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_agent_context)

    # capabilities — the FULL prose+examples projection of the L5 organ
    # registry (the same manifest the session-start hook reads compactly).
    sp = sub.add_parser("capabilities",
                        help="full prose+examples view of the brain's L5 organs.")
    sp.add_argument("--available-only", action="store_true",
                    help="only organs available on this host's tier "
                         "(default lists all, marking availability)")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_capabilities)

    # doctor
    sp = sub.add_parser("doctor",
                        help="health: DB, local-FS guard, engine, models, and the "
                             "hook join (NOT_INSTALLED / INSTALLED_NEVER_FIRED / "
                             "DEAD / LIVE per platform).")
    sp.add_argument("--platform", default=None, choices=["claude-code", "codex"],
                    help="restrict the hook join to one platform")
    sp.add_argument("--cwd", default=None,
                    help="cwd for the platform probes (default: the process cwd)")
    sp.add_argument("--probe-platform", action="store_true",
                    help="CONSENT to spawn `codex app-server` for the live hooks/list view; "
                         "the vendor writes its own state under $CODEX_HOME when spawned. "
                         "Without it doctor derives Codex registration from the hook schema "
                         "and trust from Codex's persisted record, and writes nothing.")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_doctor)

    # preflight -- decision D1 (handoff round 1): prove THIS machine before a package is cut for it. A pass-through to
    # install/preflight.py (lane L4), which installs nothing into the home and writes preflight.json to send back.
    # add_help=False and no global flags: every token after the verb, `--help` and `--out x.json` included, belongs to
    # the preflight (dispatch() hands it the unparsed extras, as it does for the organ verbs).
    sp = sub.add_parser("preflight", add_help=False,
                        help="prove this machine (venv, torch, sqlite-vec, golden vectors on its accelerator) in a "
                             "scratch folder that is then deleted; writes preflight.json. Installs nothing.")
    sp.add_argument("rest", nargs=argparse.REMAINDER,
                    help="passed to install/preflight.py (--out, --python, --tier, --keep, --use-python, --help)")
    sp.set_defaults(_fn=verb_preflight)

    # THE ORGAN VERBS.  Their own argv passes through unparsed (REMAINDER);
    # unknown leading options are collected by parse_known_args in dispatch().
    for _verb, _help in (
            ("hooks", "the SessionStart hook: install --platform {claude,codex} "
                      "[--dry-run|--yes] | status | uninstall [--yes] | --self-check"),
            ("instructions", "the activation notice in the instruction files: "
                             "install [--dry-run|--yes] | status | uninstall [--yes]"),
            ("preamble", "THE single emitter of the injected session preamble "
                         "(--json, --budget, --sections, --selftest)"),
            ("canary", "G0/G1/G2/G3 proof harness, control first (--gate, --runner, "
                       "--self-check)"),
            ("thinker", "the overnight thinker: status | set <claude|codex|kimi|gemini|local|off> | "
                        "login <claude|kimi|codex|gemini> | probe | check | migrate"),
            ("dream", "the dreams: backfill --plan | --apply --restamp|--backfill|--playbooks | "
                      "--rollback PREIMAGE ; confirm <id> ; show <id> ; watch"),
            ("persona", "the Persona Forge: status [--json] | forge | declare --name NAME "
                        "[--skill FOLDER] [--blurb TEXT] | decline [--undo]")):
        sp = sub.add_parser(_verb, help=_help)
        sp.add_argument("--json", action="store_true", help="structured envelope")
        sp.add_argument("organ_args", nargs=argparse.REMAINDER,
                        help="passed through to the organ unchanged")
        _add_global_flags(sp)
        sp.set_defaults(_fn={"hooks": verb_hooks, "instructions": verb_instructions,
                             "preamble": verb_preamble, "canary": verb_canary,
                             "thinker": verb_thinker, "dream": verb_dream,
                             "persona": verb_persona}[_verb])

    # recall
    sp = sub.add_parser("recall", help="hybrid retrieval over the brain.")
    sp.add_argument("--query", "-q", required=True, help="search query")
    sp.add_argument("--top-k", "--top_k", "-k", dest="top_k", type=int, default=5,
                    help="max results (default 5)")
    # L5-N-beta Explainable Recall pass-through. OFF by default; when set, the
    # engine's render-only "why-surfaced" explainer runs (per-result one-liner to
    # stderr) AND voidloom folds a structured, ANSI-free `explain` list into the
    # --json envelope's data (built from the same sub-scores already on each
    # result — never recomputed). Strictly additive: absent => behaviour and
    # output are byte-identical to a plain recall.
    sp.add_argument("--explain", dest="explain", action="store_true",
                    help="surface per-result 'why-surfaced' provenance "
                         "(L5-N; render-only, additive; default off)")
    # H12: forward the engine's own --no-cross-tier off-switch. Belt-and-
    # suspenders alongside C1's default-OFF gate: when set, the EDDIE enrichment
    # is skipped explicitly (the engine reads `args.no_cross_tier`). Previously
    # `voidloom recall --no-cross-tier` exited 2 (unrecognized) because the
    # wrapper never surfaced the flag.
    sp.add_argument("--no-cross-tier", dest="no_cross_tier",
                    action="store_true", default=False,
                    help="skip cross-tier (twin-brain) enrichment for this recall "
                         "(also honoured via env VOIDLOOM_ENABLE_CROSS_TIER).")
    sp.add_argument("--leads", dest="leads", type=int, default=None,
                    help="side-channel quota (dreams: default 1, 2 at --top-k>=10, 3 with --explore); "
                         "0 turns the dream channel off")
    sp.add_argument("--explore", dest="explore", action="store_true", default=False,
                    help="widen the side channels (dreams: 3)")
    sp.add_argument("--no-rerank", dest="no_rerank", action="store_true", default=False,
                    help="skip the reranker for this run (vector + keyword + RRF). It can only turn the reranker "
                         "OFF: it never runs on CPU-only machines or below 15.0 GiB RAM anyway")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_recall)

    # store
    sp = sub.add_parser("store", help="store a new memory (validated category).")
    sp.add_argument("--content", "-c", required=True, help="memory content")
    sp.add_argument("--category", required=True,
                    help="memory category (validated against the engine enum)")
    sp.add_argument("--tags", "-t", default="", help="comma-separated tags")
    sp.add_argument("--importance", "-i", type=float, default=None,
                    help="importance 0.0-1.0 (default 0.5)")
    sp.add_argument("--answers", default=None,
                    help="comma-separated dream id(s) this recorded memory settles (evidence: +0.10 each, cap 0.65)")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_store)

    # context
    sp = sub.add_parser("context", help="session-start context load.")
    sp.add_argument("--project", "-p", default=None, help="scope ranking")
    sp.add_argument("--limit", "-n", type=int, default=15,
                    help="max memories (default 15)")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_context)

    # batch-recall — N recalls in ONE process (single model load). Input is a
    # JSON array via inline string / @file / - (stdin); elements are query
    # strings or {"query":..,"top_k":..} dicts.
    sp = sub.add_parser("batch-recall",
                        help="N recalls in one process (single model load).")
    sp.add_argument("--items", required=True, metavar="JSON",
                    help="JSON array of queries: an inline string, @file, or - "
                         "(stdin). Each element is a query string or "
                         "{\"query\":..,\"top_k\":..}.")
    sp.add_argument("--top-k", "--top_k", "-k", dest="top_k", type=int, default=5,
                    help="batch-wide max results per query (default 5; a "
                         "per-item top_k overrides it)")
    sp.add_argument("--leads", dest="leads", type=int, default=None,
                    help="side-channel quota per query; 0 turns the dream channel off")
    sp.add_argument("--explore", dest="explore", action="store_true", default=False,
                    help="widen the side channels (dreams: 3)")
    sp.add_argument("--no-rerank", dest="no_rerank", action="store_true", default=False,
                    help="skip the reranker for this run (vector + keyword + RRF). It can only turn the reranker "
                         "OFF: it never runs on CPU-only machines or below 15.0 GiB RAM anyway")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_batch_recall)

    # batch-store — N stores in ONE process (single model load). Input is a JSON
    # array of memory dicts (content+category required; tags/kind/importance/
    # summary/project/context/source_user/auto_edges optional). Prefer --db a
    # temp copy for any batch that must not touch the live brain.
    sp = sub.add_parser("batch-store",
                        help="N stores in one process (single model load).")
    sp.add_argument("--items", required=True, metavar="JSON",
                    help="JSON array of memory dicts: an inline string, @file, "
                         "or - (stdin). Each element needs content+category; "
                         "tags/kind/importance/summary/project/context/"
                         "source_user/auto_edges are optional.")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_batch_store)

    # approvals — the three-faced surface (Face 3 default / Face 2 --present /
    # Face 1 --tui) over the unified self-eval + belief + governance backend.
    sp = sub.add_parser("approvals",
                        help="three-faced approvals queue "
                             "(self-eval+belief+governance).")
    sp.add_argument("--list", action="store_true",
                    help="show the pending queue (default; Face 3 record)")
    sp.add_argument("--present", action="store_true",
                    help="Face 2: VOIDWRIGHT pop-up card + AskUserQuestion payload")
    sp.add_argument("--tui", action="store_true",
                    help="Face 1: interactive TUI (ANSI by default; needs a TTY)")
    sp.add_argument("--face", choices=["ansi", "textual"], default=None,
                    help="TUI face: ansi (default, at parity) or textual (opt-in, "
                         "NOT at parity: single action, no confirm, no derived verdict)")
    sp.add_argument("--show", metavar="REF", default=None,
                    help="show one item's proposals "
                         "(self-eval:<id>|belief:<batch>|governance:<batch>)")
    sp.add_argument("--decide", metavar="REF", default=None,
                    help="UNIT B: ONE sitting, both directions "
                         "(self-eval:<id>; use --yes/--no, repeatable)")
    sp.add_argument("--yes", action="append", default=None, metavar="TITLE",
                    help="--decide: approve this title (repeatable)")
    sp.add_argument("--no", action="append", default=None, metavar="TITLE",
                    dest="no_titles",
                    help="--decide: reject this title (repeatable)")
    sp.add_argument("--approve", metavar="REF", default=None,
                    help="approve+enact REF (confirm-gated; needs --title|--ids)")
    sp.add_argument("--reject", metavar="REF", default=None,
                    help="reject REF (confirm-gated; needs --title|--ids)")
    sp.add_argument("--confirm", action="store_true",
                    help="explicit non-interactive affirmation for an enact "
                         "(without it, a non-TTY/--json enact refuses)")
    sp.add_argument("--comment", default=None,
                    help="free-text recorded as a HIGH-TRUST training signal "
                         "(human-authored; linked to the proposal)")
    sp.add_argument("--mode", nargs="?", const="show", default=None,
                    choices=["show", "auto", "manual"],
                    help="show, or set, whether the brain decides its own monthly "
                         "self-eval proposals (auto) or waits for you (manual)")
    sp.add_argument("--snooze", type=float, default=None, metavar="HOURS",
                    help="suppress the calm ping for HOURS (<=0 clears it)")
    sp.add_argument("--title", action="append", default=None, metavar="TITLE",
                    help="self-eval proposal title (repeatable)")
    sp.add_argument("--ids", nargs="+", type=int, default=None, metavar="ID",
                    help="belief / governance proposal ids")
    sp.add_argument("--reason", default=None,
                    help="reason recorded with a rejection")
    sp.add_argument("--json", action="store_true", help="structured envelope")
    _add_global_flags(sp)
    sp.set_defaults(_fn=verb_approvals)

    return p


def dispatch(argv: Optional[Sequence[str]] = None) -> int:
    """Parse argv, run the verb, emit the envelope, return the exit code.

    Top-level fail-graceful: any unexpected exception becomes an ``error``
    envelope (never a stacktrace into the caller)."""
    parser = build_parser()
    # The organ verbs own their grammar (`voidloom hooks --status --json`), so
    # unknown options must reach them in order rather than exit 2 here.  For
    # every other verb an unknown option is the usage error it always was.
    args, extras = parser.parse_known_args(argv)
    if getattr(args, "command", None) in ORGAN_VERBS:
        args.organ_args = list(extras) + list(getattr(args, "organ_args", None) or [])
        if "--json" in args.organ_args:
            args.json = True
    elif getattr(args, "command", None) == "preflight":
        args.rest = list(extras) + list(getattr(args, "rest", None) or [])
    elif extras:
        parser.error("unrecognized arguments: " + " ".join(extras))
    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    # `--db` is declared on the top-level parser; argparse stores it on the same
    # namespace, so verbs read it via getattr(args, "db", None). Ensure presence.
    # (The per-verb mirror uses argparse.SUPPRESS, so a post-verb `--db`/`--quiet`
    # overwrites the namespace attr while an absent one leaves the top-level value
    # intact — both positions resolve to the same attr.)
    if not hasattr(args, "db"):
        args.db = None

    as_json = bool(getattr(args, "json", False))
    quiet = bool(getattr(args, "quiet", False))
    fn = getattr(args, "_fn", None)
    if fn is None:
        return _emit(error("no_verb", "no verb resolved"), as_json)

    # H3 --quiet: force FULL stderr silence for the duration of the verb (this is
    # where the engine emits its model-load INFO/WARNING + the SentenceTransformers
    # version warning). The stdout envelope is written AFTER, outside the silence,
    # so the machine/JSON contract is wholly untouched. Restored unconditionally
    # in `finally`, even on exception, so we never leave stderr swallowed.
    with _maybe_silence_stderr(quiet):
        try:
            env = fn(args)
        except SystemExit:
            raise
        except Exception as e:  # pragma: no cover - top-level safety net
            env = error("unhandled", f"unexpected failure: {e}",
                        verb=getattr(args, "command", None))
    return _emit(env, as_json)


@contextlib.contextmanager
def _maybe_silence_stderr(quiet: bool):
    """Redirect ``sys.stderr`` to ``os.devnull`` for the block when ``quiet`` is
    True (the H3 ``--quiet`` full-silence path), else a no-op. Always restores the
    real stderr in ``finally``. Uses an OS-level fd redirect so the model-load
    noise emitted by C/torch native code (which writes to fd 2 directly, bypassing
    Python's ``sys.stderr`` object) is silenced too — a pure ``sys.stderr`` swap
    would miss it."""
    if not quiet:
        yield
        return
    saved_fd = None
    devnull_fd = None
    saved_obj = sys.stderr
    try:
        try:
            saved_fd = os.dup(2)
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull_fd, 2)
        except Exception:
            saved_fd = None  # fd redirect unavailable; fall back to object swap
        try:
            sys.stderr = open(os.devnull, "w")
        except Exception:
            sys.stderr = saved_obj
        yield
    finally:
        try:
            if sys.stderr is not saved_obj:
                try:
                    sys.stderr.close()
                except Exception:
                    pass
            sys.stderr = saved_obj
        except Exception:
            pass
        try:
            if saved_fd is not None:
                os.dup2(saved_fd, 2)
                os.close(saved_fd)
            if devnull_fd is not None:
                os.close(devnull_fd)
        except Exception:
            pass


def _make_output_encodable() -> None:
    """Never let a decorative character become an empty stdout and exit 1.

    CRUCIBLE-3 U4 (accessibility seat, Windows 11, Turkish OEM console).  A SINGLE
    U+2014 em-dash in a notes string made ``voidloom bootstrap`` die with an
    unhandled ``UnicodeEncodeError`` and print NOTHING on cp437 / cp850 / cp857 --
    cp857 being the Turkish OEM console codepage.  Reproduced on Linux, no Windows
    needed:  ``PYTHONIOENCODING=cp857 python3 -m voidloom bootstrap``  ->  rc 1.

    The fix is the STREAM, not the dash.  Editing the sixteen em-dashes out of the
    strings leaves the seventeenth unborn-but-inevitable, and the next author has
    no way to know the rule exists.  Here, a character the console cannot encode
    degrades to a replacement glyph and the command still does its job and still
    says what it did.  On a UTF-8 console -- every console the developers use --
    this changes nothing at all, which is precisely why the defect survived to a
    fleet seat: it is invisible on the machines that ship the code.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")   # py3.7+
        except (AttributeError, ValueError, OSError):
            pass            # a replaced/wrapped stream: leave it exactly as found


def main(argv: Optional[Sequence[str]] = None) -> int:
    _make_output_encodable()
    return dispatch(argv)


if __name__ == "__main__":
    raise SystemExit(main())
