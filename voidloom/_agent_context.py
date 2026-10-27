"""_agent_context.py — the self-describing toolkit manifest.

Modeled on Cloudflare's ``cf agent-context`` (memory #1399): a compact,
machine-readable description of every verb the toolkit exposes, designed to be
dropped into a fresh LLM's context so it can discover and drive the toolkit with
zero prior knowledge. Target budget: well under ~1K tokens.

Each verb entry carries:
  * ``name``    — the verb.
  * ``purpose`` — one line on what it does.
  * ``args``    — the flags it accepts, each with a one-word/short type + note.
  * ``when``    — a <=10-word trigger telling the LLM *when to reach for it*.

This module is stdlib-only and imports nothing heavy — discovery must work on a
bare host before any model is pulled.
"""
from __future__ import annotations

from typing import Any, Dict, List

# The single source of truth for the toolkit's shape. cli.py builds its argparse
# from intent that MUST stay consistent with this manifest (a test asserts every
# verb here is dispatchable, and vice-versa).
TOOLKIT: Dict[str, Any] = {
    "toolkit": "voidloom",
    "version": "1.0.0",
    "summary": (
        "LLM execution surface over the local wizard-library memory brain; a thin "
        "facade over claude_memory.py. Every verb takes --json. Ungated locally; "
        "gated on the local filesystem (locality-as-auth)."
    ),
    "envelope": {
        "status": ["ok", "error", "refused"],
        "code": "machine-readable, e.g. non_local_db / invalid_category",
        "data": "the verb payload (or refusal/error detail)",
        "note": "Assert on status + code, never substring error text.",
    },
    "verbs": [
        {
            "name": "bootstrap",
            "purpose": "Stdlib-only host detect: OS/WSL2, GPU, brain+model presence; reports tier.",
            "args": {
                "--json": "flag; structured envelope",
            },
            "when": "First run on a new host; check readiness.",
        },
        {
            "name": "agent-context",
            "purpose": "Emit this self-describing toolkit manifest for LLM discovery.",
            "args": {
                "--json": "flag; structured envelope (the manifest as JSON)",
            },
            "when": "A fresh LLM needs to learn the toolkit.",
        },
        {
            "name": "capabilities",
            "purpose": ("Full prose+examples view of the brain's L5 organs "
                        "(recall --explain, approvals, governance, communities, "
                        "the quickening, trust tier). Reads the manifest organ "
                        "registry — the same SSOT the session-start hook reads "
                        "compactly."),
            "args": {
                "--available-only": "flag; only organs available on this host's tier",
                "--json": "flag; structured envelope (the organ registry as JSON)",
            },
            "when": "List the brain's grown organs in full.",
        },
        {
            "name": "doctor",
            "purpose": ("Health check: DB reachable, local-FS guard verdict, brain "
                        "importable, models (best-effort), AND the hook join per "
                        "platform: NOT_INSTALLED / INSTALLED_NEVER_FIRED (with the "
                        "approval step) / DEAD (n sessions the hook did not fire in) / "
                        "LIVE — heartbeat session_ids joined against the platform's "
                        "own session records; Codex trust from its persisted record, or "
                        "live from hooks/list under --probe-platform."),
            "args": {
                "--platform": "str (optional); claude-code | codex — restrict the join",
                "--cwd": "str (optional); cwd for the platform probes",
                "--probe-platform": ("flag; CONSENT to spawn `codex app-server` for the live "
                                     "hooks/list view (the vendor writes under $CODEX_HOME -- "
                                     "stated on stderr BEFORE the spawn, naming the prefix); "
                                     "without it nothing under $HOME changes"),
                "--json": "flag; structured envelope",
            },
            "when": "Verify the install; did the hook fire? Reranker on?",
        },
        {
            "name": "preflight",
            "purpose": ("Prove THIS machine before a package is cut for it: venv, torch, sqlite-vec and golden "
                        "vectors on its accelerator, in a scratch folder that is then deleted; writes "
                        "preflight.json to send back. Installs nothing into the home. Execs "
                        "install/preflight.py, which owns stdout and the exit code (0 GREEN, 1 RED, 2 could "
                        "not run)."),
            "args": {
                "...": "every token after the verb is passed to install/preflight.py (--out, --python, --tier, "
                       "--keep, --use-python, --help)",
            },
            "when": "Before handing Voidloom to a new machine (decision D1).",
        },
        {
            "name": "hooks",
            "purpose": ("The SessionStart hook, per platform: `install --platform "
                        "{claude,codex}` (dry-run by default; --yes writes ONE "
                        "array element under a losslessness gate, with a byte "
                        "pre-image, a post-image sha and an element-level undo), "
                        "`status` (the platform's own resolver view + unreceipted "
                        "markers), `uninstall [--yes]`, `--self-check`."),
            "args": {
                "install": "word; then --platform {claude,codex} [--dry-run|--yes] [--target PATH]",
                "status": ("word; the resolver's view. Codex registration from the hook schema, "
                           "trust from Codex's persisted record for OUR key (a record for another "
                           "hooks.json path is reported FOREIGN under stale_trust_state_keys); add "
                           "--probe-platform to CONSENT to the live app-server hooks/list view (the "
                           "vendor then writes under $CODEX_HOME; stated on stderr before the spawn)"),
                "uninstall": ("word; [--yes] undoes ONLY hook mechanisms; element-level when the "
                              "file changed since install; refuses a look-alike element that is "
                              "not the receipt's"),
                "--self-check": "flag; the organ's own invariants",
                "--json": "flag; structured envelope (exit 0 ok / 3 refused / 4 incomplete)",
            },
            "when": "Install, inspect or undo the memory hook.",
        },
        {
            "name": "instructions",
            "purpose": ("The trust-free activation notice in the instruction files "
                        "(~/.agents/AGENTS.md canonical; $CODEX_HOME/AGENTS.md inline; "
                        "~/.claude/CLAUDE.md import line): `install [--dry-run|--yes]` "
                        "(dry-run names every path and which tools read it), "
                        "`status`, `uninstall [--yes]` (undoes ONLY instruction mechanisms). "
                        "A platform whose home is absent is SKIPPED, never a blocker. Refuses "
                        "any target that is not a regular file with exactly one name."),
            "args": {
                "install": "word; [--dry-run|--yes]",
                "status": "word; per-target marker presence + receipt",
                "uninstall": "word; [--yes] marker-block removal when the file changed",
                "--json": "flag; structured envelope",
            },
            "when": "Install or undo the trust-free activation notice.",
        },
        {
            "name": "preamble",
            "purpose": ("THE single emitter of the injected session preamble; the "
                        "hook's additionalContext is these bytes by construction."),
            "args": {
                "--json": "flag; structured envelope (text + segment table)",
                "--budget": "int; degrade by volatility in a published order",
                "--sections": "flag; the segment table only",
                "--selftest": "flag; the emitter's invariants (--work-db COPY required)",
            },
            "when": "Inspect what the session-start hook injects.",
        },
        {
            "name": "canary",
            "purpose": ("G0/G1/G2/G3 proof harness — did the context ARRIVE at the "
                        "model? Control runs first and is mandatory; --self-check."),
            "args": {
                "--gate": "str; GB | G0 | G1 | G2 | G3",
                "--platform": "str; platform id (default codex)",
                "--runner": "str; shell command that answers a prompt on stdin",
                "--self-check": "flag; the harness's own invariants",
                "--json": "flag; structured envelope",
            },
            "when": "Prove context delivery on a platform.",
        },
        {
            "name": "thinker",
            "purpose": ("WHO thinks overnight (the owner's recorded, proven choice): status; set "
                        "<claude|codex|kimi|gemini|local|off> proves in the timers' env then records; login asks a "
                        "secret twice (never pass one on argv); probe; check; migrate."),
            "args": {
                "status": "subverb; backend, proven_at, credential fingerprint + age, nights",
                "set": "subverb; <claude|codex|kimi|gemini|local|off> [--model] [--endpoint] [--command] [--source]",
                "login": "subverb; <claude|kimi|codex|gemini> -- a HUMAN pastes or approves at a terminal",
                "--json": "flag; structured envelope",
            },
            "when": "Ask the human which model thinks; never choose.",
        },
        {
            "name": "dream",
            "purpose": ("The brain's dreams: backfill --plan (read-only counts) | --apply --restamp|--backfill|"
                        "--playbooks (pre-image first) | --rollback PREIMAGE; confirm <id> (the OWNER's word, "
                        "terminal only); show <id> (full text, framed); watch (the dream row invariant)."),
            "args": {
                "backfill": "subverb; --plan | --apply --restamp|--backfill|--playbooks [--preimage P] | --rollback P",
                "confirm": "subverb; <dream_id> -- a HUMAN at a terminal; an agent cannot confirm",
                "show": "subverb; <dream_id>",
                "watch": "subverb; [--window-days N]",
                "--json": "flag; structured envelope",
            },
            "when": "Read a dream in full; never confirm one yourself.",
        },
        {
            "name": "persona",
            "purpose": ("The Persona Forge: is there a persona yet? status -> forged | declared-missing-skill | "
                        "declined | absent; declare merges the human's chosen persona into persona.json (backup "
                        "first); decline [--undo] stops / restores the session-start reminder."),
            "args": {
                "status": "subverb; [--json]",
                "forge": "subverb; how to start it (/persona-forge in Claude Code)",
                "declare": "subverb; --name NAME [--skill FOLDER] [--blurb TEXT] [--no-default]",
                "decline": "subverb; [--undo]",
                "--json": "flag; structured envelope",
            },
            "when": "No persona yet: offer /persona-forge; declare only approved names.",
        },
        {
            "name": "recall",
            "purpose": ("Hybrid retrieval (FTS5 + vector + RRF + rerank) over the brain. `results` holds memories "
                        "only; unverified dream leads ride beside them in `dreams` (framed, never facts)."),
            "args": {
                "--query": "str (required); the search query",
                "--top-k": "int (default 5); max results",
                "--leads": "int; side-channel quota (dreams 1, 2 at top-k>=10, 3 with --explore); 0 = off",
                "--explore": "flag; widen the side channels",
                "--no-rerank": ("flag; skip the reranker this run (it never runs on CPU-only machines or below "
                                "15.0 GiB RAM anyway -- the reranker gate)"),
                "--json": "flag; structured envelope",
            },
            "when": "Before deciding/answering; pull relevant memories.",
        },
        {
            "name": "store",
            "purpose": "Store a new memory (validated category enum) into the brain.",
            "args": {
                "--content": "str (required); the memory text",
                "--category": ("str (required); one of: user_preference, "
                               "technical_decision, project_knowledge, "
                               "error_pattern, skill_lesson, workflow_pattern, "
                               "people_context, tool_optimization, "
                               "domain_knowledge, campaign_state, correction, "
                               "insight"),
                "--tags": "str (optional); comma-separated",
                "--importance": "float 0.0-1.0 (default 0.5)",
                "--json": "flag; structured envelope",
            },
            "when": "A durable fact/decision/lesson worth keeping appears.",
        },
        {
            "name": "context",
            "purpose": "Session-start context load: top memories for a project + brain stats.",
            "args": {
                "--project": "str (optional); scope relevance ranking",
                "--limit": "int (default 15); max memories",
                "--json": "flag; structured envelope",
            },
            "when": "At session start; prime working context.",
        },
        {
            "name": "batch-recall",
            "purpose": ("N recalls in ONE process: a single model load for the "
                        "whole batch (the embedder/reranker are process-cached, "
                        "so looping is N warm embeds behind one load). Per-item "
                        "isolation: one bad query errors in place, batch "
                        "continues."),
            "args": {
                "--items": ("JSON array; an inline string, @file, or - (stdin). "
                            "Each element is a query string or "
                            "{\"query\":..,\"top_k\":..}"),
                "--top-k": "int (default 5); batch-wide default (per-item overrides)",
                "--no-rerank": "flag; skip the reranker for every query in the batch",
                "--json": "flag; structured envelope {count,succeeded,failed,results}",
            },
            "when": "Many queries at once; avoid N model reloads.",
        },
        {
            "name": "batch-store",
            "purpose": ("N stores in ONE process: a single model load for the "
                        "whole batch. Per-item isolation: one bad memory errors "
                        "in place, batch continues. Drives the full store path "
                        "(kind / auto_edges / source_user / summary)."),
            "args": {
                "--items": ("JSON array; an inline string, @file, or - (stdin). "
                            "Each element is a memory dict: content+category "
                            "required; tags/kind/importance/summary/project/"
                            "context/source_user/auto_edges optional"),
                "--json": "flag; structured envelope {count,succeeded,failed,results}",
            },
            "when": "Many memories at once; avoid N model reloads.",
        },
        {
            "name": "approvals",
            "purpose": ("Three-faced approval queue (self-eval + belief + "
                        "governance). Face 3 JSON record (default) | Face 2 "
                        "--present pop-up card + AskUserQuestion payload | Face 1 "
                        "--tui. A --comment is a high-trust training signal; "
                        "every enact writes an audit row."),
            "args": {
                "--list": "flag; the pending queue as a structured record (default)",
                "--present": "flag; Face 2 — VOIDWRIGHT card + AskUserQuestion payload",
                "--tui": "flag; Face 1 — interactive TUI (textual/ANSI; needs a TTY)",
                "--show": "REF; show one item's proposals (read-only)",
                "--approve": "REF; approve+enact (confirm-gated; needs --title or --ids)",
                "--reject": "REF; reject (needs --title or --ids)",
                "--confirm": "flag; explicit non-interactive affirmation for an enact",
                "--comment": "str; recorded as a HIGH-TRUST training signal (linked)",
                "--snooze": "float HOURS; suppress the calm ping (<=0 clears)",
                "--title": "str (repeatable); self-eval proposal title",
                "--ids": "int+ ; belief / governance proposal ids",
                "--reason": "str; recorded with a rejection",
                "--json": "flag; structured envelope (a non-TTY enact needs --confirm)",
                "ref-shape": "self-eval:<int> | belief:<batch-id> | governance:<batch-id>",
            },
            "when": "Review/decide on the brain's pending proposals.",
        },
    ],
}


def manifest() -> Dict[str, Any]:
    """Return the toolkit manifest (a fresh dict copy is unnecessary — callers
    only read it; the structure is module-constant)."""
    return TOOLKIT


def verb_names() -> List[str]:
    return [v["name"] for v in TOOLKIT["verbs"]]


def render_text(m: Dict[str, Any]) -> str:
    """A compact human rendering of the manifest (the non-``--json`` view)."""
    lines: List[str] = []
    lines.append(f"{m['toolkit']} v{m['version']} — the LLM's memory toolkit")
    lines.append("=" * 52)
    lines.append(m["summary"])
    lines.append("")
    env = m["envelope"]
    lines.append(f"Every verb: --json -> {{status: {'|'.join(env['status'])}, "
                 f"code, data}}. {env['note']}")
    lines.append("")
    lines.append("VERBS")
    for v in m["verbs"]:
        lines.append(f"  voidloom {v['name']}")
        lines.append(f"      {v['purpose']}")
        lines.append(f"      when: {v['when']}")
        arg_bits = []
        for flag, note in v["args"].items():
            # Keep the human view tight — first clause of each note.
            short = str(note).split(";")[0]
            arg_bits.append(f"{flag} ({short})")
        # Wrap args onto a continuation line for readability.
        lines.append("      args: " + ", ".join(arg_bits))
    lines.append("")
    lines.append("Discovery contract: this manifest is stable + machine-readable "
                 "(`voidloom agent-context --json`). Build calls against it.")
    return "\n".join(lines)
