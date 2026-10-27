# voidloom — the LLM's memory toolkit (v0.1)

`voidloom` is a thin CLI **facade** over the Voidloom memory brain engine
(`claude_memory.py`). It is the "Adaptive Loom" execution surface: the
toolkit a fresh LLM discovers and uses to recall, store, load session context,
and shepherd the unified approval queue — without ever re-implementing or
touching the canonical engine.

> **It is a local CLI and session hook — not a hosted API or SDK.** Every verb
> runs on your own machine against a local SQLite brain; nothing is sent to a
> server.

Licensed under the Apache License, Version 2.0 — see [`../LICENSE`](../LICENSE)
and [`../NOTICE`](../NOTICE). Copyright 2026 Andrew Grainger d/b/a Voidwright.

> **One engine. One facade.** Deleting the `voidloom/` package leaves
> `claude_memory.py` byte-for-byte untouched. Clean rollback by construction.

## Why it exists (design spine — council-ruled)

| Decision | What it means | Ref |
|---|---|---|
| **Facade, not fork** | Imports `claude_memory` and calls its `cmd_*` entry points. Never shells out, never duplicates engine logic. | #6042, #6047 |
| **Lazy heavy imports** | `bootstrap` / `agent-context` / `doctor` (happy path) are **stdlib-pure** — no torch/jina. Heavy modules load only inside `recall` / `store`. This is what lets *install* differ from *onboard*. | #6042 |
| **Self-describing** | `voidloom agent-context` emits a compact (~960-token) machine-readable manifest so a fresh LLM discovers the toolkit. Modeled on Cloudflare's `cf agent-context`. | #6044, #1399 |
| **Locality-as-auth** | Ungated locally **because** gated on the local filesystem. The local-FS guard *is* the auth model — core behaviour, not deferred security. | #6105 |
| **Structured envelopes** | Every verb emits `{status: ok|error|refused, code, data}`. Assert on `status` + `code`, never on substring error text. | #6042 |

## Install (no pip required — runs in place)

The package runs straight from the source tree alongside the brain. Two ways:

```bash
# A) via the shell shim (symlink onto PATH)
ln -s /abs/path/to/voidloom/voidloom ~/.local/bin/voidloom
voidloom bootstrap

# B) via the module (put the package PARENT dir on PYTHONPATH)
PYTHONPATH=/abs/path/to/<dir-containing-voidloom> python3 -m voidloom bootstrap
```

There is **nothing to build**. `bootstrap` works on a bare host with no models
and no DB.

## Verbs — all 14, and this list is checked

`voidloom/tests/test_docs_name_real_paths.py` compares this section against
`voidloom --help` and fails when they diverge. It had to: until 2026-09-16 this
page documented **7 of 14** while `--version` still read `0.1.0`, so the shipped
guide for the shipped version omitted half of that version's surface — including
`hooks`, which the onboarding guide calls *"the ONLY thing that wires hooks"*, and
`batch-recall`/`batch-store`, which the product's own injected preamble calls
*"the DEFAULT"* interface. (The machine-facing path was never wrong:
`agent-context` has always emitted all 14. A human was the one left short.)

### Discovery + health

```
voidloom bootstrap      [--json]   stdlib-only host detect (OS/WSL2, GPU, brain+models); reports tier
voidloom agent-context  [--json]   emit the self-describing toolkit manifest (LLM discovery; all verbs)
voidloom capabilities   [--json]   full prose + examples view of the brain's L5 organs
voidloom doctor         [--json]   health: DB, local-FS guard, engine, models, and the HOOK JOIN
                                   (NOT_INSTALLED / INSTALLED_NEVER_FIRED / DEAD / LIVE per platform)
                                   and the reranker: on or off on this machine, and why
voidloom preflight      [...]      prove THIS machine in a scratch folder (installs nothing); writes
                                   preflight.json -- every argument goes to install/preflight.py
```

### Memory

```
voidloom recall   --query Q [--top-k N] [--no-rerank] [--json]   hybrid retrieval over the brain
voidloom store    --content C --category CAT [--tags T] [--importance N] [--json]   store a memory
voidloom context  [--project P] [--limit N] [--json]        session-start context load
voidloom batch-recall --items '["q1","q2"]' [--top-k N] [--no-rerank] [--json]   N recalls, ONE model load
voidloom batch-store  --items '[{"content":"...","category":"..."}]' [--json]   N stores, ONE model load
```

**Batch is the default for more than one operation.** The single verbs reload the
embedding model on every call; the batch verbs load it once. `--items` takes inline
JSON, `@file`, or `-` for stdin.

### Integration

```
voidloom hooks         install --platform {claude,codex} [--dry-run|--yes] | status
                       | uninstall [--yes] | --self-check
voidloom instructions  install [--dry-run|--yes] | status | uninstall [--yes]
voidloom preamble      [--json] [--budget N] [--sections ...] [--selftest]
                       THE single emitter of the injected session preamble
```

### Queue + proof

```
voidloom approvals  [--list] [--show REF] [--approve REF --title T | --ids ID...]
                    [--reject REF ...] [--json]      three-faced queue (self-eval + belief + governance)
voidloom canary     [--gate G] [--runner R] [--self-check]   G0/G1/G2/G3 proof harness, control first
voidloom thinker    status | set <claude|codex|kimi|gemini|local|off> | login <claude|kimi|codex|gemini> | probe | check | migrate
                    who thinks overnight: the owner's answer, proven in the timers' environment, then recorded
voidloom dream      backfill --plan | --apply --restamp|--backfill|--playbooks [--preimage P] | --rollback P
                    confirm <id> (the owner, at a terminal) | show <id> | watch [--window-days N]
                    the brain's dreams: born at 0.40, faded at read time, raised only by evidence
voidloom persona    status [--json] | forge | declare --name NAME [--skill FOLDER] [--blurb T] | decline [--undo]
                    the Persona Forge: is there a persona yet (forged | declared-missing-skill | declined | absent)
```

`recall` and `batch-recall` return memories in `results` (per query in batch-recall) and, beside them, a `dreams`
list: unverified leads the brain synthesised, each framed `[dream — ... not a recorded fact]`, at most 1 (2 at
`--top-k` >= 10, 3 with `--explore`); `--leads 0` turns the channel off. A dream never takes a memory's place.
A dream shows only on its OWN relevance: its cosine to the query must reach the one absolute floor (0.50, the
registry's `COSINE_FLOOR`), never by out-scoring a memory. To settle a lead, store what you found with
`voidloom store --content "..." --category insight --answers <dream_id>` (or an `answers` field in a `batch-store`
item): evidence raises the dream by 0.10, capped at 0.65. `voidloom doctor` reports the dreams line with the 7-day
shown-rate and the corpus. On Tier 0 (no embedding model) dreams are still stored and kept out of `results`, but no
`dreams` channel is served: the channel's floors are cosine floors. A Tier 0 channel (FTS-only, with its own
calibrated floor) is later work.

`--db PATH` (top-level) overrides the brain DB path; it otherwise resolves from
`WIZARD_LIBRARY_DB` / `CLAUDE_MEMORY_DB` / the canonical
`$HOME/.claude/memory/claude_memory.db`.

### The envelope

Every verb emits the same shape:

```json
{ "status": "ok",       "code": "recall_ok",          "data": { ... }, "verb": "recall" }
{ "status": "error",    "code": "invalid_category",   "data": { "message": "...", "detail": {...} } }
{ "status": "refused",  "code": "non_local_db",       "data": { "message": "...", "detail": {...} } }
```

Process exit codes: `ok → 0`, `error → 1`, `refused → 3` (a distinct code so a
caller can tell *blocked by policy* from *failed*).

### `store` categories

The `--category` value is validated against the engine's canonical enum (read
live, so it can never drift):

```
user_preference  technical_decision  project_knowledge  error_pattern
skill_lesson     workflow_pattern     people_context     tool_optimization
domain_knowledge campaign_state       correction         insight
```

A bad category is refused **before** the embedding model loads.

### `approvals` — the enact gate

`--list` / `--show` are read-only. `--approve` / `--reject` are:
- **ref-regex validated** before any backend call — refs must match
  `self-eval:<id>` or `belief:<batch>` (e.g. `self-eval:7`,
  `belief:belief-sweep-2026-06-10T18:04:00`);
- **confirm-gated** — an interactive `y/N` prompt. Under `--json` or a non-TTY
  stdin, an enact is **refused** (`confirm_required_no_tty`) rather than run
  unattended.

The backend (`voidloom_approvals.py`) is the existing unified surface; voidloom
is a thin, validated passthrough to it.

## The local-FS guard (auth model)

The guard resolves the DB path and **refuses** (`non_local_db`) if it lives on a
UNC / network-authority path (`\\host\share`, `//host/share`), under a known
synced-cloud root (Dropbox, OneDrive, iCloud, Google Drive, …), or on a network
filesystem (NFS/CIFS/sshfs/…). It also adds a **read-only degrade**: reads work,
writes refuse gracefully (`readonly_db`) if the DB is read-only. A synced or
networked brain is not a sound local-auth boundary.

## What is deferred (final security audit #176)

These are **explicitly not built** in v0.1 and carry marked hooks:
- pre-store **secret-detector** (`# SECURITY-HOOK(#176)` in `cli.verb_store`),
- the enact **audit-log row**,
- terminal output **sanitizer**,
- supply-chain **pinning / signing**.

The structural floor *is* built: local-FS guard, read-only degrade, approvals
ref-regex, category enum validation, `--json` enum envelopes, fail-graceful
everywhere.

## Tests

```bash
PYTHONPATH=/abs/path/to/<dir-containing-voidloom> python3 -m unittest voidloom.tests.test_voidloom -v
```

37 GPU-free tests. Write-path tests copy the live DB to `/tmp` first and assert
the live DB md5 is untouched. Heavy `recall`/`store` tests run live when the
embedding model is cached and **skip** otherwise (never a multi-GB download in
CI). The whole suite runs from the repository root with `python3 -m pytest voidloom/tests/ -q`
(see [`../CONTRIBUTING.md`](../CONTRIBUTING.md)).

## Layout

```
voidloom/
  __init__.py          version + stdlib-pure package root
  __main__.py          enables `python3 -m voidloom`
  cli.py               argparse dispatch + the --json envelope writer + verbs
  _facade.py           lazy import of claude_memory; wraps cmd_* (capture-stdout)
  _bootstrap.py        STDLIB-ONLY host detect (never lspci; nvidia-smi best-effort)
  _guard.py            the local-FS guard + read-only degrade (the auth model)
  _agent_context.py    the toolkit manifest data + renderer
  voidloom             shell shim (runs `python3 -m voidloom` in place)
  tests/test_voidloom.py
```

## Licence

Apache License, Version 2.0 — see [`../LICENSE`](../LICENSE) and [`../NOTICE`](../NOTICE).
Copyright 2026 Andrew Grainger d/b/a Voidwright.
